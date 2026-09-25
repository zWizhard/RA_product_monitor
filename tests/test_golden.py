"""
Testes do Golden Dataset: formato, extração do banco e medição.

O que se protege aqui é o gabarito, não o motor — a regressão do motor está em
`tests/test_regressao_rotulada.py`. Um conjunto de referência corrompido ou uma métrica
calculada sobre amostra que não a sustenta são erros piores que um match errado: fazem o
erro parecer medido. Nada aqui acessa rede.
"""

import json
from datetime import UTC, datetime

import pytest

from app.evaluation import (
    MIN_POSITIVES,
    MIN_SUPPORT,
    Counts,
    count,
    evaluate,
    format_report,
    main,
)
from app.golden import (
    GOLDEN_DIR,
    GoldenDataset,
    GoldenLabel,
    InvalidGoldenDataset,
    load,
    load_all,
    save,
)
from app.golden_export import build_dataset
from app.match_repository import MatchRepository
from app.match_service import run_matching
from app.matching import MatchStatus
from app.models import Complaint, MatchRequest, Product, ProductUpdate
from app.repository import ProductRepository
from tests.helpers import make_possible, make_product

AGORA = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)


def produto(id: int, name: str = "Ultraformer III", **overrides) -> Product:
    dados = {
        "id": id,
        "name": name,
        "category": "equipamentos",
        "subcategory": "estetica",
        "brand": "Classys",
        "manufacturer": None,
        "model": None,
        "regulatory_id": None,
        "aliases": [],
        "search_terms": [],
        "active": True,
        "natural_key": f"chave {id}",
        "created_at": AGORA,
        "updated_at": AGORA,
    }
    return Product(**{**dados, **overrides})


def reclamacao(id: int, title: str = "Queimadura com Ultraformer III") -> Complaint:
    return Complaint(
        id=id,
        source="reclame_aqui",
        external_id=f"c{id}",
        url=f"https://www.reclameaqui.com.br/clinica-x/c{id}/",
        dedupe_key=f"reclame_aqui|c{id}",
        title=title,
        description=None,
        company="Clínica X",
        location=None,
        status=None,
        published_at=None,
        published_text=None,
        collected_at=AGORA,
        last_seen_at=AGORA,
    )


def conjunto(rotulos, produtos=None, reclamacoes=None, **overrides) -> GoldenDataset:
    dados = {
        "dataset_id": "conjunto-de-teste",
        "split": "holdout",
        "descricao": "conjunto sintético",
        "rotulado_por": "teste",
        "rotulado_em": "2026-09-25",
        "origem": "construído no teste",
        "produtos": produtos or [produto(1)],
        "reclamacoes": reclamacoes or [reclamacao(1)],
        "rotulos": rotulos,
    }
    return GoldenDataset(**{**dados, **overrides})


def rotulo(product_id: int, complaint_id: int, humano: str = "confirmed") -> GoldenLabel:
    return GoldenLabel(product_id=product_id, complaint_id=complaint_id, humano=humano)


# ------------------------------------------------------------------- o formato


def test_conjunto_gravado_volta_igual(tmp_path):
    original = conjunto([rotulo(1, 1)])
    caminho = save(original, tmp_path / f"{original.dataset_id}.json")

    assert load(caminho) == original


def test_gravacao_ordena_para_o_diff_mostrar_so_o_que_mudou(tmp_path):
    dataset = conjunto(
        [rotulo(2, 2), rotulo(1, 2), rotulo(1, 1)],
        produtos=[produto(2, "Outro"), produto(1)],
        reclamacoes=[reclamacao(2), reclamacao(1)],
    )
    caminho = save(dataset, tmp_path / f"{dataset.dataset_id}.json")
    bruto = json.loads(caminho.read_text(encoding="utf-8"))

    assert [p["id"] for p in bruto["produtos"]] == [1, 2]
    assert [c["id"] for c in bruto["reclamacoes"]] == [1, 2]
    assert [(r["product_id"], r["complaint_id"]) for r in bruto["rotulos"]] == [
        (1, 1),
        (1, 2),
        (2, 2),
    ]


def test_par_rotulado_duas_vezes_e_recusado():
    """Dois pareceres para o mesmo par é ambiguidade no gabarito, não dado a mais."""
    with pytest.raises(ValueError, match="duas vezes"):
        conjunto([rotulo(1, 1, "confirmed"), rotulo(1, 1, "discarded")])


