"""Typed settings loaded from `configs/{env}.yaml` and `.env`.

The yaml file holds the canonical defaults. Environment variables prefixed
``POLYMARKET_ARB_`` override any yaml value (so secrets/flags can be injected
at deploy time without committing them).

Precedence (high → low): init kwargs → env vars → .env → YAML → field defaults.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class RateLimitSpec(BaseModel):
    """Token-bucket parameters per host."""

    capacity: float
    refill_per_s: float


class HttpSettings(BaseModel):
    connect_timeout_s: float = 10.0
    read_timeout_s: float = 30.0
    max_retries: int = 3
    backoff_initial_s: float = 0.25
    backoff_max_s: float = 4.0
    rate_limits: dict[str, RateLimitSpec] = Field(default_factory=dict)


class ComplianceSettings(BaseModel):
    allowed_egress_countries: list[str] = Field(default_factory=list)
    ip_check_ttl_s: int = 300


class ParquetSettings(BaseModel):
    compression: str = "zstd"
    row_group_size: int = 50_000


class StorageSettings(BaseModel):
    data_root: Path = Path("data")
    parquet: ParquetSettings = Field(default_factory=ParquetSettings)


class LoggingSettings(BaseModel):
    level: str = "INFO"
    json_log_path: Path = Path("data/logs/polymarket_arb.jsonl")
    rotation: str = "1 day"
    retention: str = "14 days"


class NlpSettings(BaseModel):
    """Phase 1.5+ local-AI settings. Defaults match the WSL-proven Ollama
    HTTP shape (``/api/generate`` + ``/api/embed``)."""

    enabled: bool = False
    provider: Literal["ollama", "mock"] = "mock"
    base_url: str = "http://172.22.16.1:11434"
    llm_model: str = "deepseek-r1:8b"
    embedding_model: str = "nomic-embed-text"
    timeout_s: int = 180
    prompt_version: str = "market_semantics_v2"
    rulebooks: dict[str, str] = Field(default_factory=dict)
    ollama_llm_endpoint: Literal["generate", "chat"] = "generate"
    ollama_embedding_endpoint: Literal["embed", "embeddings"] = "embed"
    # Default off. Setting True dumps pre-strip text to data/debug/nlp_thinking/
    # (gitignored, excluded from every pipeline). Manual debugging only.
    debug_capture_thinking: bool = False


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        loaded = yaml.safe_load(fh) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"Expected mapping at top of {path}, got {type(loaded).__name__}")
    return loaded


class _YamlSource(PydanticBaseSettingsSource):
    """Pydantic-settings source that reads from a yaml file."""

    def __init__(self, settings_cls: type[BaseSettings], yaml_path: Path | None) -> None:
        super().__init__(settings_cls)
        self._data = _load_yaml(yaml_path) if yaml_path else {}

    def get_field_value(self, field, field_name: str):
        if field_name in self._data:
            return self._data[field_name], field_name, False
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        return self._data


# Module-level mutable so load_settings() can pass the chosen yaml path through
# to settings_customise_sources.
_ACTIVE_YAML_PATH: Path | None = None


class Settings(BaseSettings):
    """Top-level settings. See ``configs/dev.yaml`` for defaults."""

    model_config = SettingsConfigDict(
        env_prefix="POLYMARKET_ARB_",
        env_nested_delimiter="__",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: str = "dev"
    gamma_host: str = "https://gamma-api.polymarket.com"
    clob_host: str = "https://clob.polymarket.com"
    limitless_host: str = "https://api.limitless.exchange"
    limitless_paper_mode: bool = True
    http: HttpSettings = Field(default_factory=HttpSettings)
    compliance: ComplianceSettings = Field(default_factory=ComplianceSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    nlp: NlpSettings = Field(default_factory=NlpSettings)
    orders_allowed: bool = False

    # Live agent (Phase 3+).  paper_mode=True is the safe default: the agent
    # runs the strategy + preflight + writes orders_log, but the OrderClient
    # never submits to the network.  Flipping to live trading requires BOTH
    # ``paper_mode=False`` AND ``orders_allowed=True`` AND a Polymarket
    # signing key configured.
    paper_mode: bool = True
    agent_poll_interval_s: int = 10
    agent_max_iterations: int | None = None  # None = run forever; tests set this

    # Polymarket CLOB live-trading credentials.
    # All four are required for live trading (paper_mode=False).
    # Load from AWS Secrets Manager via the CLI; never commit to git.
    polymarket_private_key: str = ""
    polymarket_api_key: str = ""
    polymarket_api_secret: str = ""
    polymarket_api_passphrase: str = ""
    polymarket_funder: str = ""

    # Polymarket CLOB host (override for testnet if needed)
    polymarket_clob_host: str = "https://clob.polymarket.com"
    polymarket_chain_id: int = 137  # Polygon mainnet

    ip_provider_primary: str = "https://api.ipify.org?format=json"
    ip_provider_secondary: str = "https://ifconfig.co/json"

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings,
        env_settings,
        dotenv_settings,
        file_secret_settings,
    ):
        # Priority order, highest first.
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            _YamlSource(settings_cls, _ACTIVE_YAML_PATH),
            file_secret_settings,
        )

    @property
    def data_root(self) -> Path:
        root = self.storage.data_root
        return root if root.is_absolute() else REPO_ROOT / root

    @property
    def killswitch_path(self) -> Path:
        return self.data_root / ".killswitch"

    @property
    def polymarket_credentials_configured(self) -> bool:
        """True only when all four live-trading credentials are non-empty."""
        return all([
            self.polymarket_private_key,
            self.polymarket_api_key,
            self.polymarket_api_secret,
            self.polymarket_api_passphrase,
        ])

    @property
    def nlp_thinking_debug_dir(self) -> Path:
        return self.data_root / "debug" / "nlp_thinking"


def load_settings(env: str | None = None, config_dir: Path | None = None) -> Settings:
    """Load Settings using yaml at ``configs/{env}.yaml`` as low-priority defaults."""

    env_name = env or "dev"
    cfg_dir = config_dir or (REPO_ROOT / "configs")
    yaml_path = cfg_dir / f"{env_name}.yaml"

    global _ACTIVE_YAML_PATH
    _ACTIVE_YAML_PATH = yaml_path if yaml_path.exists() else None
    try:
        return Settings()
    finally:
        _ACTIVE_YAML_PATH = None
