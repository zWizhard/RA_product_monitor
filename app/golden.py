"""
Golden Dataset: validação humana congelada como conjunto de referência.

O banco é estado vivo — uma revisão posterior sobrescreve o parecer anterior (D-010).
Um Golden Dataset é o oposto: um recorte datado, versionado em arquivo, que não muda
quando o motor muda. É contra ele que uma versão do matching é medida (D-015).

Formato (um arquivo JSON por conjunto, em `golden/`):

- cabeçalho: `dataset_id`, `split`, quem rotulou, quando, e de onde veio;
- `produtos` e `reclamacoes`: a cópia integral usada na rotulagem, para que a medição
  seja reproduzível offline mesmo que o cadastro mude depois;
- `rotulos`: o parecer humano de cada par, com a decisão que o motor dava **no momento
  da rotulagem** (`motor_*`) — dado histórico, nunca gabarito.

`split` separa o que originou as regras do que serve para medir desempenho:

- `development`: o corpus a partir do qual as regras foram escritas. Medir nele detecta
  **regressão**, não desempenho — por construção não revela o que o corpus não contém;
- `holdout`: amostra independente, coletada e rotulada inteira antes de qualquer ajuste.
  Só ela mede desempenho.

Limite do formato, registrado de propósito: o rótulo existe para o par que alguma versão
do motor propôs. Pares que nenhuma versão jamais propôs não foram vistos pelo revisor,
então o recall medido é recall sobre os pares propostos — não sobre todos os pares
possíveis. Ampliar isso exige rotular por reclamação, não por candidato.

Nada aqui decide nada sobre matching: é leitura, escrita e validação de formato.
"""

from __future__ import annotations

import json
from datetime import date
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from app.config import PROJECT_ROOT
from app.matching import MatchMethod, MatchStatus
from app.models import Complaint, Product

# Conjuntos versionados junto do código: são gabarito, não dado de execução, e por isso
# não ficam em `data/` (que é estado local e sobrescrevível).
GOLDEN_DIR = PROJECT_ROOT / "golden"

# Muda quando o formato muda de forma incompatível. Arquivo com versão diferente é
# recusado na leitura em vez de interpretado por aproximação.
SCHEMA_VERSION = 1


class InvalidGoldenDataset(ValueError):
    """Arquivo de Golden Dataset malformado ou incoerente.

    `load` é a fronteira: tudo que vem de disco falha com este erro. A construção direta
    do modelo continua falhando como qualquer modelo Pydantic (`ValidationError`, que
    também é `ValueError`), com a mesma mensagem dentro.
    """


def _unique_ids(registros: list, rotulo: str) -> set[int]:
    """Ids de uma lista do conjunto, recusando repetição.

    Id repetido não é registro a mais: a cópia que sobrevive é a última lida, e o par
    rotulado passa a apontar para um produto ou uma reclamação que não é a que o revisor
    tinha na frente.
    """
    ids: set[int] = set()
    for registro in registros:
        if registro.id in ids:
            raise InvalidGoldenDataset(f"{rotulo} {registro.id} aparece duas vezes")
        ids.add(registro.id)
    return ids


class Split(StrEnum):
    """Papel do conjunto na avaliação. Ver o cabeçalho do módulo."""

    DEVELOPMENT = "development"
    HOLDOUT = "holdout"


class GoldenLabel(BaseModel):
    """Parecer humano sobre um par produto ↔ reclamação.

    `humano` é o gabarito. Os campos `motor_*` guardam o que a versão do motor indicada
    dizia quando o revisor julgou: servem para auditar a rotulagem, não para avaliar.
    """

    model_config = ConfigDict(extra="forbid")

    product_id: int
    complaint_id: int
    humano: MatchStatus
    # Id do match no banco de origem, quando o par veio de um. Preserva a rastreabilidade
    # até a linha que o revisor tinha na frente.
    match_id: int | None = None
    motor_version: str | None = None
    motor_status: MatchStatus | None = None
    motor_metodo: MatchMethod | None = None

    @property
    def pair(self) -> tuple[int, int]:
        return (self.product_id, self.complaint_id)