def test_rotulo_sem_produto_ou_reclamacao_e_recusado():
    """Sem o produto e a reclamação no arquivo, a medição não é reproduzível offline."""
    with pytest.raises(ValueError, match="produto 9"):
        conjunto([rotulo(9, 1)])
    with pytest.raises(ValueError, match="reclamação 9"):
        conjunto([rotulo(1, 9)])


def test_conjunto_sem_rotulo_nao_e_gabarito():
    with pytest.raises(ValueError, match="sem rótulo"):
        conjunto([])


def test_arquivo_incoerente_falha_com_o_erro_do_formato(tmp_path):
    """`load` é a fronteira: o que vem de disco falha como `InvalidGoldenDataset`."""
    caminho = tmp_path / "conjunto-de-teste.json"
    bruto = json.loads(save(conjunto([rotulo(1, 1)]), caminho).read_text(encoding="utf-8"))
    bruto["rotulos"].append({"product_id": 9, "complaint_id": 1, "humano": "confirmed"})
    caminho.write_text(json.dumps(bruto), encoding="utf-8")

    with pytest.raises(InvalidGoldenDataset, match="produto 9"):
        load(caminho)

    caminho.write_text("{ isto não é json", encoding="utf-8")
    with pytest.raises(InvalidGoldenDataset, match="JSON inválido"):
        load(caminho)


def test_conjunto_existente_nao_e_sobrescrito(tmp_path):
    """Reescrever um gabarito é ajustar a régua depois da medida — e não há de onde voltar."""
    caminho = tmp_path / "conjunto-de-teste.json"
    save(conjunto([rotulo(1, 1)]), caminho)
    outro = conjunto([rotulo(1, 1, "discarded")])

    with pytest.raises(FileExistsError, match="não se reescreve"):
        save(outro, caminho)
    assert load(caminho).rotulos[0].humano is MatchStatus.CONFIRMED

    save(outro, caminho, overwrite=True)
    assert load(caminho).rotulos[0].humano is MatchStatus.DISCARDED


def test_produto_ou_reclamacao_repetido_e_recusado():
    """Id repetido troca em silêncio o registro que o rótulo aponta."""
    with pytest.raises(ValueError, match="produto 1 aparece duas vezes"):
        conjunto([rotulo(1, 1)], produtos=[produto(1), produto(1, "Outro nome")])
    with pytest.raises(ValueError, match="reclamação 1 aparece duas vezes"):
        conjunto([rotulo(1, 1)], reclamacoes=[reclamacao(1), reclamacao(1, "Outro")])


def test_arquivo_ilegivel_interrompe_a_leitura_do_diretorio(tmp_path):
    """Medir só o que sobrou relataria menos do que quem pediu pensa estar medindo."""
    save(conjunto([rotulo(1, 1)]), tmp_path / "conjunto-de-teste.json")
    (tmp_path / "outra-coisa.json").write_text('{"nao": "e um conjunto"}', encoding="utf-8")

    with pytest.raises(InvalidGoldenDataset, match="outra-coisa.json"):
        load_all(tmp_path)


def test_nome_do_arquivo_e_o_identificador(tmp_path):
    dataset = conjunto([rotulo(1, 1)])
    caminho = save(dataset, tmp_path / "outro-nome.json")

    with pytest.raises(InvalidGoldenDataset, match="não confere com o arquivo"):
        load(caminho)


def test_formato_futuro_e_recusado_em_vez_de_interpretado(tmp_path):
    caminho = tmp_path / "conjunto-de-teste.json"
    bruto = json.loads(save(conjunto([rotulo(1, 1)]), caminho).read_text(encoding="utf-8"))
    bruto["schema_version"] = 99
    caminho.write_text(json.dumps(bruto), encoding="utf-8")

    with pytest.raises(InvalidGoldenDataset, match="schema_version 99"):
        load(caminho)


def test_split_diz_o_que_a_medicao_significa():
    assert conjunto([rotulo(1, 1)], split="holdout").measures == "desempenho"
    assert conjunto([rotulo(1, 1)], split="development").measures == "regressão"
    with pytest.raises(ValueError):
        conjunto([rotulo(1, 1)], split="treino")


