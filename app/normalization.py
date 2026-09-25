"""
Normalização de texto.

Usada no cadastro (chave de duplicidade, termos de busca) e reaproveitada pelas
etapas determinísticas de matching. Regra única: minúsculas, sem acentos, sem
pontuação, espaços colapsados.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

_NON_ALNUM = re.compile(r"[^0-9a-z]+")


def normalize_text(value: str | None) -> str:
    """Forma canônica de `value` para comparação exata e busca."""
    if not value:
        return ""
    decomposed = unicodedata.normalize("NFKD", value)
    without_accents = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return _NON_ALNUM.sub(" ", without_accents.casefold()).strip()


def dedupe_terms(values: Iterable[str]) -> list[str]:
    """Remove vazios e repetições (pela forma normalizada), preservando a ordem e o texto original."""
    seen: set[str] = set()
    result: list[str] = []
    for raw in values:
        term = (raw or "").strip()
        normalized = normalize_text(term)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(term)
    return result
