"""
Coleta de reclamações no Reclame Aqui.

Duas camadas com propósitos separados:

- navegação (Playwright): abre a busca, pagina e captura a resposta JSON que a própria
  página recebe do endpoint de busca;
- determinística: valida os campos, monta a URL pública e produz a reclamação.

A camada determinística não depende de rede nem de navegador — é ela que os testes
cobrem.

A lista de resultados não existe no HTML da busca: a página consulta
`iosearch.reclameaqui.com.br/raichu-io-site-search-v1/query/{termo}/{tamanho}/{offset}`
e monta tudo no cliente. Chamar esse endereço fora da sessão do navegador devolve 403,
então a coleta captura a resposta que a própria navegação provoca (D-011).

Regra inegociável: registro sem `companyShortname` e `url` é descartado e contado como
falha. Nenhuma URL é sintetizada.
"""

from __future__ import annotations

import asyncio
import re
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from html import unescape
from time import monotonic
from typing import Any, Protocol
from urllib.parse import quote_plus, urlsplit

from app.models import ComplaintCreate

SOURCE = "reclame_aqui"
BASE_URL = "https://www.reclameaqui.com.br"
SEARCH_URL = BASE_URL + "/busca/?q={query}&pagina={page}"

# Endpoint que a página de busca consulta. Não é chamado diretamente: a navegação o
# provoca e a resposta é capturada da própria sessão.
SEARCH_API_HOST = "iosearch.reclameaqui.com.br"
SEARCH_API_PATH = "/raichu-io-site-search-v1/query/"

# Tamanho de página observado na própria aplicação; é ele que define o `offset`.
PAGE_SIZE = 10

# Só aceitamos link cuja origem é verificável como sendo do próprio Reclame Aqui.
ALLOWED_HOSTS = frozenset({"reclameaqui.com.br", "www.reclameaqui.com.br"})

# Segmento de caminho vindo da fonte: barra, espaço ou esquema indicam valor que não é
# um slug e não pode ser concatenado na URL.
_UNSAFE_SEGMENT = re.compile(r"[\s/\\?#:]")

_TAG = re.compile(r"<[^>]+>")

# `created` vem em ISO no horário local do site; os formatos brasileiros continuam
# aceitos porque a fonte já mostrou variação de formato entre telas.
_DATE_FORMATS = ("%d/%m/%Y %H:%M", "%d/%m/%Y %H:%M:%S", "%d/%m/%Y")

# Comprimento da prévia que a fonte devolve em `description`. Texto que para exatamente
# nesse tamanho, sem versão completa ao lado, é prévia cortada na origem — não uma
# reclamação curta.
PREVIEW_LENGTH = 130

MAX_TITLE = 300
# Limite do texto gravado. Folgado de propósito: a maior reclamação observada tem ~2,4 mil
# caracteres, então o corte é caso extremo — e, quando ocorre, vira nota da execução.
MAX_TEXT = 20_000
MAX_SHORT = 200


class ItemDiscarded(ValueError):
    """Registro bruto que não vira reclamação rastreável."""


@dataclass(frozen=True)
class ParsedItem:
    """Reclamação válida mais a posição em que a busca a encontrou."""

    complaint: ComplaintCreate
    page: int
    position: int


@dataclass
class FetchResult:
    """Saída bruta da navegação, antes de qualquer validação."""

    items: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    failures: int = 0


@dataclass
class ParseResult:
    """Resultado da camada determinística.

    `discarded` e `notes` são canais distintos de propósito: descarte é falha (registro
    que não virou reclamação), nota é observação sobre um registro que foi gravado —
    tipicamente perda de fidelidade do texto. Misturar os dois inflaria `failures`.
    """

    items: list[ParsedItem] = field(default_factory=list)
    discarded: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


class Collector(Protocol):
    """Contrato que o serviço de coleta consome (permite coletor falso em teste)."""

    source: str

    async def fetch(self, term: str, pages: int) -> FetchResult: ...


# ------------------------------------------------------------------ determinístico


def search_url(term: str, page: int) -> str:
    return SEARCH_URL.format(query=quote_plus(term), page=page)


def is_search_api(url: str) -> bool:
    """A resposta veio do endpoint de busca que a página consulta."""
    parts = urlsplit(url)
    return (parts.hostname or "").lower() == SEARCH_API_HOST and SEARCH_API_PATH in parts.path


def api_offset(url: str) -> int | None:
    """Offset pedido na chamada capturada (`.../query/{termo}/{tamanho}/{offset}`)."""
    tail = urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]
    return int(tail) if tail.isdigit() else None


