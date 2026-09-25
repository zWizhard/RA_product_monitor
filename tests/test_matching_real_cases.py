"""
Regressão do matching sobre os casos reais da coleta de glicosímetros.

Cada teste reproduz um trecho realmente coletado do Reclame Aqui, identificado pelo
id que a reclamação tinha no banco quando a falha foi observada (c6, c9, c13, c15,
c59, c63). Os trechos são recortados, nunca reescritos — inclusive o mascaramento
que a própria fonte aplica ao nome da marca em c63. São os casos que a avaliação de
`det-1` errou, e os que ela acertava e precisam continuar acertando. Nada aqui
acessa rede.
"""

from dataclasses import replace

from app.match_repository import MatchRepository
from app.match_service import run_matching
from app.matching import DET2, MATCHING, MatchMethod, MatchStatus
from app.models import MatchRequest
from tests.helpers import make_complaint, make_product

# Trechos reais, como coletados. Recortados no tamanho necessário para o caso.
C6_TITLE = "Experiência negativa com sensor Accu Chek Smart Guide, com falhas de leitura."
C6_DESCRIPTION = (
    "Era usuário do Libre 2 Plus, foi oferecido para mim na farmácia o Accu Chek Smart "
    "Guide, com orientações que teria uma melhor performance que o Libre e que o custo "
    "benefício seria menor."
)
C13_TITLE = "FreeStyle Libre 2 Plus por R$ 98,97: valor constava no sistema, mas venda foi retida"
C15_TITLE = "Produto essencial com defeito, SAC inacessível e formulário enganoso no site da Abbott"
C15_DESCRIPTION = (
    "No dia 11/09/2026, comprei um sensor FreeStyle Libre na Drogaria Pacheco, e o produto "
    "apresentou defeito de leitura no primeiríssimo dia de uso. Como solução paliativa, o "
    "representante me ofereceu apenas um cupom de R$ 80 de desconto para que eu COMPRASSE "
    "outro modelo (Libre 2 Plus), gastando ainda mais dinheiro."
)
C59_TITLE = "ATRASO NA TROCA DE SENSOR QUE PAROU DE FUNCIONAR DO NADA"
C59_DESCRIPTION = (
    "Sou DM1 e usuária do sensor Sibionics há 3 anos, amava o sensor, mas de um tempo "
    "para cá está havendo bastantes falhas na leitura da glicose."
)
# Abertura de c63: nestes trechos a fonte mascara o nome da marca, e só a empresa da
# página identifica o fabricante (mais adiante o texto real volta a citá-la).
C63_TITLE = "***** nunca funcionou, empresa recusou a garantia e ofereceu apenas um cupom"
C63_DESCRIPTION = (
    "Adquiri um sensor ***** que simplesmente nunca funcionou. O sensor nunca "
    "sincronizou via Bluetooth e nunca chegou a realizar uma única leitura de glicose."
)
C41_TITLE = "Não consigo trocar sensor Freestyle Libre com defeito"
C41_DESCRIPTION = (
    "Faz mais de ano que meu pai usa o aparelho da freestyle libre plus 2 e algumas vezes "
    "tivesmo erros no sensor, todos consegui fazer a troca, pois apresentavam códigos de "
    "erro do próprio sensor."
)
C46_TITLE = "Sensor de glicose Sibionics com defeito e suporte recusa garantia após testes"
C46_DESCRIPTION = (
    "No sistema deles, o protocolo de atendimento exigiu testes de ponta de dedo. Fui "
    "obrigado a comprar um glicosímetro capilar (Accu-Chek Guide Me) única e exclusivamente "
    "para comprovar a falha do sensor."
)
C9_TITLE = "Cliente se arrepende da compra de glicosímetro por dificuldade em encontrar tiras"
C9_DESCRIPTION = "Comprei esse glicosimetro pensando que era facil achar as tiras."