class GoldenDataset(BaseModel):
    """Um conjunto de referência completo e autossuficiente."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int = SCHEMA_VERSION
    dataset_id: str
    split: Split
    descricao: str
    rotulado_por: str
    rotulado_em: date
    # De onde saiu o recorte: banco, execução de busca, versão do motor que propôs os
    # pares. Texto livre, mas obrigatório — conjunto sem origem não é auditável.
    origem: str
    produtos: list[Product]
    reclamacoes: list[Complaint]
    rotulos: list[GoldenLabel]

    @model_validator(mode="after")
    def _check(self) -> GoldenDataset:
        if self.schema_version != SCHEMA_VERSION:
            raise InvalidGoldenDataset(
                f"schema_version {self.schema_version} não é {SCHEMA_VERSION}"
            )
        if not self.rotulos:
            raise InvalidGoldenDataset("conjunto sem rótulo humano não é gabarito")
        produtos = _unique_ids(self.produtos, "produto")
        reclamacoes = _unique_ids(self.reclamacoes, "reclamação")
        vistos: set[tuple[int, int]] = set()
        for rotulo in self.rotulos:
            if rotulo.pair in vistos:
                raise InvalidGoldenDataset(f"par {rotulo.pair} rotulado duas vezes")
            vistos.add(rotulo.pair)
            if rotulo.product_id not in produtos:
                raise InvalidGoldenDataset(
                    f"rótulo cita produto {rotulo.product_id}, ausente do conjunto"
                )
            if rotulo.complaint_id not in reclamacoes:
                raise InvalidGoldenDataset(
                    f"rótulo cita reclamação {rotulo.complaint_id}, ausente do conjunto"
                )
        return self

    @property
    def labels(self) -> dict[tuple[int, int], MatchStatus]:
        return {rotulo.pair: rotulo.humano for rotulo in self.rotulos}

    @property
    def match_ids(self) -> dict[tuple[int, int], int | None]:
        return {rotulo.pair: rotulo.match_id for rotulo in self.rotulos}

    @property
    def products_by_id(self) -> dict[int, Product]:
        return {produto.id: produto for produto in self.produtos}

    @property
    def measures(self) -> str:
        """O que uma medição sobre este conjunto significa. Ver o cabeçalho do módulo."""
        return "regressão" if self.split is Split.DEVELOPMENT else "desempenho"


def load(path: Path) -> GoldenDataset:
    """Lê um conjunto do disco, validando formato e coerência interna."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise InvalidGoldenDataset(f"{path.name}: JSON inválido ({error})") from error
    try:
        dataset = GoldenDataset.model_validate(raw)
    except ValidationError as error:
        raise InvalidGoldenDataset(f"{path.name}: {error}") from error
    # O nome do arquivo é o identificador: divergir dos dois é como um conjunto vira dois
    # na referência de um teste ou de um relatório.
    if dataset.dataset_id != path.stem:
        raise InvalidGoldenDataset(
            f"{path.name}: dataset_id '{dataset.dataset_id}' não confere com o arquivo"
        )
    return dataset


def save(dataset: GoldenDataset, path: Path, *, overwrite: bool = False) -> Path:
    """Grava o conjunto em ordem estável, para que o diff mostre só o que mudou.

    Não sobrescreve: reescrever um gabarito é ajustar a régua depois da medida, e o
    conjunto anterior não tem de onde voltar. Substituir um conjunto existente é ato
    deliberado, e exige `overwrite=True`.
    """
    if path.exists() and not overwrite:
        raise FileExistsError(f"{path} já existe; um conjunto de referência não se reescreve")
    ordenado = dataset.model_copy(
        update={
            "produtos": sorted(dataset.produtos, key=lambda p: p.id),
            "reclamacoes": sorted(dataset.reclamacoes, key=lambda c: c.id),
            "rotulos": sorted(dataset.rotulos, key=lambda r: r.pair),
        }
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(ordenado.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def load_all(directory: Path = GOLDEN_DIR) -> list[GoldenDataset]:
    """Todos os conjuntos de um diretório, em ordem de `dataset_id`.

    Arquivo ilegível interrompe a leitura, nomeado: medir só o que sobrou seria relatar
    menos do que quem pediu a medição pensa estar medindo. O diretório é de conjuntos de
    referência e de mais nada.
    """
    if not directory.is_dir():
        return []
    datasets = []
    for path in sorted(directory.glob("*.json")):
        try:
            datasets.append(load(path))
        except InvalidGoldenDataset as error:
            raise InvalidGoldenDataset(
                f"{directory} só contém conjuntos de referência — {error}"
            ) from error
    return sorted(datasets, key=lambda dataset: dataset.dataset_id)