def test_diretorio_sem_conjunto_nao_e_erro(tmp_path):
    assert load_all(tmp_path) == []
    assert load_all(tmp_path / "inexistente") == []


def test_conjuntos_versionados_do_projeto_carregam():
    """Guarda os arquivos de `golden/`: gabarito corrompido invalida toda medição."""
    conjuntos = load_all(GOLDEN_DIR)

    assert conjuntos, f"nenhum conjunto de referência em {GOLDEN_DIR}"
    assert len({d.dataset_id for d in conjuntos}) == len(conjuntos)


# -------------------------------------------------------- extração do banco


def _rotula(db, status=MatchStatus.CONFIRMED):
    match = MatchRepository(db).list()[0]
    return MatchRepository(db).review(
        match.id, status=status, reviewed_by="revisor", note=None
    )


def test_extracao_congela_a_decisao_humana_do_banco(db, tmp_path):
    make_product(db)
    make_possible(db)
    run_matching(db, MatchRequest())
    match = _rotula(db)

    dataset = build_dataset(
        db,
        dataset_id="extraido",
        split="holdout",
        descricao="teste",
        rotulado_por="revisor",
        origem="banco de teste",
    )

    assert [r.pair for r in dataset.rotulos] == [(match.product_id, match.complaint_id)]
    assert dataset.rotulos[0].humano is MatchStatus.CONFIRMED
    # A decisão automática vai junto como histórico, com a versão que a produziu.
    assert dataset.rotulos[0].motor_status is MatchStatus.POSSIBLE
    assert dataset.rotulos[0].motor_version == match.matcher_version
    assert dataset.rotulos[0].match_id == match.id
    # Gravado e relido sem o banco: é isso que torna a medição reproduzível.
    assert load(save(dataset, tmp_path / "extraido.json")) == dataset


def test_extracao_leva_o_catalogo_inteiro_nao_so_o_rotulado(db):
    """A arbitragem compara candidatos entre si: sem os vizinhos, a medida muda."""
    make_product(db)
    make_product(db, name="Ultraformer MPT Plus", aliases=[])
    make_possible(db)
    run_matching(db, MatchRequest())
    _rotula(db)

    dataset = build_dataset(
        db,
        dataset_id="extraido",
        split="holdout",
        descricao="teste",
        rotulado_por="revisor",
        origem="banco de teste",
    )

    assert len(dataset.produtos) == 2
    assert len(dataset.rotulos) == 1


def test_extracao_nao_reexporta_o_que_ja_esta_congelado(db, tmp_path):
    """O banco acumula: sem recorte, o corpus de desenvolvimento entraria no `holdout`."""
    make_product(db)
    make_possible(db, "c1")
    make_possible(db, "c2")
    run_matching(db, MatchRequest())
    antigos, novos = MatchRepository(db).list()
    for match in (antigos, novos):
        MatchRepository(db).review(
            match.id, status=MatchStatus.CONFIRMED, reviewed_by="revisor", note=None
        )

    primeiro = build_dataset(
        db,
        dataset_id="primeiro",
        split="development",
        descricao="teste",
        rotulado_por="revisor",
        origem="banco de teste",
    )
    assert len(primeiro.rotulos) == 2

    # Congelado o primeiro, o segundo recorte fica sem par novo a levar.
    with pytest.raises(ValueError, match="nenhuma decisão humana nova"):
        build_dataset(
            db,
            dataset_id="segundo",
            split="holdout",
            descricao="teste",
            rotulado_por="revisor",
            origem="banco de teste",
            congelados=[primeiro],
        )


def test_recorte_leva_so_as_reclamacoes_dos_seus_rotulos(db):
    """A reclamação do conjunto anterior já está no arquivo dele; repeti-la é ruído."""
    make_product(db)
    make_possible(db, "c1")
    make_possible(db, "c2")
    run_matching(db, MatchRequest())
    antigo, novo = MatchRepository(db).list()
    for match in (antigo, novo):
        MatchRepository(db).review(
            match.id, status=MatchStatus.CONFIRMED, reviewed_by="revisor", note=None
        )
    congelado = conjunto(
        [rotulo(antigo.product_id, antigo.complaint_id)],
        produtos=[produto(antigo.product_id)],
        reclamacoes=[reclamacao(antigo.complaint_id)],
    )

    recorte = build_dataset(
        db,
        dataset_id="recorte",
        split="holdout",
        descricao="teste",
        rotulado_por="revisor",
        origem="banco de teste",
        congelados=[congelado],
    )

    assert [r.pair for r in recorte.rotulos] == [(novo.product_id, novo.complaint_id)]
    assert [c.id for c in recorte.reclamacoes] == [novo.complaint_id]
    # O catálogo continua inteiro: a arbitragem depende dos vizinhos.
    assert len(recorte.produtos) == len(ProductRepository(db).list())