def glicosimetro(db, **overrides):
    """Produto do escopo real, sem os padrões de equipamento estético do helper."""
    data = {
        "category": "diagnostico_in_vitro",
        "subcategory": "glicosimetro",
        "brand": None,
        "manufacturer": None,
        "model": None,
        "aliases": [],
        "search_terms": [],
    }
    data.update(overrides)
    return make_product(db, **data)


def libre(db):
    """Família e modelo específico: o par que `det-1` confirmava junto."""
    familia = glicosimetro(
        db, name="FreeStyle Libre", brand="FreeStyle", manufacturer="Abbott", model="Libre"
    )
    modelo = glicosimetro(
        db,
        name="FreeStyle Libre 2 Plus",
        brand="FreeStyle",
        manufacturer="Abbott",
        model="Libre 2 Plus",
        aliases=["Libre 2 Plus"],
    )
    return familia, modelo


def smartguide(db):
    return glicosimetro(
        db,
        name="Accu-Chek SmartGuide",
        brand="Accu-Chek",
        manufacturer="Roche",
        model="SmartGuide",
        aliases=["Accu-Chek Smart Guide"],
    )


def by_product(db):
    return {match.product_id: match for match in MatchRepository(db).list(limit=500)}


# ------------------------------------------------- 1. arbitragem por especificidade


def test_c13_familia_deixa_de_ser_candidata_diante_do_modelo(db):
    familia, modelo = libre(db)
    make_complaint(db, "c13", title=C13_TITLE, description=None, company="Raia")

    run_matching(db, MatchRequest())
    matches = by_product(db)

    assert matches[modelo.id].status is MatchStatus.CONFIRMED
    assert matches[modelo.id].shadowed_by_product_id is None
    # O nome da família perde para o do modelo: no `det-3` ele nem chega a ser candidato.
    assert familia.id not in matches


def test_c13_no_det2_a_familia_ainda_virava_candidato(db):
    """O `det-2` continua executável: é com ele que se compara qualquer mudança."""
    familia, modelo = libre(db)
    make_complaint(db, "c13", title=C13_TITLE, description=None, company="Raia")

    run_matching(db, MatchRequest(), DET2)
    matches = by_product(db)

    assert matches[modelo.id].status is MatchStatus.CONFIRMED
    assert matches[familia.id].status is MatchStatus.POSSIBLE
    assert matches[familia.id].shadowed_by_product_id == modelo.id
    assert matches[familia.id].matcher_version == "det-2"


def test_arbitragem_nao_alcanca_produtos_citados_em_trechos_distintos(db):
    familia, modelo = libre(db)
    make_complaint(
        db, "c15", title=C15_TITLE, description=C15_DESCRIPTION, company="Abbott FreeStyle"
    )

    run_matching(db, MatchRequest())
    matches = by_product(db)

    # "FreeStyle Libre" e "(Libre 2 Plus)" aparecem em trechos diferentes do corpo.
    assert matches[familia.id].shadowed_by_product_id is None
    assert matches[modelo.id].shadowed_by_product_id is None


# --------------------------------------------------- 2. evidência só na descrição


def test_c15_mencao_comparativa_no_corpo_nao_confirma(db):
    _, modelo = libre(db)
    make_complaint(
        db, "c15", title=C15_TITLE, description=C15_DESCRIPTION, company="Abbott FreeStyle"
    )

    run_matching(db, MatchRequest())
    match = by_product(db)[modelo.id]

    # "COMPRASSE outro modelo (Libre 2 Plus)" é o aparelho oferecido, não o reclamado.
    assert match.evidence_field == "description"
    assert match.status is MatchStatus.POSSIBLE
    assert "comparativa" in match.explanation
    assert "Libre 2 Plus" in match.evidence


def test_c15_modelo_citado_apenas_no_corpo_continua_confirmando(db):
    familia, _ = libre(db)
    make_complaint(
        db, "c15", title=C15_TITLE, description=C15_DESCRIPTION, company="Abbott FreeStyle"
    )

    run_matching(db, MatchRequest())
    match = by_product(db)[familia.id]

    # "comprei um sensor FreeStyle Libre" é evidência legítima, e só existe no corpo.
    assert match.evidence_field == "description"
    assert match.status is MatchStatus.CONFIRMED
    assert "comprei um sensor FreeStyle Libre" in match.evidence


