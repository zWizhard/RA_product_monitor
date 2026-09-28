"""
Relevância de uma reclamação para uma área de vigilância, por regra de vocabulário.

Serve à pesquisa livre: um termo solto ("silicone") traz de tudo, e a etiqueta ajuda a
ler primeiro o que interessa. Não é matching: não associa produto, não grava nada e
nunca esconde reclamação — só ordena e mostra a palavra que decidiu.

Etiquetas:
- `relevante`: cita vocabulário da área e nada que aponte outro assunto;
- `duvidosa`: cita os dois, ou nenhum dos dois;
- `fora_do_assunto`: não cita a área e cita outro assunto — ou, com área/categoria
  escolhida, só cita vocabulário de outra área.

O vocabulário é rascunho a revisar com o uso. Mudou lista ou regra, muda
`RULES_VERSION`: a versão vai em toda resposta.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from app.normalization import normalize_text
from app.taxonomy import CATEGORY_CONTEXT_TERMS, Category

RULES_VERSION = "rel-2"

RELEVANT = "relevante"
DOUBTFUL = "duvidosa"
OFF_TOPIC = "fora_do_assunto"
LABEL_ORDER = (RELEVANT, DOUBTFUL, OFF_TOPIC)

# Objetos de consumo que dividem palavra com as áreas ("silicone", "laser", "implante").
_CONSUMER_GOODS = (
    "capinha", "capa de celular", "celular", "smartphone", "iphone", "notebook",
    "fone de ouvido", "relogio", "smartwatch", "pulseira", "carregador",
    "forma de silicone", "assadeira", "cozinha", "utensilio", "utensilios", "espatula",
    "pote", "tampa", "garrafa", "mamadeira", "chupeta", "brinquedo",
    "vedacao", "vedante", "calafetar", "box do banheiro", "pia", "janela",
    "carro", "veiculo", "automovel", "pneu", "impressora", "toner", "cartucho",
    "copo menstrual", "coletor menstrual",
)
# Cosmético "com ácido hialurônico"/"efeito preenchedor" é o grosso do ruído dos
# preenchedores injetáveis.
_COSMETICS = (
    "sérum", "serum", "creme", "hidratante", "sabonete", "protetor solar", "shampoo",
    "condicionador", "cápsula", "cápsulas", "suplemento", "colágeno", "maquiagem",
    "batom", "gloss", "cosmético", "cosméticos", "demaquilante", "tônico", "skincare",
    "gel de limpeza", "perfume",
)


@dataclass(frozen=True)
class Area:
    key: str
    label: str
    category: Category
    context: tuple[str, ...]
    off_topic: tuple[str, ...]


AREAS: tuple[Area, ...] = (
    Area(
        "estetica",
        "Equipamentos de estética",
        Category.EQUIPAMENTOS,
        context=(
            "hifu", "ultrassom microfocado", "laser", "depilação a laser", "luz pulsada",
            "ipl", "criolipólise", "radiofrequência", "microagulhamento", "carboxiterapia",
            "cavitação", "ultracavitação", "endermoterapia", "eletroestimulação",
            "jato de plasma", "procedimento estético", "tratamento estético",
            "clínica de estética", "clínica estética", "esteticista", "fotodepilação",
            "aparelho de estética", "equipamento de estética",
        ),
        off_topic=(*_CONSUMER_GOODS, "impressora a laser", "mira laser", "nível a laser"),
    ),
    Area(
        "glicosimetro",
        "Glicosímetros",
        Category.DIAGNOSTICO_IN_VITRO,
        # Mesmo vocabulário que o matching usa para qualificar marca.
        context=CATEGORY_CONTEXT_TERMS[(Category.DIAGNOSTICO_IN_VITRO, "glicosimetro")],
        # Celular fica de fora: o sensor contínuo é lido por aplicativo no celular.
        off_topic=(
            "sensor de estacionamento", "sensor de ré", "sensor de presença",
            "alarme", "câmera", "lâmpada", "carro", "veículo", "pneu",
        ),
    ),
    Area(
        "mamario",
        "Implantes mamários",
        Category.MATERIAIS_IMPLANTAVEIS,
        context=(
            "prótese mamária", "próteses mamárias", "prótese de mama", "próteses de mama",
            "prótese de silicone", "próteses de silicone", "implante mamário",
            "implantes mamários", "implante de silicone", "implantes de silicone",
            "mamoplastia", "mastopexia", "explante", "contratura capsular",
            "capsulectomia", "seio", "seios", "mama", "mamas", "cirurgia plástica",
            "cirurgião plástico", "cirurgiã plástica", "doença do silicone",
        ),
        off_topic=(*_CONSUMER_GOODS, "sutiã adesivo"),
    ),
    Area(
        "dentario",
        "Implantes dentários",
        Category.MATERIAIS_IMPLANTAVEIS,
        context=(
            "implante dentário", "implantes dentários", "implante dental", "implantes dentais",
            "prótese dentária", "próteses dentárias", "prótese dental", "dentadura",
            "implantodontia", "enxerto ósseo", "carga imediata", "dentista",
            "cirurgião dentista", "odontologia", "odontológica", "odontológico",
            "clínica odontológica", "dente", "dentes", "gengiva", "mandíbula", "maxila",
            "periimplantite", "peri-implantite",
        ),
        off_topic=(
            *_CONSUMER_GOODS, "implante capilar", "implante hormonal",
            "implante contraceptivo", "creme dental", "escova de dente", "escova dental",
            "fio dental", "plano odontológico", "plano dental", "convênio odontológico",
        ),
    ),
    Area(
        "pmma",
        "PMMA",
        Category.MATERIAIS_ESTETICOS,
        context=(
            "pmma", "polimetilmetacrilato", "metacrilato", "bioplastia",
            "preenchimento definitivo", "preenchedor definitivo", "preenchimento",
            "preenchimentos", "harmonização", "harmonização facial", "harmonização glútea",
            "glúteo", "glúteos", "granuloma", "procedimento estético",
        ),
        off_topic=(
            *_CONSUMER_GOODS, *_COSMETICS, "placa de acrílico", "chapa de acrílico",
            "aquário", "vitrine", "letreiro", "filamento", "impressora 3d", "unha", "unhas",
        ),
    ),
    Area(
        "acido_hialuronico",
        "Ácido hialurônico (preenchimento)",
        Category.MATERIAIS_ESTETICOS,
        context=(
            "preenchimento", "preenchimento labial", "preenchimento facial", "preenchedor",
            "hialuronidase", "harmonização", "harmonização facial", "rinomodelação",
            "bigode chinês", "olheiras", "biomédica", "biomédico", "dermatologista",
            "procedimento estético",
        ),
        off_topic=(*_CONSUMER_GOODS, *_COSMETICS),
    ),
)

AREAS_BY_KEY = {area.key: area for area in AREAS}

CATEGORY_LABELS = {
    Category.EQUIPAMENTOS: "Equipamentos",
    Category.DIAGNOSTICO_IN_VITRO: "Diagnóstico in vitro",
    Category.MATERIAIS_IMPLANTAVEIS: "Materiais implantáveis",
    Category.MATERIAIS_ESTETICOS: "Materiais estéticos",
}

# Sinais de dano à pessoa. Não mudam a etiqueta: só destacam o caso para leitura.
ADVERSE_SIGNALS = (
    "infecção", "infeccionou", "infeccionada", "infeccionado", "inflamação", "inflamou",
    "inflamada", "inflamado", "necrose", "necrosou", "rompeu", "rompimento", "ruptura",
    "rompida", "internada", "internado", "internação", "uti", "pronto socorro",
    "hospital", "hospitalizada", "hospitalizado", "reoperação", "nova cirurgia",
    "segunda cirurgia", "cirurgia de retirada", "reação alérgica", "alergia",
    "choque anafilático", "queimadura", "queimaduras", "bolhas", "cicatriz",
    "cicatrizes", "deformidade", "deformação", "assimetria", "granuloma", "granulomas",
    "nódulo", "nódulos", "caroço", "abscesso", "pus", "febre", "sangramento",
    "hemorragia", "dor intensa", "dores fortes", "cegueira", "perda de visão", "embolia",
    "trombose", "hipoglicemia", "hiperglicemia", "cetoacidose", "desmaio", "desmaiei",
    "convulsão", "óbito", "faleceu", "sequela", "sequelas", "paralisia", "linfoma",
    "rejeição", "perdi o implante", "periimplantite", "peri-implantite",
)


@dataclass(frozen=True)
class Relevance:
    label: str
    area: str | None
    context_terms: tuple[str, ...]
    off_topic_terms: tuple[str, ...]
    adverse_signals: tuple[str, ...]


@lru_cache(maxsize=None)
def _normalized(term: str) -> str:
    """Vocabulário é fixo: normaliza cada termo uma vez, não uma vez por reclamação."""
    return normalize_text(term)


def _found(padded_text: str, terms: tuple[str, ...]) -> tuple[str, ...]:
    """Termos presentes como palavra/expressão inteira, na grafia da lista, sem repetição."""
    seen: set[str] = set()
    found: list[str] = []
    for term in terms:
        normalized = _normalized(term)
        if normalized and f" {normalized} " in padded_text and normalized not in seen:
            seen.add(normalized)
            found.append(term)
    return tuple(found)


def classify(
    title: str,
    description: str | None,
    area_key: str | None = None,
    category: Category | None = None,
) -> Relevance:
    """Etiqueta a reclamação para a área pedida, para a mais próxima das áreas da
    `category`, ou para a mais próxima de todas quando nenhuma das duas é dada.

    Com recorte, reclamação que só cita vocabulário de outra área é de outro assunto.
    Levanta `KeyError` para área desconhecida.
    """
    padded = f" {normalize_text(f'{title} {description or ''}')} "
    if area_key:
        areas: tuple[Area, ...] = (AREAS_BY_KEY[area_key],)
    elif category:
        areas = tuple(area for area in AREAS if area.category == category)
    else:
        areas = AREAS
    adverse = _found(padded, ADVERSE_SIGNALS)

    evaluated = [
        (area, _found(padded, area.context), _found(padded, area.off_topic)) for area in areas
    ]
    # Mais vocabulário da área primeiro; no empate, menos sinal de outro assunto.
    evaluated.sort(key=lambda item: (-len(item[1]), len(item[2])))
    clean = [item for item in evaluated if item[1] and not item[2]]
    if clean:
        area, context, off = clean[0]
        label = RELEVANT
    else:
        area, context, off = evaluated[0]
        if context:
            label = DOUBTFUL
        else:
            # Sem vocabulário de área nenhuma: fora do assunto se algo aponta outro.
            off = next((item[2] for item in evaluated if item[2]), ())
            if not off:
                others = (other for other in AREAS if other not in areas)
                off = next((found for other in others if (found := _found(padded, other.context))), ())
            label = OFF_TOPIC if off else DOUBTFUL
    return Relevance(
        label=label,
        area=area.key if context else (area_key or None),
        context_terms=context,
        off_topic_terms=off,
        adverse_signals=adverse,
    )