def test_par_sem_decisao_humana_fica_de_fora(db):
    make_product(db)
    make_possible(db)
    run_matching(db, MatchRequest())

    with pytest.raises(ValueError, match="nenhuma decisão humana"):
        build_dataset(
            db,
            dataset_id="vazio",
            split="holdout",
            descricao="teste",
            rotulado_por="revisor",
            origem="banco de teste",
        )


def test_par_obsoleto_com_parecer_humano_continua_no_conjunto(db):
    """É o caso que mede falso negativo: o revisor confirmou, o motor deixou de propor."""
    product = make_product(db)
    make_possible(db)
    run_matching(db, MatchRequest())
    _rotula(db)

    # Sem a marca, o motor não reencontra evidência: o par é datado como obsoleto.
    ProductRepository(db).update(product.id, ProductUpdate(brand="Outra Marca"))
    run_matching(db, MatchRequest())
    assert MatchRepository(db).list()[0].stale_since is not None

    dataset = build_dataset(
        db,
        dataset_id="extraido",
        split="holdout",
        descricao="teste",
        rotulado_por="revisor",
        origem="banco de teste",
    )

    assert len(dataset.rotulos) == 1


# ------------------------------------------------------------------- métricas


def pares(n: int, inicio: int = 0) -> frozenset:
    return frozenset((1, i) for i in range(inicio, inicio + n))


def test_metricas_saem_das_contagens():
    contagem = Counts(tp=pares(30), fp=pares(10, 100), fn=pares(10, 200), tn=pares(50, 300))

    assert contagem.precision == 0.75
    assert contagem.recall == 0.75
    assert contagem.f1 == 0.75
    assert contagem.support == 100
    assert contagem.positives == 40


def test_motor_que_nao_confirma_nada_nao_tem_precisao_zero():
    """0/0 não é 0,0: sem nenhuma confirmação, precisão é ausência de medida."""
    contagem = Counts(fn=pares(10), tn=pares(50, 100))

    assert contagem.precision is None
    assert contagem.recall == 0.0
    assert contagem.f1 is None


def test_amostra_pequena_nao_vira_metrica():
    pequena = Counts(tp=pares(3), fp=pares(1, 10), fn=pares(1, 20), tn=pares(5, 30))
    assert pequena.support < MIN_SUPPORT
    assert pequena.sufficient is False
    resumo = pequena.summary()
    assert resumo["precision"] is None and resumo["recall"] is None and resumo["f1"] is None
    # As contagens continuam lá: o que se recusa é a métrica, não o dado.
    assert resumo["TP"] == 3 and resumo["amostra"] == 10


def test_f1_zero_e_medida_nao_ausencia_de_medida():
    """Motor que só erra tem F1 zero; `None` fica reservado para o que não se mede."""
    contagem = Counts(fp=pares(10), fn=pares(10, 100))

    assert contagem.precision == 0.0 and contagem.recall == 0.0
    assert contagem.f1 == 0.0


def test_amostra_grande_sem_positivo_humano_tambem_e_insuficiente():
    """Recall exige positivos: 100 descartes não medem o que o motor deixa passar."""
    contagem = Counts(fp=pares(2), tn=pares(98, 100))

    assert contagem.support >= MIN_SUPPORT
    assert contagem.positives < MIN_POSITIVES
    assert contagem.sufficient is False


def test_duvida_humana_fica_fora_de_acerto_e_de_erro():
    """`possible` do revisor não é gabarito: vira acerto ou erro conveniente."""
    dataset = conjunto(
        [rotulo(1, 1, "possible")],
        reclamacoes=[reclamacao(1, "Queimadura com Ultraformer III")],
    )
    avaliacao = evaluate(dataset)
    geral = avaliacao.overall

    assert avaliacao.outcomes[(1, 1)].decision == str(MatchStatus.CONFIRMED)
    assert geral.undecided == frozenset({(1, 1)})
    assert geral.support == 0


