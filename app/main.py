"""API do RA Product Monitor — health check, cadastro, coleta, matching e dashboard."""

from contextlib import asynccontextmanager
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.collector import ReclameAquiCollector
from app.config import get_settings
from app.db import SCHEMA_VERSION, Database
from app.repository import DuplicateProduct, RecordNotFound
from app.routers import complaints, dashboard, matches, products, searches
from app.taxonomy import InvalidTaxonomy


@asynccontextmanager
async def lifespan(app: FastAPI):
    db = Database(get_settings().db_path)
    db.connect()
    app.state.db = db
    # Playwright só é importado quando uma coleta acontece; construir o coletor aqui
    # não exige a dependência opcional para subir a API.
    app.state.collector = ReclameAquiCollector()
    try:
        yield
    finally:
        db.close()


def _error(status_code: int, detail: str) -> JSONResponse:
    return JSONResponse({"detail": detail}, status_code=status_code)


_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _cross_origin_write(request: Request) -> bool:
    """Escrita disparada por página de outra origem (CSRF).

    Navegador sempre manda `Origin` em POST/PATCH; cliente fora do navegador (curl,
    testes) não manda e não é afetado. `Origin: null` também é recusado.
    """
    if request.method in _SAFE_METHODS:
        return False
    origin = request.headers.get("origin")
    if origin is None:
        return False
    return urlsplit(origin).netloc != request.headers.get("host")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, version=settings.version, lifespan=lifespan)

    @app.middleware("http")
    async def _same_origin_writes(request: Request, call_next):
        if _cross_origin_write(request):
            return _error(403, "Escrita a partir de outra origem não é permitida.")
        return await call_next(request)

    # Adicionado por último, roda primeiro: Host fora da lista nem chega às rotas.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)

    @app.exception_handler(RecordNotFound)
    async def _not_found(request: Request, exc: RecordNotFound) -> JSONResponse:
        return _error(404, str(exc))

    @app.exception_handler(DuplicateProduct)
    async def _duplicate(request: Request, exc: DuplicateProduct) -> JSONResponse:
        return _error(409, str(exc))

    @app.exception_handler(InvalidTaxonomy)
    async def _invalid_taxonomy(request: Request, exc: InvalidTaxonomy) -> JSONResponse:
        """Par categoria/subcategoria inválido fora do schema (ex.: em PATCH parcial).

        Apenas esta exceção de domínio vira 422; `ValueError` inesperado continua 500.
        """
        return _error(422, str(exc))

    @app.get("/health")
    async def health() -> JSONResponse:
        db: Database = app.state.db
        ok = db.healthy()
        payload = {
            "status": "ok" if ok else "degraded",
            "app": settings.app_name,
            "version": settings.version,
            "schema_version": SCHEMA_VERSION,
            "database": {"path": str(db.path), "ok": ok},
        }
        return JSONResponse(payload, status_code=200 if ok else 503)

    @app.get("/", include_in_schema=False)
    async def dashboard_page() -> FileResponse:
        """Dashboard: página estática que consome apenas as rotas desta API."""
        return FileResponse(settings.frontend_dir / "index.html")

    app.include_router(products.router)
    app.include_router(searches.router)
    app.include_router(complaints.router)
    app.include_router(matches.router)
    app.include_router(dashboard.router)
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    _settings = get_settings()
    uvicorn.run("app.main:app", host=_settings.host, port=_settings.port, reload=True)
