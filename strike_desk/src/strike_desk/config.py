"""Typed, environment-driven configuration for the Strike Desk service."""

from __future__ import annotations

from datetime import time
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

IST = ZoneInfo("Asia/Kolkata")

# Project root is two levels above this file: strike_desk/src/strike_desk/config.py
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_ENV_FILES = (
    _PROJECT_ROOT / ".env",
    Path(".env"),  # also honour a cwd-relative .env when present
)

TimeWindow = tuple[time, time]


def parse_windows(raw: str) -> tuple[TimeWindow, ...]:
    """Parse ``"09:15-09:30,15:15-15:30"`` into ordered (start, end) time pairs."""
    windows: list[TimeWindow] = []
    for chunk in (piece.strip() for piece in raw.split(",")):
        if not chunk:
            continue
        start_raw, separator, end_raw = chunk.partition("-")
        if not separator:
            raise ValueError(f"window {chunk!r} must look like HH:MM-HH:MM")
        start = time.fromisoformat(start_raw.strip())
        end = time.fromisoformat(end_raw.strip())
        if start >= end:
            raise ValueError(f"window {chunk!r} must start before it ends")
        windows.append((start, end))
    return tuple(windows)


class Settings(BaseSettings):
    """All runtime configuration, read from ``STRIKE_DESK_*`` environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="STRIKE_DESK_",
        env_file=_ENV_FILES,
        env_file_encoding="utf-8",
        extra="ignore",
        protected_namespaces=(),
    )

    # --- OpenAlgo substrate -------------------------------------------------
    openalgo_base_url: str = "http://127.0.0.1:5000"
    openalgo_api_key: SecretStr
    openalgo_timeout_seconds: float = Field(default=5.0, gt=0, le=60)
    openalgo_retries: int = Field(default=2, ge=0, le=5)

    # --- Book ---------------------------------------------------------------
    index_symbol: str = "NIFTY"
    option_exchange: str = "NFO"
    index_spot_exchange: str = "NSE_INDEX"
    vix_symbol: str = "INDIAVIX"

    # --- Tick cadence and budgets ------------------------------------------
    tick_interval_seconds: int = Field(default=900, ge=30, le=3600)
    tick_budget_seconds: float = Field(default=40.0, gt=0, le=120)
    specialist_timeout_seconds: float = Field(default=25.0, gt=0, le=60)

    # --- Session gates ------------------------------------------------------
    no_trade_windows: str = "09:15-09:30,15:15-15:30"
    expiry_cutoff: str = "14:00"
    expiry_weekday: int = Field(default=1, ge=0, le=6)  # 0 = Monday; NIFTY weeklies expire Tuesday

    # --- Supervisor policy --------------------------------------------------
    min_regime_confidence: float = Field(default=0.55, ge=0.0, le=1.0)

    # --- Regime Analyst (reasoning plane) -----------------------------------
    anthropic_api_key: SecretStr | None = None
    regime_model: str = "claude-haiku-4-5"
    regime_temperature: float = Field(default=0.0, ge=0.0, le=1.0)
    regime_max_output_tokens: int = Field(default=1200, ge=256, le=8192)
    regime_max_rounds: int = Field(default=4, ge=1, le=8)
    regime_deadline_margin_seconds: float = Field(default=3.0, ge=0.5, le=15.0)
    regime_tool_output_chars: int = Field(default=8000, ge=1000, le=60000)
    regime_rationale_max_chars: int = Field(default=320, ge=80, le=1000)
    price_in_per_mtok: float = Field(default=1.0, ge=0.0, le=1000.0)
    price_out_per_mtok: float = Field(default=5.0, ge=0.0, le=1000.0)

    # --- MCP toolbox --------------------------------------------------------
    mcp_python: Path = Path("/opt/openalgo/.venv/bin/python")
    mcp_server_script: Path = Path("/opt/openalgo/mcp/mcpserver.py")
    mcp_startup_timeout_seconds: float = Field(default=45.0, gt=0, le=180)

    # --- Paths --------------------------------------------------------------
    state_dir: Path = Path("/var/lib/strike-desk")
    prompts_dir: Path = Field(default_factory=lambda: Path(__file__).parent / "prompts")

    # --- StrikeShield (QuadShield mind → Tradetron hands) -------------------
    strikeshield_enabled: bool = False
    strikeshield_interval_seconds: int = Field(default=15, ge=5, le=120)
    strikeshield_adx_entry: float = Field(default=40.0, gt=0, le=100)
    strikeshield_adx_exit: float = Field(default=45.0, gt=0, le=100)
    strikeshield_vix_min: float = Field(default=11.0, ge=0, le=100)
    strikeshield_vix_max: float = Field(default=40.0, gt=0, le=100)
    strikeshield_atr_period: int = Field(default=14, ge=2, le=50)
    strikeshield_adx_period: int = Field(default=15, ge=2, le=50)
    strikeshield_spot_atr_mult: float = Field(default=3.5, gt=0, le=20)
    strikeshield_min_spot_move: float = Field(default=180.0, gt=0, le=2000)
    strikeshield_atr_spike: float = Field(default=1.5, gt=1.0, le=5)
    strikeshield_first_min_move: float = Field(default=50.0, gt=0, le=500)
    strikeshield_max_adjustments_per_day: int = Field(default=1, ge=0, le=20)
    strikeshield_short_cooldown_seconds: int = Field(default=60, ge=0, le=3600)
    tt_auth_token: SecretStr | None = None
    tt_auth_token_hands: SecretStr | None = None
    tt_auth_token_overlay: SecretStr | None = None
    tt_auth_token_bn: SecretStr | None = None
    tt_auth_token_coa: SecretStr | None = None
    tt_auth_token_ofa: SecretStr | None = None
    tt_webhook_url: str = "https://api.tradetron.tech/api?"
    tt_hands: bool = False
    coa_interval_seconds: int = Field(default=300, ge=60, le=900)
    tt_map: str = ""
    slots: str = ""

    # --- OFA (mind + Analyzer fill + Tradetron Hands) -----------------------
    ofa_symbol: str = ""
    ofa_exchange: str = "NFO"
    ofa_tick: float = Field(default=0.05, gt=0, le=10)
    ofa_wall_mult: float = Field(default=3.0, gt=1.0, le=20)
    ofa_thin_total: float = Field(default=80.0, ge=0, le=1_000_000)
    ofa_interval_seconds: int = Field(default=5, ge=1, le=60)
    ofa_lots: int = Field(default=1, ge=1, le=20)
    ofa_product: str = "MIS"

    # --- Observability ------------------------------------------------------
    service_name: str = "strike-desk"
    environment: str = "practice"
    log_level: str = "INFO"
    otlp_endpoint: str | None = None
    otlp_headers: SecretStr | None = None

    @field_validator("no_trade_windows")
    @classmethod
    def _validate_windows(cls, value: str) -> str:
        parse_windows(value)
        return value

    @field_validator("expiry_cutoff")
    @classmethod
    def _validate_cutoff(cls, value: str) -> str:
        time.fromisoformat(value)
        return value

    @field_validator("environment")
    @classmethod
    def _validate_environment(cls, value: str) -> str:
        if value not in {"practice", "production"}:
            raise ValueError("environment must be 'practice' or 'production'")
        return value

    @property
    def no_trade_window_times(self) -> tuple[TimeWindow, ...]:
        return parse_windows(self.no_trade_windows)

    @property
    def expiry_cutoff_time(self) -> time:
        return time.fromisoformat(self.expiry_cutoff)

    @property
    def analyst_deadline_seconds(self) -> float:
        """The analyst's own budget, always under the registry's timeout."""
        return max(1.0, self.specialist_timeout_seconds - self.regime_deadline_margin_seconds)

    @property
    def db_path(self) -> Path:
        return self.state_dir / "strike_desk.db"

    @property
    def checkpoint_path(self) -> Path:
        return self.state_dir / "checkpoints.sqlite"

    @property
    def kill_switch_path(self) -> Path:
        return self.state_dir / "KILL"

    @property
    def heartbeat_path(self) -> Path:
        return self.state_dir / "heartbeat"

    @property
    def pid_path(self) -> Path:
        return self.state_dir / "strike-desk.pid"

    @property
    def events_path(self) -> Path:
        return self.state_dir / "events.json"

    @property
    def strikeshield_state_path(self) -> Path:
        return self.state_dir / "strikeshield.json"

    @property
    def strikeshield_heartbeat_path(self) -> Path:
        return self.state_dir / "strikeshield.heartbeat"

    @property
    def public_brief_path(self) -> Path:
        return self.state_dir / "public-brief.json"

    @property
    def ofa_state_path(self) -> Path:
        return self.state_dir / "ofa.json"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings singleton."""
    return Settings()  # type: ignore[call-arg]