def test_possible_do_motor_conta_na_fila_e_nao_em_acerto():
    dataset = conjunto(
        [rotulo(1, 1, "confirmed")],
        reclamacoes=[reclamacao(1, "Aparelho da Classys usado na estética")],
    )
    geral = evaluate(dataset).overall

    assert geral.review == frozenset({(1, 1)})
    assert geral.tp == frozenset()
    assert geral.fn == frozenset({(1, 1)}), "encaminhar não é confirmar"


def test_par_que_o_motor_nem_propoe_conta_como_falso_negativo():
    dataset = conjunto(
        [rotulo(1, 1, "confirmed")],
        reclamacoes=[reclamacao(1, "Reclamação sem qualquer menção ao aparelho")],
    )
    geral = evaluate(dataset).overall

    assert geral.fn == frozenset({(1, 1)})
    assert geral.review == frozenset()


def test_fatias_por_categoria_somam_o_geral():
    dataset = conjunto(
        [rotulo(1, 1), rotulo(2, 2, "discarded")],
        produtos=[
            produto(1),
            produto(2, "Accu-Chek Guide", category="diagnostico_in_vitro",
                    subcategory="glicosimetro", brand="Accu-Chek"),
        ],
        reclamacoes=[reclamacao(1), reclamacao(2, "Problema com o medidor")],
    )
    avaliacao = evaluate(dataset)

    assert set(avaliacao.by_category) == {
        "equipamentos/estetica",
        "diagnostico_in_vitro/glicosimetro",
    }
    assert sum(c.support for c in avaliacao.by_category.values()) == avaliacao.overall.support


def test_relatorio_recusa_numero_que_a_amostra_nao_sustenta():
    dataset = conjunto([rotulo(1, 1)])
    texto = format_report(evaluate(dataset))

    assert "amostra insuficiente" in texto
    assert "P 0," not in texto


def test_relatorio_avisa_quando_o_conjunto_e_o_de_desenvolvimento():
    desenvolvimento = format_report(evaluate(conjunto([rotulo(1, 1)], split="development")))
    holdout = format_report(evaluate(conjunto([rotulo(1, 1)], split="holdout")))

    assert "mede regressão" in desenvolvimento and "não desempenho" in desenvolvimento
    assert "mede desempenho" in holdout and "não desempenho" not in holdout


def test_classificacao_nao_inventa_rotulo():
    """`count` só classifica par rotulado; par sem parecer humano não entra na medida."""
    rotulos = {(1, 1): MatchStatus.CONFIRMED}
    contagem = count({}, rotulos, rotulos)

    assert contagem.support == 1
    assert contagem.fn == frozenset({(1, 1)})


# ------------------------------------------------------------------------ CLI


def test_cli_mede_os_conjuntos_de_um_diretorio(tmp_path, capsys):
    dataset = conjunto([rotulo(1, 1)])
    caminho = save(dataset, tmp_path / f"{dataset.dataset_id}.json")

    assert main([str(caminho), "--baseline", "det-2"]) == 0
    saida = capsys.readouterr().out
    assert dataset.dataset_id in saida and "comparado a det-2" in saida


def test_cli_em_json_nao_afirma_metrica_sem_amostra(tmp_path, capsys):
    dataset = conjunto([rotulo(1, 1)])
    caminho = save(dataset, tmp_path / f"{dataset.dataset_id}.json")

    assert main([str(caminho), "--json"]) == 0
    relatorio = json.loads(capsys.readouterr().out)[0]

    assert relatorio["geral"]["amostra_suficiente"] is False
    assert relatorio["geral"]["precision"] is None
    assert relatorio["mede"] == "desempenho"


def test_cli_recusa_motor_desconhecido(tmp_path):
    dataset = conjunto([rotulo(1, 1)])
    caminho = save(dataset, tmp_path / f"{dataset.dataset_id}.json")

    with pytest.raises(SystemExit, match="det-9"):
        main([str(caminho), "--matcher", "det-9"])


def test_cli_sem_conjunto_falha(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("app.evaluation.load_all", lambda *a, **k: [])

    assert main([]) == 1
    assert "nenhum conjunto" in capsys.readouterr().err