def canonical_url(href: str | None) -> str | None:
    """Forma canônica da URL da reclamação, ou `None` se o link não for utilizável.

    Descarta query e fragmento (rastreamento não faz parte da identidade) e exige
    host do Reclame Aqui. Nunca completa informação ausente.
    """
    raw = (href or "").strip()
    if not raw:
        return None
    if raw.startswith("//"):
        raw = "https:" + raw
    elif raw.startswith("/"):
        raw = BASE_URL + raw
    parts = urlsplit(raw)
    if parts.scheme not in ("http", "https"):
        return None
    host = (parts.hostname or "").lower()
    if host not in ALLOWED_HOSTS:
        return None
    path = parts.path.rstrip("/")
    if not path:
        return None
    return f"https://{host}{path}"


def complaint_url(company_shortname: Any, slug: Any) -> str | None:
    """URL pública da reclamação: `/{companyShortname}/{url}/`.

    Os dois campos vêm da fonte e nenhum é deduzido: faltando qualquer um, ou vindo
    com conteúdo que não é um segmento de caminho, o registro não tem link real.
    """
    company = str(company_shortname or "").strip().strip("/")
    slug_path = str(slug or "").strip().strip("/")
    if not company or not slug_path:
        return None
    if _UNSAFE_SEGMENT.search(company) or _UNSAFE_SEGMENT.search(slug_path):
        return None
    return canonical_url(f"{BASE_URL}/{company}/{slug_path}/")


def dedupe_key(source: str, url: str, complaint_id: str | None) -> str:
    """Identidade da reclamação: id da fonte quando existe, senão a URL canônica."""
    return f"{source}|{complaint_id or url}"


def records_from_payload(payload: Any) -> tuple[list[Mapping[str, Any]], int | None]:
    """Reclamações e total declarado dentro da resposta do endpoint de busca.

    Formato diferente do esperado devolve lista vazia — quem chama transforma isso em
    falha observável, não em silêncio.
    """
    if not isinstance(payload, Mapping):
        return [], None
    complains = payload.get("complainResult")
    complains = complains.get("complains") if isinstance(complains, Mapping) else None
    if not isinstance(complains, Mapping):
        return [], None
    data = complains.get("data")
    count = complains.get("count")
    records = [r for r in data if isinstance(r, Mapping)] if isinstance(data, list) else []
    return records, count if isinstance(count, int) else None


def clean_html(value: Any) -> str:
    """Texto sem o HTML embutido da fonte (`<br/>` e afins), sem alterar o conteúdo."""
    return unescape(_TAG.sub(" ", str(value or "")))


def is_masked(raw: Mapping[str, Any]) -> bool:
    """A fonte declara que este registro teve dado pessoal substituído por `****`."""
    return str(raw.get("maskingStatus") or "").upper() == "MASKED"


def complaint_text(raw: Mapping[str, Any]) -> tuple[str | None, list[str]]:
    """Texto da reclamação e o que se perdeu dele pelo caminho.

    `description` vem cortado em `PREVIEW_LENGTH` caracteres; `descriptionMasked` traz o
    mesmo texto completo, com dados pessoais substituídos por `****` pela própria fonte.
    Registro mascarado usa a versão mascarada — é mais fiel à reclamação e não
    reintroduz dado pessoal. Os dois continuam preservados em `raw`.

    Quando só resta a prévia, ou quando o texto passa de `MAX_TEXT`, isso vira nota:
    conteúdo incompleto gravado em silêncio é o que se quer evitar.
    """
    preview = str(raw.get("description") or "")
    masked = str(raw.get("descriptionMasked") or "")
    if (is_masked(raw) and masked) or len(masked) > len(preview):
        chosen, from_preview = masked, False
    else:
        chosen, from_preview = preview, True

    notes: list[str] = []
    if from_preview and len(preview) >= PREVIEW_LENGTH:
        notes.append(
            f"texto disponível apenas na prévia de {PREVIEW_LENGTH} caracteres "
            "(registro sem versão completa)"
        )
    # O corte é medido no texto inteiro, não no já cortado: `_clean` devolveria um
    # comprimento menor que `MAX_TEXT` quando o limite cai em cima de um espaço.
    full = " ".join(clean_html(chosen).split())
    if len(full) > MAX_TEXT:
        notes.append(f"texto cortado em {MAX_TEXT} caracteres; original preservado em `raw`")
    return _clean(full, MAX_TEXT), notes


def complaint_title(raw: Mapping[str, Any]) -> str | None:
    """Título da reclamação, pela mesma regra do texto: mascarado vence quando existe."""
    masked = raw.get("titleMasked")
    chosen = masked if is_masked(raw) and masked else (raw.get("title") or masked)
    return _clean(clean_html(chosen), MAX_TITLE)