def test_c6_empresa_de_outra_marca_impede_confirmacao_pelo_corpo(db):
    _, modelo = libre(db)
    accu = smartguide(db)
    make_complaint(
        db,
        "c6",
        title=C6_TITLE,
        description=C6_DESCRIPTION,
        company="Roche Diabetes - Accu-Chek",
    )

    run_matching(db, MatchRequest())
    matches = by_product(db)

    # A reclamação é da página da Roche e é sobre o Accu-Chek: ele confirma pelo título.
    assert matches[accu.id].status is MatchStatus.CONFIRMED
    assert matches[accu.id].evidence_field == "title"
    # "Era usuário do Libre 2 Plus" é o aparelho anterior, de outra marca.
    assert matches[modelo.id].status is MatchStatus.POSSIBLE
    assert matches[modelo.id].evidence_field == "description"


# ------------------------------------------- 3. marcas e modelos compostos


def test_grafia_composta_casa_deterministicamente(db):
    onetouch = glicosimetro(
        db, name="OneTouch Select Plus", brand="OneTouch", manufacturer="LifeScan",
        model="Select Plus",
    )
    gtech = glicosimetro(db, name="G-Tech Free", brand="G-Tech", manufacturer="Accumed",
                         model="Free")
    accuchek = glicosimetro(db, name="Accu-Chek Guide", brand="Accu-Chek", manufacturer="Roche",
                            model="Guide")
    make_complaint(db, "one", title="One Touch Select Plus parou de ler", description=None,
                   company="OneTouch Diabetes Brasil")
    make_complaint(db, "gte", title="GTech Free com defeito", description=None,
                   company="Accumed-Glicomed")
    make_complaint(db, "acc", title="Accuchek Guide marcando errado", description=None,
                   company="Roche Diabetes - Accu-Chek")

    run_matching(db, MatchRequest())
    matches = by_product(db)

    for product in (onetouch, gtech, accuchek):
        match = matches[product.id]
        assert match.method in (MatchMethod.EXACT_NAME, MatchMethod.ALIAS), match.method
        assert match.status is MatchStatus.CONFIRMED
        assert "grafia composta" in match.explanation


def test_forma_compacta_nao_casa_dentro_de_outra_palavra(db):
    """A forma compactada junta tokens inteiros, nunca pedaço de palavra maior."""
    glicosimetro(db, name="G-Tech Free", brand="G-Tech", manufacturer="Accumed")
    make_complaint(db, "dentro", title="Comprei um gtechfreedom paralelo", description=None)

    assert run_matching(db, MatchRequest()).matched == 0

    make_complaint(db, "junto", title="Meu GTechFree parou de ler", description=None)
    assert run_matching(db, MatchRequest()).matched == 1


# ----------------------------------------- 4. `company` como evidência de marca


def test_c75_empresa_composta_atribui_marca_e_recupera_para_revisao(db):
    """c75: a marca só aparece na empresa da página, e com grafia separada."""
    produto = glicosimetro(
        db,
        name="OneTouch Select Plus",
        brand="OneTouch",
        manufacturer="LifeScan",
        model="Select Plus",
    )
    make_complaint(
        db,
        "c75",
        title="O aparelho de Glicosimetro da jonhson & jonhson que estar na listas CANCELADOS",
        description=None,
        company="One Touch Ultra",
    )

    run_matching(db, MatchRequest())
    match = by_product(db)[produto.id]

    assert match.method is MatchMethod.COMPANY_BRAND
    assert match.status is MatchStatus.POSSIBLE
    assert match.evidence_field == "company"
    assert "One Touch Ultra" in match.evidence


