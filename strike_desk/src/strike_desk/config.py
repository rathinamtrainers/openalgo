"""Typed, environment-driven configuration for the Strike Desk service."""

from __future__ import annotations

from datetime import time
from functools import lru_cache
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import Field, SecretStr, field_validator, model_validator
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
        extra="forbid",
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
    tick_budget_seconds: float = Field(default=90.0, gt=0, le=300)
    specialist_timeout_seconds: float = Field(default=25.0, gt=0, le=60)
    strategist_timeout_seconds: float = Field(default=35.0, gt=0, le=120)

    # --- Session gates ------------------------------------------------------
    no_trade_windows: str = "09:15-09:30,15:15-15:30"
    expiry_cutoff: str = "14:00"
    expiry_weekday: int = Field(default=1, ge=0, le=6)  # 0 = Monday; NIFTY weeklies expire Tuesday

    # --- Supervisor policy --------------------------------------------------
    min_regime_confidence: float = Field(default=0.55, ge=0.0, le=1.0)
    directional_regimes: str = "trending"
    reason_text_max_chars: int = Field(default=400, ge=120, le=1000)

    # --- Reporting ----------------------------------------------------------
    report_default_days: int = Field(default=5, ge=1, le=60)

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

    # --- Options Strategist (deliberation plane) ----------------------------
    strategist_model: str = "claude-sonnet-5"
    strategist_temperature: float = Field(default=0.0, ge=0.0, le=1.0)
    strategist_max_output_tokens: int = Field(default=2400, ge=512, le=16384)
    strategist_max_rounds: int = Field(default=6, ge=2, le=12)
    strategist_deadline_margin_seconds: float = Field(default=4.0, ge=0.5, le=20.0)
    strategist_tool_output_chars: int = Field(default=16000, ge=2000, le=80000)
    proposal_rationale_max_chars: int = Field(default=700, ge=200, le=2000)
    strategist_price_in_per_mtok: float = Field(default=3.0, ge=0.0, le=1000.0)
    strategist_price_out_per_mtok: float = Field(default=15.0, ge=0.0, le=1000.0)

    # --- Playbook -----------------------------------------------------------
    playbook_delta_min: float = Field(default=0.35, gt=0.0, lt=1.0)
    playbook_delta_max: float = Field(default=0.60, gt=0.0, le=1.0)
    playbook_max_spread_pct: float = Field(default=1.5, gt=0.0, le=25.0)
    playbook_min_open_interest: int = Field(default=50_000, ge=0)
    playbook_iv_floor: float = Field(default=8.0, ge=0.0, le=200.0)
    playbook_iv_ceiling: float = Field(default=35.0, ge=0.0, le=500.0)
    playbook_max_lots: int = Field(default=2, ge=1, le=20)
    playbook_min_days_to_expiry: int = Field(default=1, ge=0, le=60)
    playbook_max_days_to_expiry: int = Field(default=10, ge=1, le=120)
    playbook_theta_budget_rupees: float = Field(default=1500.0, gt=0.0, le=1_000_000.0)
    playbook_time_stop: str = "15:00"

    # --- Risk Officer (control plane) ---------------------------------------
    risk_daily_loss_cap_pct: float = Field(default=2.0, gt=0.0, le=100.0)
    risk_per_trade_loss_cap_pct: float = Field(default=0.5, gt=0.0, le=100.0)
    risk_deployed_capital_pct: float = Field(default=10.0, gt=0.0, le=100.0)
    risk_per_index_exposure_pct: float = Field(default=10.0, gt=0.0, le=100.0)
    risk_max_concurrent_positions: int = Field(default=1, ge=1, le=10)
    risk_max_lots: int = Field(default=2, ge=1, le=20)
    risk_max_trades_per_day: int = Field(default=3, ge=1, le=50)
    risk_capital_floor: float = Field(default=50_000.0, ge=0.0, le=100_000_000.0)

    # --- Execution and the approval gate ------------------------------------
    execution_enabled: bool = False
    openalgo_user: str | None = None
    openalgo_db_path: Path = Path("/opt/openalgo/db/openalgo.db")
    order_product: str = "MIS"
    order_strategy_prefix: str = "strike-desk"
    price_tick: float = Field(default=0.05, gt=0, le=100)
    approval_deadline_seconds: int = Field(default=300, ge=30, le=1800)
    approval_poll_seconds: int = Field(default=5, ge=1, le=60)
    fill_deadline_seconds: int = Field(default=300, ge=30, le=3600)

    # --- The position monitor ------------------------------------------------
    monitor_enabled: bool = True
    ws_url: str = "ws://127.0.0.1:8765"
    ws_open_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    monitor_poll_seconds: float = Field(default=1.0, gt=0, le=10)
    quote_poll_seconds: float = Field(default=2.0, gt=0, le=30)
    feed_stale_seconds: float = Field(default=15.0, gt=0, le=300)
    feed_blackout_seconds: float = Field(default=90.0, gt=0, le=1800)
    reconcile_interval_seconds: float = Field(default=30.0, gt=0, le=600)
    session_exit_deadline: str = "15:10"
    exit_latency_budget_ms: int = Field(default=1500, ge=100, le=30000)
    exit_max_attempts: int = Field(default=3, ge=1, le=10)
    exit_retry_seconds: float = Field(default=2.0, gt=0, le=60)

    # --- Autonomy (iteration 07) --------------------------------------------
    autonomy: Literal["attended", "unattended"] = "unattended"
    monitor_heartbeat_max_age_seconds: float = Field(default=30.0, gt=0, le=600)
    unattended_daily_loss_cap: float = Field(default=6000.0, gt=0)
    unattended_max_trades_per_day: int = Field(default=3, ge=1, le=20)

    # --- MCP toolbox --------------------------------------------------------
    mcp_python: Path = Path("/opt/openalgo/.venv/bin/python")
    mcp_server_script: Path = Path("/opt/openalgo/mcp/mcpserver.py")
    mcp_startup_timeout_seconds: float = Field(default=45.0, gt=0, le=180)

    # --- Paths --------------------------------------------------------------
    state_dir: Path = Path("/var/lib/strike-desk")
    prompts_dir: Path = Field(default_factory=lambda: Path(__file__).parent / "prompts")

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

    @field_validator("directional_regimes")
    @classmethod
    def _validate_directional(cls, value: str) -> str:
        from .grounding import TRADEABLE_LABELS

        labels = {piece.strip() for piece in value.split(",") if piece.strip()}
        if not labels:
            raise ValueError("directional_regimes must name at least one regime")
        unknown = sorted(labels - set(TRADEABLE_LABELS))
        if unknown:
            raise ValueError(f"not tradeable regimes: {', '.join(unknown)}")
        return value

    @field_validator("playbook_time_stop")
    @classmethod
    def _validate_time_stop(cls, value: str) -> str:
        time.fromisoformat(value)
        return value

    @field_validator("session_exit_deadline")
    @classmethod
    def _validate_exit_deadline(cls, value: str) -> str:
        time.fromisoformat(value)
        return value

    @field_validator("ws_url")
    @classmethod
    def _validate_ws_url(cls, value: str) -> str:
        if not value.startswith(("ws://", "wss://")):
            raise ValueError("ws_url must start with ws:// or wss://")
        return value

    @field_validator("order_product")
    @classmethod
    def _validate_product(cls, value: str) -> str:
        if value not in {"MIS", "NRML", "CNC"}:
            raise ValueError("order_product must be one of MIS, NRML, CNC")
        return value

    @field_validator("order_strategy_prefix")
    @classmethod
    def _validate_strategy_prefix(cls, value: str) -> str:
        if not value or len(value) > 15 or not all(ch.isalnum() or ch in "-_" for ch in value):
            raise ValueError("order_strategy_prefix must be 1-15 chars of [A-Za-z0-9_-]")
        return value

    @model_validator(mode="after")
    def _execution_needs_a_user(self) -> Settings:
        """A desk that may place orders must know whose approval queue it is writing into."""
        if self.execution_enabled and not (self.openalgo_user or "").strip():
            raise ValueError(
                "STRIKE_DESK_EXECUTION_ENABLED=true requires STRIKE_DESK_OPENALGO_USER"
            )
        return self

    @model_validator(mode="after")
    def _budgets_fit(self) -> Settings:
        """A tick must be able to hold both specialists, or every good read ends in a
        tick-timeout that looks like an infrastructure problem and is not."""
        if self.playbook_delta_min >= self.playbook_delta_max:
            raise ValueError("playbook_delta_min must be below playbook_delta_max")
        if self.playbook_iv_floor >= self.playbook_iv_ceiling:
            raise ValueError("playbook_iv_floor must be below playbook_iv_ceiling")
        if self.playbook_min_days_to_expiry > self.playbook_max_days_to_expiry:
            raise ValueError("playbook_min_days_to_expiry must not exceed the maximum")
        needed = self.specialist_timeout_seconds + self.strategist_timeout_seconds
        if needed >= self.tick_budget_seconds:
            raise ValueError(
                f"specialist timeouts total {needed:.0f}s, which does not fit inside the "
                f"{self.tick_budget_seconds:.0f}s tick budget"
            )
        if self.risk_per_trade_loss_cap_pct > self.risk_daily_loss_cap_pct:
            raise ValueError(
                "risk_per_trade_loss_cap_pct must not exceed risk_daily_loss_cap_pct, or one "
                "trade can end the session"
            )
        if self.risk_max_lots > self.playbook_max_lots:
            raise ValueError(
                f"risk_max_lots {self.risk_max_lots} is looser than playbook_max_lots "
                f"{self.playbook_max_lots}; the hard limit must bind at or before the playbook"
            )
        return self

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
    def session_exit_deadline_time(self) -> time:
        return time.fromisoformat(self.session_exit_deadline)

    @property
    def unattended(self) -> bool:
        return self.autonomy == "unattended"

    @property
    def directional_regime_set(self) -> frozenset[str]:
        return frozenset(
            piece.strip() for piece in self.directional_regimes.split(",") if piece.strip()
        )

    @property
    def analyst_deadline_seconds(self) -> float:
        """The analyst's own budget, always under the registry's timeout."""
        return max(1.0, self.specialist_timeout_seconds - self.regime_deadline_margin_seconds)

    @property
    def strategist_deadline_seconds(self) -> float:
        """The strategist's own budget, always under the registry's timeout."""
        return max(1.0, self.strategist_timeout_seconds - self.strategist_deadline_margin_seconds)

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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings singleton."""
    return Settings()  # type: ignore[call-arg]
