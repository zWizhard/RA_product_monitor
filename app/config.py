"""
Configuração da aplicação.

Todos os caminhos são previsíveis: resolvidos a partir da raiz do projeto,
nunca do diretório de trabalho atual. Sobrescrita por variáveis de ambiente
com prefixo `RAPM_` ou pelo arquivo `.env`.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="RAPM_",
        env_file=PROJECT_ROOT / ".env",
        extra="ignore",
    )

    app_name: str = "RA Product Monitor"
    version: str = "0.1.0"
    data_dir: Path = PROJECT_ROOT / "data"
    frontend_dir: Path = PROJECT_ROOT / "frontend"
    db_filename: str = "ra_product_monitor.duckdb"
    host: str = "127.0.0.1"
    port: int = 8000
    # Sem autenticação, a API só atende a própria máquina: o cabeçalho `Host` precisa ser
    # um destes nomes. Abrir para a rede exige autenticação antes de ampliar a lista.
    allowed_hosts: list[str] = ["127.0.0.1", "localhost"]

    @field_validator("data_dir", "frontend_dir")
    @classmethod
    def _resolve_dir(cls, value: Path) -> Path:
        """Caminho relativo é resolvido contra a raiz do projeto, não contra o cwd."""
        return value if value.is_absolute() else (PROJECT_ROOT / value).resolve()

    @property
    def db_path(self) -> Path:
        return self.data_dir / self.db_filename


@lru_cache
def get_settings() -> Settings:
    return Settings()
