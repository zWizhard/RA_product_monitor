"""
Extração de um Golden Dataset a partir das validações humanas do banco.

É o caminho de ida: o banco é estado vivo e a revisão mais recente sobrescreve a
anterior (D-010); congelar um recorte datado em arquivo é o que transforma validação
humana em gabarito (D-015). Depois do arquivo gravado, medir não depende mais do banco.

Só entra no conjunto o par com decisão humana registrada (`reviewed_status`), inclusive
o par já datado como obsoleto: o parecer do revisor continua valendo mesmo quando o
motor deixou de propor aquele par — e é justamente esse caso que mede falso negativo.

**O recorte exclui o que já está congelado.** O banco acumula: as decisões do conjunto
anterior continuam nele. Reexportá-las colocaria os mesmos pares em dois conjuntos e o
corpus que originou as regras dentro do `holdout` — que passaria a chamar de desempenho
uma medida feita, em boa parte, sobre dados de desenvolvimento. Por isso a extração lê os
conjuntos já existentes e deixa de fora todo par que algum deles contém.

Produtos e reclamações vão para o arquivo inteiros, para que a medição seja reproduzível
offline mesmo que o cadastro mude. O escopo dos produtos é o catálogo, não apenas os
produtos rotulados: a arbitragem compara candidatos entre si, e avaliar sem os vizinhos
daria outro resultado. As reclamações são as do recorte — as do conjunto anterior já
estão no arquivo dele.

Uso:

    python -m app.golden_export --id glicosimetro-2026-10-20 --split holdout \\
        --por "Nome do revisor" --origem "coleta de 20/10, 6 termos novos"
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterable, Sequence
from datetime import date
from pathlib import Path

from app.collect_repository import ComplaintRepository
from app.config import get_settings
from app.db import Database
from app.golden import GOLDEN_DIR, GoldenDataset, GoldenLabel, Split, load_all, save
from app.match_repository import MatchRepository
from app.repository import ProductRepository

# Teto por página de leitura; o escopo inteiro é lido paginando até esgotar.
_PAGE = 500


def _all(fetch, **kwargs):
    """Pagina uma listagem do repositório até o fim."""
    items = []
    while True:
        page = fetch(limit=_PAGE, offset=len(items), **kwargs)
        items.extend(page)
        if len(page) < _PAGE:
            return items


def build_dataset(
    db: Database,
    *,
    dataset_id: str,
    split: Split | str,
    descricao: str,
    rotulado_por: str,
    origem: str,
    rotulado_em: date | None = None,
    congelados: Iterable[GoldenDataset] = (),
) -> GoldenDataset:
    """Monta o conjunto com a decisão humana do banco que `congelados` ainda não contém."""
    ja_congelados = {pair for dataset in congelados for pair in dataset.labels}
    matches = [
        match
        for match in _all(MatchRepository(db).list, reviewed=True)
        if (match.product_id, match.complaint_id) not in ja_congelados
    ]
    if not matches:
        raise ValueError(
            "nenhuma decisão humana nova no banco: não há gabarito a congelar"
            if ja_congelados
            else "nenhuma decisão humana no banco: não há gabarito a congelar"
        )

    rotulos = [
        GoldenLabel(
            product_id=match.product_id,
            complaint_id=match.complaint_id,
            humano=match.reviewed_status,
            match_id=match.id,
            motor_version=match.matcher_version,
            motor_status=match.status,
            motor_metodo=match.method,
        )
        for match in matches
    ]
    produtos = _all(ProductRepository(db).list)
    citadas = {rotulo.complaint_id for rotulo in rotulos}
    reclamacoes = [c for c in _all(ComplaintRepository(db).list) if c.id in citadas]

    # Um rótulo órfão é dado perdido, não conjunto inválido: falha aqui, com o par na
    # mensagem, em vez de na validação genérica do formato.
    conhecidos = {p.id for p in produtos}, {c.id for c in reclamacoes}
    faltando = [
        rotulo.pair
        for rotulo in rotulos
        if rotulo.product_id not in conhecidos[0] or rotulo.complaint_id not in conhecidos[1]
    ]
    if faltando:
        raise ValueError(f"pares rotulados sem produto ou reclamação no banco: {faltando}")

    return GoldenDataset(
        dataset_id=dataset_id,
        split=Split(split),
        descricao=descricao,
        rotulado_por=rotulado_por,
        rotulado_em=rotulado_em or date.today(),
        origem=origem,
        produtos=produtos,
        reclamacoes=reclamacoes,
        rotulos=rotulos,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.golden_export",
        description="Congela as validações humanas do banco em um Golden Dataset.",
    )
    parser.add_argument("--id", required=True, help="identificador e nome do arquivo")
    parser.add_argument(
        "--split", required=True, choices=[str(s) for s in Split], help="papel do conjunto"
    )
    parser.add_argument("--por", required=True, help="quem rotulou")
    parser.add_argument("--origem", required=True, help="de onde veio o recorte")
    parser.add_argument("--descricao", default="", help="o que o conjunto contém")
    parser.add_argument("--dir", type=Path, default=GOLDEN_DIR, help="diretório de destino")
    args = parser.parse_args(argv)

    destino = args.dir / f"{args.id}.json"
    congelados = load_all(args.dir)

    db = Database(get_settings().db_path)
    db.connect()
    try:
        dataset = build_dataset(
            db,
            dataset_id=args.id,
            split=args.split,
            descricao=args.descricao or f"Validações humanas congeladas em {args.id}.",
            rotulado_por=args.por,
            origem=args.origem,
            congelados=congelados,
        )
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1
    finally:
        db.close()

    try:
        save(dataset, destino)
    except FileExistsError as error:
        print(f"{error}; use outro --id", file=sys.stderr)
        return 1
    print(f"{destino}: {len(dataset.rotulos)} rótulos, {len(dataset.reclamacoes)} reclamações")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
