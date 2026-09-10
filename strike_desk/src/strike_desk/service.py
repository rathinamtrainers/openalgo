"""Service composition: scheduler, signals, lifecycle."""

from __future__ import annotations

import logging
import os
import signal
import sqlite3
import threading
from types import FrameType

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from langgraph.checkpoint.sqlite import SqliteSaver

from .approval_gate import ApprovalGate
from .approval_watcher import ApprovalWatcher
from .config import IST, Settings
from .errors import JournalWriteError, McpUnavailable, ModelCallFailed, PromptNotFound
from .execution_client import ExecutionClient
from .graph import TickDeps
from .journal import Journal
from .mcp_toolbox import McpToolbox
from .observability import Redactor, configure_logging, configure_tracing
from .openalgo_client import OpenAlgoClient
from .openalgo_mirror import OpenAlgoMirror
from .options_strategist import build_options_strategist
from .prompt_registry import PromptRegistry
from .regime_analyst import build_regime_analyst
from .runner import TickRunner
from .session import SessionGate, engage_kill_switch
from .specialists import SpecialistRegistry, shutdown_executor

logger = logging.getLogger(__name__)

MAX_CONSECUTIVE_JOURNAL_FAILURES = 3


class StrikeDeskService:
    """The long-running desk process."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        settings.state_dir.mkdir(parents=True, exist_ok=True)

        secrets = [settings.openalgo_api_key.get_secret_value()]
        if settings.anthropic_api_key is not None:
            secrets.append(settings.anthropic_api_key.get_secret_value())
        redactor = Redactor(secrets)
        configure_logging(settings, redactor)

        self._journal = Journal(settings.db_path)
        self._journal.create_schema()
        self._provider, self._span_processor = configure_tracing(settings, self._journal, redactor)

        self._client = OpenAlgoClient(settings)
        self._prompts = PromptRegistry.load(settings.prompts_dir)
        self.registry = SpecialistRegistry()
        self._toolbox: McpToolbox | None = None
        self._register_specialists()

        self._mirror: OpenAlgoMirror | None = None
        self._execution: ExecutionClient | None = None
        self._gate: ApprovalGate | None = None
        if settings.execution_enabled:
            self._mirror = OpenAlgoMirror(settings)
            self._execution = ExecutionClient(settings)
            self._gate = ApprovalGate(settings, self._journal, self._mirror, self._execution)

        self._checkpoint_conn = sqlite3.connect(
            str(settings.checkpoint_path), check_same_thread=False
        )
        checkpointer = SqliteSaver(self._checkpoint_conn)
        checkpointer.setup()

        deps = TickDeps(
            settings=settings,
            client=self._client,
            journal=self._journal,
            registry=self.registry,
            prompts=self._prompts,
            span_processor=self._span_processor,
            checkpointer=checkpointer,
            gate=self._gate,
        )
        self._runner = TickRunner(deps, SessionGate(self._client, settings))
        self._watcher: ApprovalWatcher | None = None
        if self._gate is not None and self._mirror is not None and self._execution is not None:
            self._watcher = ApprovalWatcher(
                settings,
                self._journal,
                self._mirror,
                self._execution,
                self._runner.resume_approval,
            )
        self._scheduler = BackgroundScheduler(timezone=IST)
        self._stop = threading.Event()
        self._manual = threading.Event()
        self._journal_failures = 0

    def _register_specialists(self) -> None:
        """Stand up the reasoning plane, or run without it and decline every tick."""
        if self._settings.anthropic_api_key is None:
            logger.warning(
                "no Anthropic API key configured — the desk will tick and decline with "
                "specialist-unavailable until one is set"
            )
            return
        toolbox = McpToolbox(self._settings)
        specialists = []
        try:
            specialists.append(build_regime_analyst(self._settings, self._prompts, toolbox))
        except (ModelCallFailed, PromptNotFound):
            logger.exception("could not build the regime analyst — running without one")
        try:
            specialists.append(build_options_strategist(self._settings, self._prompts, toolbox))
        except (ModelCallFailed, PromptNotFound):
            logger.exception("could not build the options strategist — running without one")
        if not specialists:
            toolbox.close()
            return
        try:
            toolbox.start()
        except McpUnavailable:
            logger.exception("MCP session unavailable at startup — it will retry on each read")
        self._toolbox = toolbox
        for specialist in specialists:
            self.registry.register(specialist)

    def _safe_tick(self, trigger: str) -> None:
        """Never let one bad tick kill the daemon — but never let it hide, either."""
        try:
            self._runner.run_tick(trigger)
        except JournalWriteError:
            self._journal_failures += 1
            logger.critical(
                "journal write failed (%d consecutive) — the tick failed closed",
                self._journal_failures,
            )
            if self._journal_failures >= MAX_CONSECUTIVE_JOURNAL_FAILURES:
                self.engage_kill_switch("journal unwritable — desk stopped automatically")
        except Exception:  # noqa: BLE001 — the scheduler thread must survive
            logger.exception("trigger %s failed", trigger)
        else:
            self._journal_failures = 0

    def engage_kill_switch(self, reason: str) -> None:
        engage_kill_switch(self._settings, reason)

    def _poll_approvals(self) -> None:
        """Never let one bad poll kill the scheduler thread."""
        try:
            self._watcher.poll_once()  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            logger.exception("approval watch failed")

    def _handle_stop(self, _signum: int, _frame: FrameType | None) -> None:
        self._stop.set()

    def _handle_manual(self, _signum: int, _frame: FrameType | None) -> None:
        self._manual.set()

    def start(self) -> None:
        self._client.ping()
        logger.info(
            "strike-desk starting: index=%s cadence=%ss prompts=%s roles=%s model=%s",
            self._settings.index_symbol,
            self._settings.tick_interval_seconds,
            self._prompts.set_version,
            self.registry.registered_roles() or "(none)",
            self._settings.regime_model,
        )
        self._settings.pid_path.write_text(str(os.getpid()), encoding="utf-8")
        self._scheduler.add_job(
            self._safe_tick,
            IntervalTrigger(seconds=self._settings.tick_interval_seconds),
            args=("schedule",),
            id="decision-tick",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=30,
        )
        if self._gate is not None:
            health = self._gate.health()
            if health.ok:
                logger.info("approval gate: %s", health.detail)
            else:
                logger.critical(
                    "approval gate is NOT usable: %s — the desk will decline every tick "
                    "with approval-gate-unavailable until this is fixed",
                    health.detail,
                )
        if self._watcher is not None:
            self._scheduler.add_job(
                self._poll_approvals,
                IntervalTrigger(seconds=self._settings.approval_poll_seconds),
                id="approval-watch",
                max_instances=1,
                coalesce=True,
                misfire_grace_time=30,
            )
        self._scheduler.start()
        signal.signal(signal.SIGTERM, self._handle_stop)
        signal.signal(signal.SIGINT, self._handle_stop)
        # SIGUSR1 is POSIX-only; on Windows tick-now is unavailable via signals.
        if hasattr(signal, "SIGUSR1"):
            signal.signal(signal.SIGUSR1, self._handle_manual)

    def run_forever(self) -> None:
        try:
            while not self._stop.is_set():
                if self._manual.wait(timeout=1.0):
                    self._manual.clear()
                    self._safe_tick("manual")
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        logger.info("strike-desk shutting down")
        try:
            self._scheduler.shutdown(wait=True)
        except Exception:  # noqa: BLE001
            logger.exception("scheduler shutdown failed")
        shutdown_executor()
        if self._toolbox is not None:
            self._toolbox.close()
        self._provider.shutdown()
        self._client.close()
        if self._execution is not None:
            self._execution.close()
        if self._mirror is not None:
            self._mirror.close()
        try:
            self._checkpoint_conn.close()
        finally:
            self._journal.close()
        self._settings.pid_path.unlink(missing_ok=True)
