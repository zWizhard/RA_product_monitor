"""Cadastro de produtos monitorados.

Não existe remoção: um produto sai do monitoramento por desativação, para que a
relação `product <-> complaint` continue auditável nas fases seguintes.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status

from app.models import Product, ProductCreate, ProductUpdate
from app.repository import ProductRepository
from app.taxonomy import Category

router = APIRouter(prefix="/products", tags=["produtos"])


def get_repository(request: Request) -> ProductRepository:
    return ProductRepository(request.app.state.db)


Repo = Annotated[ProductRepository, Depends(get_repository)]


@router.post("", response_model=Product, status_code=status.HTTP_201_CREATED)
def create_product(data: ProductCreate, repo: Repo) -> Product:
    return repo.create(data)


@router.get("", response_model=list[Product])
def list_products(
    repo: Repo,
    category: Category | None = None,
    subcategory: str | None = None,
    active: bool | None = None,
    q: Annotated[str | None, Query(description="Busca por nome, marca, modelo ou termo")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Product]:
    return repo.list(
        category=category,
        subcategory=subcategory,
        active=active,
        query=q,
        limit=limit,
        offset=offset,
    )


@router.get("/{product_id}", response_model=Product)
def get_product(product_id: int, repo: Repo) -> Product:
    return repo.get(product_id)


@router.patch("/{product_id}", response_model=Product)
def update_product(product_id: int, patch: ProductUpdate, repo: Repo) -> Product:
    return repo.update(product_id, patch)


@router.post("/{product_id}/deactivate", response_model=Product)
def deactivate_product(product_id: int, repo: Repo) -> Product:
    return repo.set_active(product_id, False)


@router.post("/{product_id}/activate", response_model=Product)
def activate_product(product_id: int, repo: Repo) -> Product:
    return repo.set_active(product_id, True)
