"""
Taxonomia de produtos monitorados (ver `docs/DOMAIN.md`).

Categoria e subcategoria são fechadas: o escopo de vigilância é definido antes da
coleta, não descoberto a partir dela.
"""

from __future__ import annotations

from enum import StrEnum


class InvalidTaxonomy(ValueError):
    """Par categoria/subcategoria fora da taxonomia definida em `docs/DOMAIN.md`."""


class Category(StrEnum):
    EQUIPAMENTOS = "equipamentos"
    DIAGNOSTICO_IN_VITRO = "diagnostico_in_vitro"
    MATERIAIS_IMPLANTAVEIS = "materiais_implantaveis"
    MATERIAIS_ESTETICOS = "materiais_esteticos"


SUBCATEGORIES: dict[Category, tuple[str, ...]] = {
    Category.EQUIPAMENTOS: ("estetica",),
    Category.DIAGNOSTICO_IN_VITRO: ("glicosimetro",),
    Category.MATERIAIS_IMPLANTAVEIS: ("mamario", "dentario"),
    Category.MATERIAIS_ESTETICOS: ("pmma", "acido_hialuronico"),
}


def canonical_subcategory(value: str | None) -> str | None:
    """Forma canônica da subcategoria, usada tanto para gravar quanto para consultar."""
    return (value or "").strip().casefold() or None


def validate_pair(category: Category, subcategory: str | None) -> str | None:
    """Valida o par categoria/subcategoria e devolve a subcategoria canônica.

    Levanta `InvalidTaxonomy` com a lista de valores aceitos quando o par é inválido.
    """
    allowed = SUBCATEGORIES.get(category, ())
    value = canonical_subcategory(subcategory)
    if not allowed:
        if value is not None:
            raise InvalidTaxonomy(f"categoria '{category}' não aceita subcategoria")
        return None
    if value is None:
        raise InvalidTaxonomy(
            f"subcategoria é obrigatória para '{category}'; aceitas: {', '.join(allowed)}"
        )
    if value not in allowed:
        raise InvalidTaxonomy(
            f"subcategoria '{value}' inválida para '{category}'; aceitas: {', '.join(allowed)}"
        )
    return value


# Vocabulário de categoria: como o consumidor nomeia o *tipo* de produto, não o modelo.
# Serve só para qualificar uma marca já encontrada (ver `app.matching`); nunca identifica
# um modelo sozinho, por isso fica aqui e não entre os aliases do cadastro.
CATEGORY_CONTEXT_TERMS: dict[tuple[Category, str | None], tuple[str, ...]] = {
    (Category.DIAGNOSTICO_IN_VITRO, "glicosimetro"): (
        "glicosimetro",
        "glicosimetros",
        "medidor de glicose",
        "medidor de glicemia",
        "monitor de glicose",
        "monitor de glicemia",
        "monitoramento continuo da glicose",
        "sensor de glicose",
        "sensor de glicemia",
        "sensores de glicose",
        # Como o usuário de monitoramento contínuo chama o aparelho. Genérico demais
        # para identificar modelo — e é justamente por isso que só qualifica marca.
        "sensor",
        "sensores",
        "aparelho de medir glicose",
        "tira de glicemia",
        "tiras de glicemia",
        "fita de glicemia",
        "fitas de glicemia",
    ),
}


def category_context_terms(category: Category, subcategory: str | None) -> tuple[str, ...]:
    """Termos de contexto da categoria. Vazio quando o vocabulário ainda não foi definido."""
    return CATEGORY_CONTEXT_TERMS.get((category, canonical_subcategory(subcategory)), ())
