"""
Avaliação do motor de matching contra um Golden Dataset.

Roda o mesmo caminho da produção (`app.matching.match_complaint`) sobre os produtos e
reclamações congelados no conjunto, compara com o parecer humano e reporta a medida.
Não lê banco nem rede: entra arquivo, sai número.

**Convenção.** Positivo é o motor ter **confirmado**. `possible` não é afirmação, é
encaminhamento para revisão — por isso conta na fila (`review`) e não em TP/FP. Par que o
revisor deixou em dúvida (`humano = possible`) fica fora de TP/FP/FN/TN: não é acerto nem
erro, e transformá-lo em um dos dois seria escolher o que dá o número melhor.

**O que o número significa** depende do `split` do conjunto (ver `app.golden`): sobre
`development` isto mede regressão, porque é o corpus que originou as regras; só
`holdout` mede desempenho. O relatório diz qual dos dois está sendo medido, sempre.

**Amostra insuficiente não vira métrica.** Abaixo de `MIN_SUPPORT` pares decididos ou
`MIN_POSITIVES` positivos humanos, a fatia reporta as contagens e recusa precisão,
recall e F1 — um recorte de meia dúzia de pares produz número que parece medida e é
ruído. Pelo mesmo motivo, precisão sem nenhuma confirmação do motor é `None`, não 0,0.

Uso:

    python -m app.evaluation                      # todos os conjuntos, motor atual
    python -m app.evaluation --matcher det-2
    python -m app.evaluation --baseline det-2     # compara duas versões do motor
    python -m app.evaluation golden/x.json --json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from app.golden import GOLDEN_DIR, GoldenDataset, load, load_all
from app.matching import (
    DET2,
    MATCHING,
    MatchCandidate,
    MatchingConfig,
    MatchStatus,
    build_catalog,
    decide,
    match_complaint,
)
from app.models import Product

Pair = tuple[int, int]

# Versões do motor executáveis lado a lado. Comparar versões sobre o mesmo gabarito é o
# objetivo do conjunto; guardar código antigo em paralelo seria a alternativa ruim.
ENGINES: dict[str, MatchingConfig] = {MATCHING.version: MATCHING, DET2.version: DET2}

# Decisões do motor que não são `MatchStatus`.
NO_CANDIDATE = "sem candidato"
SUPPRESSED = "suprimido"

# Pisos de amostra para reportar precisão/recall/F1 em uma fatia. Com menos de 30 pares
# decididos, um único par move a precisão em mais de três pontos; com menos de cinco
# positivos humanos, o recall é a média de meia dúzia de casos.
MIN_SUPPORT = 30
MIN_POSITIVES = 5


@dataclass(frozen=True)
class Outcome:
    """O que o motor decidiu sobre um par, e com que evidência."""

    decision: str
    candidate: MatchCandidate | None = None

    @property
    def suppressed_by(self) -> str | None:
        return self.candidate.suppressed_by if self.candidate is not None else None


def run_engine(dataset: GoldenDataset, config: MatchingConfig) -> dict[Pair, Outcome]:
    """Executa o motor sobre o conjunto inteiro, em memória.

    O escopo é o catálogo inteiro do conjunto, como em produção: a arbitragem compara
    candidatos de produtos diferentes, e avaliar produto a produto daria outro resultado.
    """
    catalog = build_catalog(dataset.produtos)
    outcomes: dict[Pair, Outcome] = {}
    for complaint in dataset.reclamacoes:
        for product, candidate in match_complaint(
            complaint, dataset.produtos, catalog, config
        ):
            pair = (product.id, complaint.id)
            if candidate.suppressed_by is not None:
                outcomes[pair] = Outcome(SUPPRESSED, candidate)
            else:
                outcomes[pair] = Outcome(str(decide(candidate, config)), candidate)
    return outcomes


@dataclass(frozen=True)
class Counts:
    """Os pares de cada classe.

    Guarda quais, não só quantos: uma versão do motor que troca um acerto por outro tem a
    mesma contagem e não fez a mesma coisa.
    """

    tp: frozenset[Pair] = frozenset()
    fp: frozenset[Pair] = frozenset()
    fn: frozenset[Pair] = frozenset()
    tn: frozenset[Pair] = frozenset()
    # Pares que o motor mandou para revisão humana: medem custo, não acerto.
    review: frozenset[Pair] = frozenset()
    # O revisor ficou em dúvida: deliberadamente fora de TP/FP/FN/TN.
    undecided: frozenset[Pair] = frozenset()

    @property
    def support(self) -> int:
        """Pares com parecer humano definido — o denominador de tudo aqui."""
        return len(self.tp) + len(self.fp) + len(self.fn) + len(self.tn)

    @property
    def positives(self) -> int:
        return len(self.tp) + len(self.fn)

    @property
    def sufficient(self) -> bool:
        return self.support >= MIN_SUPPORT and self.positives >= MIN_POSITIVES

    @property
    def precision(self) -> float | None:
        """`None` quando o motor não confirmou nada: 0/0 não é 0,0."""
        confirmados = len(self.tp) + len(self.fp)
        return len(self.tp) / confirmados if confirmados else None

    @property
    def recall(self) -> float | None:
        return len(self.tp) / self.positives if self.positives else None

    @property
    def f1(self) -> float | None:
        precision, recall = self.precision, self.recall
        if precision is None or recall is None:
            return None
        # Precisão ou recall zerados dão F1 zero — que é medida, não ausência dela.
        total = precision + recall
        return 2 * precision * recall / total if total else 0.0

    def summary(self) -> dict[str, object]:
        """Contagens e métricas em forma serializável.

        Precisão, recall e F1 saem como `None` quando a amostra não os sustenta: o
        relatório em JSON não deve afirmar o que o relatório em texto recusa.
        """
        return {
            "TP": len(self.tp),
            "FP": len(self.fp),
            "FN": len(self.fn),
            "TN": len(self.tn),
            "revisao": len(self.review),
            "duvida_humana": len(self.undecided),
            "amostra": self.support,
            "amostra_suficiente": self.sufficient,
            "precision": self.precision if self.sufficient else None,
            "recall": self.recall if self.sufficient else None,
            "f1": self.f1 if self.sufficient else None,
        }


def count(
    outcomes: dict[Pair, Outcome], labels: dict[Pair, MatchStatus], pairs: Iterable[Pair]
) -> Counts:
    """Classifica os pares indicados. Ver a convenção no cabeçalho do módulo."""
    classes: dict[str, set[Pair]] = {
        name: set() for name in ("tp", "fp", "fn", "tn", "review", "undecided")
    }
    for pair in pairs:
        humano = labels[pair]
        decisao = outcomes.get(pair, Outcome(NO_CANDIDATE)).decision
        if decisao == str(MatchStatus.POSSIBLE):
            classes["review"].add(pair)
        if humano is MatchStatus.POSSIBLE:
            classes["undecided"].add(pair)
            continue
        confirmou = decisao == str(MatchStatus.CONFIRMED)
        if humano is MatchStatus.CONFIRMED:
            classes["tp" if confirmou else "fn"].add(pair)
        else:
            classes["fp" if confirmou else "tn"].add(pair)
    return Counts(**{name: frozenset(pares) for name, pares in classes.items()})


def _slice_name(product: Product) -> str:
    if product.subcategory:
        return f"{product.category}/{product.subcategory}"
    return str(product.category)


@dataclass(frozen=True)
class Evaluation:
    """Resultado de uma versão do motor sobre um conjunto."""

    dataset_id: str
    split: str
    measures: str
    matcher_version: str
    labels: int
    overall: Counts
    by_category: dict[str, Counts]
    outcomes: dict[Pair, Outcome]

    @property
    def suppressed(self) -> dict[Pair, str]:
        """Pares que a arbitragem tirou de campo, e a regra que os tirou."""
        return {
            pair: suppressed_by
            for pair, outcome in self.outcomes.items()
            if (suppressed_by := outcome.suppressed_by) is not None
        }

    def summary(self) -> dict[str, object]:
        return {
            "dataset_id": self.dataset_id,
            "split": self.split,
            "mede": self.measures,
            "matcher_version": self.matcher_version,
            "rotulos": self.labels,
            "geral": self.overall.summary(),
            "por_categoria": {
                nome: contagem.summary() for nome, contagem in self.by_category.items()
            },
        }


def evaluate(dataset: GoldenDataset, config: MatchingConfig = MATCHING) -> Evaluation:
    """Mede uma versão do motor sobre um conjunto de referência."""
    outcomes = run_engine(dataset, config)
    labels = dataset.labels
    produtos = dataset.products_by_id

    por_categoria: dict[str, list[Pair]] = {}
    for pair in labels:
        por_categoria.setdefault(_slice_name(produtos[pair[0]]), []).append(pair)

    return Evaluation(
        dataset_id=dataset.dataset_id,
        split=str(dataset.split),
        measures=dataset.measures,
        matcher_version=config.version,
        labels=len(labels),
        overall=count(outcomes, labels, labels),
        by_category={
            nome: count(outcomes, labels, pares)
            for nome, pares in sorted(por_categoria.items())
        },
        outcomes=outcomes,
    )


# ------------------------------------------------------------------- relatório


def _num(value: float | None) -> str:
    return "—" if value is None else f"{value:.3f}".replace(".", ",")


def _line(nome: str, counts: Counts) -> str:
    base = (
        f"  {nome:<34} TP {len(counts.tp):>3}  FP {len(counts.fp):>3}  "
        f"FN {len(counts.fn):>3}  TN {len(counts.tn):>3}  fila {len(counts.review):>3}"
    )
    if not counts.sufficient:
        return (
            f"{base}  amostra insuficiente "
            f"(n={counts.support}, positivos={counts.positives})"
        )
    return f"{base}  P {_num(counts.precision)}  R {_num(counts.recall)}  F1 {_num(counts.f1)}"


def format_report(evaluation: Evaluation, baseline: Evaluation | None = None) -> str:
    """Relatório legível de uma avaliação, opcionalmente comparada a outra versão."""
    overall = evaluation.overall
    linhas = [
        f"{evaluation.dataset_id} · split {evaluation.split} · mede {evaluation.measures}",
        f"motor {evaluation.matcher_version} · {evaluation.labels} rótulos · "
        f"{overall.support} decididos · {len(overall.undecided)} em dúvida humana "
        f"(fora da medida)",
        _line("geral", overall),
        "por categoria",
        *(_line(nome, contagem) for nome, contagem in evaluation.by_category.items()),
    ]

    if baseline is not None:
        linhas += [
            "",
            f"comparado a {baseline.matcher_version}",
            _line("geral", baseline.overall),
            f"  acertos perdidos: {sorted(baseline.overall.tp - overall.tp) or 'nenhum'}",
            f"  falsos positivos novos: {sorted(overall.fp - baseline.overall.fp) or 'nenhum'}",
        ]
    if evaluation.measures == "regressão":
        linhas.append(
            "  atenção: conjunto de desenvolvimento — isto mede regressão sobre o corpus"
            " que originou as regras, não desempenho em dados novos."
        )
    return "\n".join(linhas)


# ------------------------------------------------------------------------- CLI


def _engine(name: str) -> MatchingConfig:
    if name not in ENGINES:
        raise SystemExit(f"motor '{name}' desconhecido; use: {', '.join(ENGINES)}")
    return ENGINES[name]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.evaluation",
        description="Mede o motor de matching contra os Golden Datasets.",
    )
    parser.add_argument(
        "datasets", nargs="*", type=Path, help=f"arquivos; sem isto, todos em {GOLDEN_DIR}"
    )
    parser.add_argument("--matcher", default=MATCHING.version, help="versão avaliada")
    parser.add_argument("--baseline", default=None, help="versão de comparação")
    parser.add_argument("--json", action="store_true", help="saída em JSON")
    args = parser.parse_args(argv)

    conjuntos = [load(path) for path in args.datasets] if args.datasets else load_all()
    if not conjuntos:
        print(f"nenhum conjunto em {GOLDEN_DIR}", file=sys.stderr)
        return 1

    config = _engine(args.matcher)
    base = _engine(args.baseline) if args.baseline else None

    relatorios: list[object] = []
    for dataset in conjuntos:
        avaliacao = evaluate(dataset, config)
        anterior = evaluate(dataset, base) if base is not None else None
        if args.json:
            saida = avaliacao.summary()
            if anterior is not None:
                saida["baseline"] = anterior.summary()
            relatorios.append(saida)
        else:
            relatorios.append(format_report(avaliacao, anterior))

    if args.json:
        print(json.dumps(relatorios, ensure_ascii=False, indent=2))
    else:
        print("\n\n".join(str(relatorio) for relatorio in relatorios))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