def test_marca_mascarada_pela_fonte_recupera_pela_empresa(db):
    produto = glicosimetro(
        db, name="Sibionics GS1", brand="Sibionics", manufacturer="Sibionics", model="GS1"
    )
    make_complaint(db, "c63", title=C63_TITLE, description=C63_DESCRIPTION, company="Sibionics")

    run_matching(db, MatchRequest())
    match = by_product(db)[produto.id]

    # A marca só existe na empresa da página; o contexto de sensor qualifica, o modelo
    # não aparece. Candidato para revisão humana, nunca confirmação automática.
    assert match.method is MatchMethod.COMPANY_BRAND
    assert match.status is MatchStatus.POSSIBLE
    assert match.evidence_field == "company"
    assert "Sibionics" in match.evidence and "sensor" in match.evidence


def test_c59_marca_no_corpo_com_contexto_de_sensor_nao_confirma_modelo(db):
    produto = glicosimetro(
        db, name="Sibionics GS1", brand="Sibionics", manufacturer="Sibionics", model="GS1"
    )
    make_complaint(db, "c59", title=C59_TITLE, description=C59_DESCRIPTION, company="Sibionics")

    run_matching(db, MatchRequest())
    match = by_product(db)[produto.id]

    # "sensor Sibionics" identifica a marca, não o GS1: vai para a fila, não confirma.
    assert match.method is MatchMethod.BRAND_CONTEXT
    assert match.status is MatchStatus.POSSIBLE
    assert "Sibionics" in match.evidence


def test_empresa_sem_contexto_de_categoria_nao_gera_match(db):
    glicosimetro(db, name="Sibionics GS1", brand="Sibionics", manufacturer="Sibionics",
                 model="GS1")
    make_complaint(
        db,
        "sem",
        title="Reembolso negado e atendimento demorado",
        description="Pedi o estorno e ninguém respondeu.",
        company="Sibionics",
    )

    assert run_matching(db, MatchRequest()).matched == 0


def test_c9_marketplace_nao_atribui_marca(db):
    libre(db)
    glicosimetro(db, name="Sibionics GS1", brand="Sibionics", manufacturer="Sibionics")
    make_complaint(db, "c9", title=C9_TITLE, description=C9_DESCRIPTION, company="Mercado Livre")

    assert run_matching(db, MatchRequest()).matched == 0


def test_contexto_de_categoria_nao_identifica_modelo_sozinho(db):
    glicosimetro(db, name="Sibionics GS1", brand="Sibionics", manufacturer="Sibionics")
    make_complaint(
        db,
        "gen",
        title="Sensor de glicose com defeito",
        description="O monitor de glicose parou no terceiro dia.",
        company="Drogasil",
    )

    assert run_matching(db, MatchRequest()).matched == 0


def test_evidencia_apenas_no_nome_da_empresa_nunca_confirma(db):
    produto = glicosimetro(
        db, name="Accu-Chek", brand="Accu-Chek", manufacturer="Roche", model="Guide"
    )
    make_complaint(
        db,
        "emp",
        title="Glicosímetro com defeito e sem troca",
        description="Pedi a substituição e não resolveram.",
        company="Roche Diabetes - Accu-Chek",
    )

    run_matching(db, MatchRequest())
    match = by_product(db)[produto.id]

    assert match.evidence_field == "company"
    assert match.status is MatchStatus.POSSIBLE


# ------------------------------------------------------ 5. proveniência da versão


def test_reprocessar_em_nova_versao_preserva_a_decisao_anterior(db):
    _, modelo = libre(db)
    make_complaint(db, "c13", title=C13_TITLE, description=None, company="Raia")
    anterior = replace(MATCHING, version="det-teste")
    run_matching(db, MatchRequest(), anterior)
    antes = by_product(db)[modelo.id]

    run_matching(db, MatchRequest())
    depois = by_product(db)[modelo.id]
    historico = MatchRepository(db).revisions(depois.id)

    assert depois.matcher_version == MATCHING.version
    assert len(historico) == 1
    registro = historico[0]
    assert registro.matcher_version == "det-teste"
    assert registro.status is antes.status
    assert registro.score == antes.score
    assert registro.evidence == antes.evidence
    assert registro.method is antes.method


