"""Central configuration. All secrets come from .env — never hardcoded."""
from __future__ import annotations

from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Groq
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"

    # Angel One SmartAPI
    smartapi_api_key: str = ""
    smartapi_client_id: str = ""
    smartapi_password: str = ""
    smartapi_totp_secret: str = ""
    smartapi_rate_limit_rps: float = 3.0

    # paths
    data_dir: Path = Path("data")
    artifact_path: Path = Path("data/artifacts/fundxray.duckdb")

    # analytical assumptions (surfaced to users, never hidden)
    dtl_participation_rate: float = 0.20
    reconciliation_tolerance_pct: float = 0.5

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def staging_dir(self) -> Path:
        return self.data_dir / "staging"

    @property
    def warehouse_dir(self) -> Path:
        return self.data_dir / "warehouse"

    def ensure_dirs(self) -> None:
        for p in (self.raw_dir, self.staging_dir, self.warehouse_dir,
                  self.artifact_path.parent):
            p.mkdir(parents=True, exist_ok=True)

    @property
    def groq_enabled(self) -> bool:
        return bool(self.groq_api_key)

    @property
    def smartapi_enabled(self) -> bool:
        return bool(self.smartapi_api_key and self.smartapi_client_id)


settings = Settings()
