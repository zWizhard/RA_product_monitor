"""Produtos e reclamações de teste, compartilhados pelos testes de matching e revisão."""

from app.collect_repository import ComplaintRepository
from app.models import ComplaintCreate, ProductCreate
from app.repository import ProductRepository

RA = "https://www.reclameaqui.com.br"


def make_product(db, **overrides):
    data = {
        "name": "Ultraformer III",
        "category": "equipamentos",
        "subcategory": "estetica",
        "brand": "Classys",
        "model": "MPT",
        "aliases": ["Ultraformer MPT"],
        "search_terms": ["ultrassom microfocado"],
    }
    data.update(overrides)
    return ProductRepository(db).create(ProductCreate(**data))


def make_complaint(db, slug="c1", **overrides):
    data = {
        "source": "reclame_aqui",
        "external_id": slug,
        "url": f"{RA}/clinica-x/{slug}/",
        "title": "Queimadura após sessão",
        "description": "Fiquei com bolhas no rosto.",
        "company": "Clínica X",
    }
    data.update(overrides)
    complaint_id, _ = ComplaintRepository(db).upsert(ComplaintCreate(**data))
    return ComplaintRepository(db).get(complaint_id)


def make_possible(db, slug="c1"):
    """Marca + contexto: o motor deixa em `possible`, à espera de decisão humana."""
    return make_complaint(
        db,
        slug,
        title="Aparelho da Classys",
        description="O modelo MPT queimou minha pele.",
    )