def test_datar_como_obsoleto_nao_gera_revisao(db):
    """Histórico é da decisão reescrita. Obsolescência não reescreve nada: só data."""
    from app.models import ProductUpdate
    from app.repository import ProductRepository

    # Sem marca cadastrada: tirar o nome tira a única evidência possível do par.
    produto = glicosimetro(db, name="Sibionics GS1")
    make_complaint(db, "gs1", title="Sibionics GS1 parou de ler", description=None,
                   company="Drogasil")
    run_matching(db, MatchRequest())
    match_id = MatchRepository(db).list()[0].id

    ProductRepository(db).update(produto.id, ProductUpdate(name="Outro Medidor"))
    assert run_matching(db, MatchRequest()).stale == 1

    obsoleto = MatchRepository(db).get(match_id)
    assert obsoleto.stale_since is not None
    assert "Sibionics GS1" in obsoleto.evidence
    assert MatchRepository(db).revisions(match_id) == []


def test_api_expoe_o_historico_da_decisao_automatica(client):
    client.post(
        "/products",
        json={
            "name": "FreeStyle Libre 2 Plus",
            "category": "diagnostico_in_vitro",
            "subcategory": "glicosimetro",
            "brand": "FreeStyle",
            "manufacturer": "Abbott",
        },
    ).raise_for_status()
    db = client.app.state.db
    make_complaint(db, "c13", title=C13_TITLE, description=None, company="Raia")
    client.post("/matches/run", json={}).raise_for_status()
    match_id = client.get("/matches").json()[0]["id"]

    assert client.get(f"/matches/{match_id}/revisions").json() == []
    assert client.get("/matches/999/revisions").status_code == 404

    produto_id = client.get("/products").json()[0]["id"]
    client.patch(f"/products/{produto_id}", json={"name": "Libre 2 Plus"}).raise_for_status()
    assert client.post("/matches/run", json={}).json()["updated"] == 1

    historico = client.get(f"/matches/{match_id}/revisions").json()
    assert len(historico) == 1
    assert historico[0]["matched_term"] == "FreeStyle Libre 2 Plus"
    assert historico[0]["status"] == "confirmed"
    assert historico[0]["evidence"] and historico[0]["superseded_at"]
    assert client.get(f"/matches/{match_id}").json()["matched_term"] == "Libre 2 Plus"


def test_reprocessamento_continua_idempotente_com_arbitragem(db):
    libre(db)
    make_complaint(db, "c13", title=C13_TITLE, description=None, company="Raia")

    primeira = run_matching(db, MatchRequest())
    antes = MatchRepository(db).list(limit=500)
    segunda = run_matching(db, MatchRequest())
    depois = MatchRepository(db).list(limit=500)

    assert (primeira.created, primeira.updated, primeira.unchanged) == (1, 0, 0)
    assert (segunda.created, segunda.updated, segunda.unchanged) == (0, 0, 1)
    assert [m.updated_at for m in depois] == [m.updated_at for m in antes]
    assert MatchRepository(db).revisions(depois[0].id) == []


# --------------------------------------------- 6. regras introduzidas no det-3


def test_c41_ordem_trocada_identifica_o_modelo(db):
    """#58: o corpo diz "freestyle libre plus 2"; o cadastro tem "FreeStyle Libre 2 Plus"."""
    familia, modelo = libre(db)
    make_complaint(db, "c41", title=C41_TITLE, description=C41_DESCRIPTION,
                   company="Abbott FreeStyle")

    run_matching(db, MatchRequest())
    matches = by_product(db)

    assert matches[modelo.id].method is MatchMethod.EXACT_NAME
    assert matches[modelo.id].status is MatchStatus.CONFIRMED
    assert "freestyle libre plus 2" in matches[modelo.id].evidence.lower()
    # A família, que casou só o título, perde para o modelo lido no corpo.
    assert familia.id not in matches


