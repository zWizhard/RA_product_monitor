"""
Regressão do motor contra o Golden Dataset de glicosímetros.

`golden/glicosimetro-2026-09-25.json` guarda 145 pares produto ↔ reclamação julgados um
a um por um revisor em 24 e 25/09/2026, sobre as 75 reclamações já coletadas. É um
conjunto **fixo**: mudança de motor se mede contra ele, não contra a impressão de quem
mudou.

A medição é a de `app.evaluation` — a mesma que o relatório da linha de comando usa, com
a mesma convenção: positivo é o motor ter **confirmado**, `possible` é encaminhamento e
entra na fila, e o par em que o revisor ficou em dúvida fica fora de TP/FP/FN/TN.

Os números fixados aqui são o **gate**: o do `det-2` é âncora (se mudar, o `det-2` deixou
de ser reproduzível e comparar versões perde o sentido) e o do `det-3` é piso — existe
para denunciar perda, não para ser perseguido. Como o conjunto é o de desenvolvimento,
isto mede regressão, não desempenho. Nada aqui acessa rede nem banco.
"""

from dataclasses import replace

import pytest

from app.evaluation import SUPPRESSED, count, evaluate, run_engine
from app.golden import GOLDEN_DIR, load
from app.matching import DET2, MATCHING, MatchMethod, MatchStatus

DATASET = GOLDEN_DIR / "glicosimetro-2026-09-25.json"

# Resultado medido do `det-2` sobre este conjunto.
DET2_ESPERADO = {"TP": 30, "FP": 10, "FN": 4, "TN": 76, "revisao": 103}
# Piso do `det-3`: não confirmar menos, não errar mais, não encaminhar mais.
DET3_PISO = {"TP": 32, "FP": 5, "revisao": 47}


@pytest.fixture(scope="module")
def dataset():
    return load(DATASET)


@pytest.fixture(scope="module")
def avaliacoes(dataset):
    """O conjunto medido pelas duas versões do motor."""
    return evaluate(dataset, DET2), evaluate(dataset, MATCHING)


def test_conjunto_e_o_de_desenvolvimento(dataset):
    """O relatório precisa dizer o que mede: este corpus originou as regras do `det-3`."""
    assert dataset.measures == "regressão"
    assert len(dataset.rotulos) == 145


def test_det2_continua_reproduzivel(avaliacoes):
    """Desligar as regras do `det-3` reproduz o motor anterior, número por número."""
    resumo = avaliacoes[0].overall.summary()
    assert {chave: resumo[chave] for chave in DET2_ESPERADO} == DET2_ESPERADO


def test_det3_nao_perde_acerto_nem_ganha_erro(avaliacoes):
    antes, depois = avaliacoes[0].overall, avaliacoes[1].overall

    assert antes.tp - depois.tp == frozenset(), "verdadeiro positivo perdido"
    assert depois.fp - antes.fp == frozenset(), "falso positivo novo"
    assert len(depois.tp) >= DET3_PISO["TP"]
    assert len(depois.fp) <= DET3_PISO["FP"]
    assert len(depois.review) <= DET3_PISO["revisao"]
    assert len(depois.fp) < len(antes.fp) and len(depois.tp) > len(antes.tp)


def test_metricas_publicadas_conferem(avaliacoes):
    """Os números que `docs/` cita saem desta medição, não de cálculo à parte."""
    det3 = avaliacoes[1].overall
    assert (round(det3.precision, 3), round(det3.recall, 3), round(det3.f1, 3)) == (
        0.865,
        0.941,
        0.901,
    )
    # Uma categoria só: a fatia por categoria tem de repetir o geral, sem inventar corte.
    assert list(avaliacoes[1].by_category) == ["diagnostico_in_vitro/glicosimetro"]
    assert avaliacoes[1].by_category["diagnostico_in_vitro/glicosimetro"] == det3


def test_supressao_so_alcanca_par_que_o_revisor_descartou(dataset, avaliacoes):
    """Suprimir é a única ação irreversível do motor: não pode alcançar acerto humano."""
    rotulos = dataset.labels
    suprimidos = avaliacoes[1].suppressed
    assert suprimidos, "nenhuma supressão: a regra não está agindo"
    for chave, regra in suprimidos.items():
        assert rotulos.get(chave) is not MatchStatus.CONFIRMED, (
            f"{chave} suprimido por {regra} era o produto certo"
        )
    por_especificidade = [c for c, r in suprimidos.items() if r == "especificidade"]
    assert {rotulos[c] for c in por_especificidade if c in rotulos} == {
        MatchStatus.DISCARDED
    }


def test_sentinela_58_o_modelo_com_ordem_trocada(dataset, avaliacoes):
    """#58: o corpo diz "freestyle libre plus 2" e o cadastro tem "Libre 2 Plus".

    O `det-2` só alcançava esse par por marca+contexto, e a regra do irmão da marca o
    removeria. Com a precedência do nome mais específico ele passa a ser identificado
    pelo nome — e sobrevive às regras seguintes, como o revisor decidiu.
    """
    chave = next(c for c, match_id in dataset.match_ids.items() if match_id == 58)
    assert dataset.labels[chave] is MatchStatus.CONFIRMED

    assert avaliacoes[0].outcomes[chave].decision == str(MatchStatus.POSSIBLE)
    resultado = avaliacoes[1].outcomes[chave]
    candidato = resultado.candidate

    assert resultado.decision == str(MatchStatus.CONFIRMED)
    assert candidato.method is MatchMethod.EXACT_NAME
    assert candidato.suppressed_by is None
    assert "ordem" in candidato.explanation or "libre plus 2" in candidato.evidence.lower()


def test_cada_regra_isolada_nao_piora_o_conjunto(dataset, avaliacoes):
    """Ligada sozinha sobre o `det-2`, nenhuma das quatro regras perde acerto nem erra mais.

    O tamanho da fila não entra aqui de propósito: a precedência do nome, sozinha, tira
    confirmações erradas e as devolve para revisão — a fila encolhe quando a supressão
    entra, não antes. Exigir queda a cada regra seria exigir a coisa errada.
    """
    base = avaliacoes[0].overall
    rotulos = dataset.labels
    for chave in ("model_precedence", "suppress_shadowed", "brand_sibling", "extended_comparative"):
        config = replace(DET2, version=f"det-2+{chave}", **{chave: True})
        contagem = count(run_engine(dataset, config), rotulos, rotulos)
        assert base.tp - contagem.tp == frozenset(), f"{chave} perdeu verdadeiro positivo"
        assert len(contagem.fp) <= len(base.fp), f"{chave} criou falso positivo"

        # As duas regras que suprimem candidato são as que reduzem a fila; as outras não.
        if chave in ("suppress_shadowed", "brand_sibling"):
            assert len(contagem.review) < len(base.review), f"{chave} não reduziu a fila"


def test_par_suprimido_nao_e_par_sem_candidato(avaliacoes):
    """Supressão e ausência de evidência chegam ao mesmo lugar por caminhos diferentes.

    Ambos saem de `confirmed`, mas só o suprimido traz a regra que o tirou — é o que
    `app.match_service` grava em `stale_reason` e o que uma auditoria precisa ler.
    """
    suprimidos = [
        resultado
        for resultado in avaliacoes[1].outcomes.values()
        if resultado.decision == SUPPRESSED
    ]
    assert suprimidos
    assert all(r.suppressed_by in ("especificidade", "irmao_de_marca") for r in suprimidos)