def complaint_location(raw: Mapping[str, Any]) -> str | None:
    """Localização declarada pelo usuário: `Cidade - UF`, com o que existir."""
    city = _clean(raw.get("userCity"), MAX_SHORT)
    state = _clean(raw.get("userState"), MAX_SHORT)
    if city and state:
        return f"{city} - {state}"
    return city or state


def parse_published(text: str | None) -> datetime | None:
    """Data de publicação informada pela fonte, em horário local do site.

    Sem texto reconhecível, devolve `None` — o texto original continua preservado
    em `published_text`.
    """
    raw = " ".join((text or "").split())
    if not raw:
        return None
    cleaned = raw.replace(" às ", " ").replace(" as ", " ")
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(cleaned.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _clean(value: Any, limit: int) -> str | None:
    text = " ".join(str(value or "").split())[:limit].strip()
    return text or None


def build_complaint(
    raw: Mapping[str, Any], *, source: str = SOURCE, notes: list[str] | None = None
) -> ComplaintCreate:
    """Monta a reclamação a partir do registro JSON; levanta `ItemDiscarded` se não der.

    `notes`, quando fornecida, recebe o que se perdeu do conteúdo original — o registro
    é gravado assim mesmo, mas a perda fica visível na execução.
    """
    url = complaint_url(raw.get("companyShortname"), raw.get("url"))
    if url is None:
        raise ItemDiscarded(
            "registro sem link real: "
            f"companyShortname={raw.get('companyShortname')!r} url={raw.get('url')!r}"
        )

    title = complaint_title(raw)
    if title is None or len(title) < 3:
        raise ItemDiscarded(f"registro sem título utilizável: {url}")

    description, text_notes = complaint_text(raw)
    if notes is not None:
        notes.extend(f"{url}: {note}" for note in text_notes)

    published_text = _clean(raw.get("created"), MAX_SHORT)
    return ComplaintCreate(
        source=source,
        external_id=_clean(raw.get("id"), MAX_SHORT),
        url=url,
        title=title,
        description=description,
        company=_clean(raw.get("companyName") or raw.get("companyShortname"), MAX_SHORT),
        location=complaint_location(raw),
        status=_clean(raw.get("status"), MAX_SHORT),
        published_at=parse_published(published_text),
        published_text=published_text,
        raw=dict(raw),
    )


def parse_items(
    raw_items: Iterable[Mapping[str, Any]], *, source: str = SOURCE
) -> ParseResult:
    """Converte registros brutos em reclamações.

    Devolve, além das reclamações, o motivo de cada descarte (falha) e as observações
    sobre o conteúdo gravado (não são falha).
    """
    result = ParseResult()
    for index, raw in enumerate(raw_items):
        try:
            complaint = build_complaint(raw, source=source, notes=result.notes)
        except (ItemDiscarded, ValueError) as exc:
            result.discarded.append(str(exc))
            continue
        position = raw.get("position")
        result.items.append(
            ParsedItem(
                complaint=complaint,
                page=int(raw.get("page") or 1),
                position=int(position) if position is not None else index,
            )
        )
    return result


# -------------------------------------------------------------------- navegação

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


class NavigationBlocked(Exception):
    """A proteção do site respondeu ao documento em vez da página de busca."""

    def __init__(self, status: int):
        super().__init__(f"navegação bloqueada pela proteção do site (status {status})")
        self.status = status


class ReclameAquiCollector:
    """Navegação real com Playwright.

    Para cada página de resultados, navega a busca e espera a resposta do endpoint que
    a própria página consulta; a paginação segue o `offset` desse endpoint, limitada
    pelo `count` que a fonte declara. A página individual de cada reclamação não é
    aberta.

    Limite conhecido e verificado em 17/09/2026: só a **primeira** navegação de uma
    sessão passa. A segunda recebe o desafio do Cloudflare (403, "Um momento…") e o
    documento nem chega a consultar o endpoint — inclusive com 60 s de intervalo. Não há
    contorno dentro do que é permitido aqui, então a paginação para no bloqueio, com
    nota, em vez de insistir. Na prática, hoje, uma execução entrega a primeira página
    de resultados por termo (D-011).

    Playwright é dependência opcional (`pip install -e .[collect]`) e só é importado
    quando uma coleta de fato acontece.
    """

    source = SOURCE

    def __init__(
        self,
        *,
        headless: bool = True,
        nav_timeout_ms: int = 30_000,
        response_timeout_ms: int = 25_000,
        interval_ms: int = 4_000,
        page_size: int = PAGE_SIZE,
    ):
        self.headless = headless
        self.nav_timeout_ms = nav_timeout_ms
        self.response_timeout_ms = response_timeout_ms
        self.interval_ms = interval_ms
        self.page_size = page_size
        # Momento da última requisição ao site, guardado na instância: o ritmo precisa
        # valer entre execuções (uma por termo), não só entre páginas de uma execução.
        self._last_request_at: float | None = None

    async def _pace(self) -> None:
        """Segura a próxima requisição até completar o intervalo desde a anterior."""
        if self._last_request_at is not None:
            remaining = self.interval_ms / 1000 - (monotonic() - self._last_request_at)
            if remaining > 0:
                await asyncio.sleep(remaining)
        self._last_request_at = monotonic()

    async def fetch(self, term: str, pages: int) -> FetchResult:
        if sys.platform == "win32":
            # O navegador é aberto como subprocesso, e o loop `Selector` — que o uvicorn
            # usa no Windows quando roda com `--reload` — não suporta subprocesso. Rodar
            # em um loop `Proactor` próprio, em outra thread, deixa a coleta funcionando
            # independentemente de como o servidor foi iniciado.
            return await asyncio.to_thread(self._fetch_on_proactor_loop, term, pages)
        return await self._fetch(term, pages)

    def _fetch_on_proactor_loop(self, term: str, pages: int) -> FetchResult:
        with asyncio.Runner(loop_factory=asyncio.ProactorEventLoop) as runner:
            return runner.run(self._fetch(term, pages))

    async def _fetch(self, term: str, pages: int) -> FetchResult:
        from playwright.async_api import async_playwright

        result = FetchResult()
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=self.headless,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                ],
            )
            try:
                context = await browser.new_context(
                    user_agent=_USER_AGENT,
                    viewport={"width": 1366, "height": 768},
                    locale="pt-BR",
                    timezone_id="America/Sao_Paulo",
                    extra_http_headers={"Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8"},
                )
                page = await context.new_page()
                total: int | None = None
                for page_num in range(1, pages + 1):
                    offset = (page_num - 1) * self.page_size
                    if total is not None and offset >= total:
                        result.notes.append(
                            f"página {page_num}: além do total informado pela fonte ({total})"
                        )
                        break
                    count, blocked = await self._collect_page(
                        page, term, page_num, offset, result
                    )
                    # O intervalo conta a partir do fim da requisição anterior.
                    self._last_request_at = monotonic()
                    if blocked:
                        # Insistir depois do desafio só gera navegação barrada.
                        break
                    if count is not None:
                        total = count
            finally:
                await browser.close()
        return result

    async def _collect_page(
        self, page, term: str, page_num: int, offset: int, result: FetchResult
    ) -> tuple[int | None, bool]:
        """Uma página de resultados.

        Devolve o total declarado pela fonte, quando veio, e se a proteção do site
        barrou a navegação — caso em que continuar paginando é inútil. Erro aqui é
        contado e anotado, não interrompe a busca.
        """
        url = search_url(term, page_num)
        # Ritmo entre requisições: sem ele a navegação seguinte é barrada pelo Cloudflare.
        await self._pace()
        try:
            async with page.expect_response(
                lambda response: is_search_api(response.url),
                timeout=self.response_timeout_ms,
            ) as caught:
                navigation = await page.goto(
                    url, wait_until="domcontentloaded", timeout=self.nav_timeout_ms
                )
                if navigation is not None and not navigation.ok:
                    # Falhar aqui dentro evita esperar o timeout de uma resposta que a
                    # página bloqueada nunca vai pedir.
                    raise NavigationBlocked(navigation.status)
            response = await caught.value
            if not response.ok:
                result.failures += 1
                result.notes.append(
                    f"página {page_num}: busca respondeu {response.status} ({response.url})"
                )
                return None, False
            payload = await response.json()
        except NavigationBlocked as exc:
            result.failures += 1
            result.notes.append(f"página {page_num}: {exc}; paginação interrompida")
            return None, True
        except Exception as exc:  # timeout, navegação ou resposta ilegível
            result.failures += 1
            result.notes.append(
                f"página {page_num}: falha ao capturar a busca ({type(exc).__name__}: {exc})"
            )
            return None, False

        records, count = records_from_payload(payload)
        captured = api_offset(response.url)
        if captured is not None and captured != offset:
            result.notes.append(f"página {page_num}: offset capturado {captured}, esperado {offset}")
            offset = captured
        if not records:
            result.failures += 1
            result.notes.append(
                f"página {page_num}: resposta da busca sem reclamações — verificar formato"
            )
            return count, False

        for index, record in enumerate(records):
            result.items.append(
                {
                    **record,
                    "page": page_num,
                    "position": offset + index,
                    "search_url": url,
                    "api_url": response.url,
                }
            )
        return count, False