def test_irmao_da_marca_sai_quando_um_modelo_foi_nomeado(db):
    """c6: nomeado o SmartGuide, os outros Accu-Chek são a mesma marca, não outro aparelho."""
    guide = glicosimetro(db, name="Accu-Chek Guide", brand="Accu-Chek", manufacturer="Roche",
                         model="Guide")
    smart = smartguide(db)
    make_complaint(db, "c6", title=C6_TITLE, description=C6_DESCRIPTION,
                   company="Roche Diabetes - Accu-Chek")

    run_matching(db, MatchRequest())
    matches = by_product(db)

    assert matches[smart.id].status is MatchStatus.CONFIRMED
    assert guide.id not in matches


def test_irmao_da_marca_nao_alcanca_marca_diferente(db):
    """A supressão é dentro da marca: outra marca citada continua sendo candidata."""
    _, modelo = libre(db)
    sibionics = glicosimetro(db, name="Sibionics GS1", brand="Sibionics",
                             manufacturer="Sibionics", model="GS1")
    make_complaint(
        db,
        "duas",
        title="Sensor Sibionics falhou e precisei do FreeStyle Libre 2 Plus",
        description=None,
        company="Sibionics",
    )

    run_matching(db, MatchRequest())
    matches = by_product(db)

    assert modelo.id in matches and sibionics.id in matches


def test_c46_aparelho_usado_como_regua_nao_confirma(db):
    """"Fui obrigado a comprar um glicosímetro capilar (Accu-Chek Guide Me)" é aferição."""
    guide_me = glicosimetro(db, name="Accu-Chek Guide Me", brand="Accu-Chek",
                            manufacturer="Roche", model="Guide Me")
    make_complaint(db, "c46", title=C46_TITLE, description=C46_DESCRIPTION,
                   company="Sibionics")

    run_matching(db, MatchRequest())
    match = by_product(db)[guide_me.id]

    assert match.evidence_field == "description"
    assert match.status is MatchStatus.POSSIBLE
    assert "comparativa" in match.explanation


def test_no_det2_o_aparelho_de_aferição_confirmava(db):
    """A mesma reclamação com o motor anterior: confirmava — e o revisor descartou."""
    guide_me = glicosimetro(db, name="Accu-Chek Guide Me", brand="Accu-Chek",
                            manufacturer="Roche", model="Guide Me")
    make_complaint(db, "c46", title=C46_TITLE, description=C46_DESCRIPTION,
                   company="Sibionics")

    run_matching(db, MatchRequest(), DET2)
    match = by_product(db)[guide_me.id]

    assert match.matcher_version == "det-2"
    assert match.status is MatchStatus.CONFIRMED


def test_migracao_det2_para_det3_preserva_e_explica_o_que_saiu(db):
    """O par suprimido não some: fica datado, com a regra que o tirou e a decisão antiga."""
    familia, modelo = libre(db)
    make_complaint(db, "c13", title=C13_TITLE, description=None, company="Raia")
    run_matching(db, MatchRequest(), DET2)
    antes = by_product(db)[familia.id]
    assert antes.matcher_version == "det-2"

    resultado = run_matching(db, MatchRequest())

    assert resultado.stale == 1
    depois = MatchRepository(db).get(antes.id)
    assert depois.stale_since is not None
    assert depois.stale_reason == "suprimido por especificidade (det-3)"
    # A decisão do det-2 continua na linha, com a versão que a produziu e a evidência.
    assert depois.matcher_version == "det-2"
    assert depois.status is antes.status
    assert depois.evidence == antes.evidence
    # E o modelo específico continua confirmado, agora pelo det-3.
    assert by_product(db)[modelo.id].matcher_version == "det-3"


def test_fuzzy_continua_sem_confirmar_sozinho(db):
    glicosimetro(db, name="Accu-Chek Guide", brand="Accu-Chek", manufacturer="Roche")
    make_complaint(db, "fz", title="Tiras Accu-Chek Guiide com defeito", description=None,
                   company="Drogasil")

    run_matching(db, MatchRequest())
    match = MatchRepository(db).list()[0]

    assert match.method is MatchMethod.FUZZY
    assert match.status is MatchStatus.POSSIBLE
    assert match.score <= MATCHING.fuzzy_max_score
