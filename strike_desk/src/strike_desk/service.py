"""Service composition: scheduler, signals, lifecycle."""

from __future__ import annotations

import logging
import os
import signal
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from types import FrameType

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger
from langgraph.checkpoint.sqlite import SqliteSaver

from .approval_gate import ApprovalGate
from .approval_watcher import ApprovalWatcher
from .config import IST, Settings
from .errors import JournalWriteError, McpUnavailable, ModelCallFailed, PromptNotFound
from .execution_client import ExecutionClient
from .exit_executor import ExitExecutor
from .graph import TickDeps
from .journal import Journal
from .ltp_fade.session import FadeSession
from .mcp_toolbox import McpToolbox
from .observability import Redactor, configure_logging, configure_tracing
from .openalgo_client import OpenAlgoClient
from .openalgo_mirror import OpenAlgoMirror
from .options_strategist import build_options_strategist
from .position_monitor import PositionMonitor
from .prompt_registry import PromptRegistry
from .regime_analyst import build_regime_analyst
from .runner import TickRunner
from .session import SessionGate, engage_kill_switch
from .slots import bind_slot, resolve_slot_ids
from .specialists import SpecialistRegistry, shutdown_executor
from .tradetron_signal import close_tradetron_client

logger = logging.getLogger(__name__)

MAX_CONSECUTIVE_JOURNAL_FAILURES = 3


@dataclass
class SlotRuntime:
    """One named book inside the host process."""

    slot_id: str
    settings: Settings
    journal: Journal
    runner: TickRunner
    checkpoint_conn: sqlite3.Connection
    gate: ApprovalGate | None = None
    monitor: PositionMonitor | None = None
    watcher: ApprovalWatcher | None = None
    fade: FadeSession | None = None
    journal_failures: int = 0


class StrikeDeskService:
    """The long-running desk process. One host, one or more strategy slots."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._host_settings = settings
        settings.state_dir.mkdir(parents=True, exist_ok=True)

        secrets = [settings.openalgo_api_key.get_secret_value()]
        if settings.anthropic_api_key is not None:
            secrets.append(settings.anthropic_api_key.get_secret_value())
        if settings.jev_api_key is not None:
            secrets.append(settings.jev_api_key.get_secret_value())
        redactor = Redactor(secrets)
        configure_logging(settings, redactor)

        self._client = OpenAlgoClient(settings)
        self._prompts = PromptRegistry.load(settings.prompts_dir)
        self.registry = SpecialistRegistry()
        self._toolbox: McpToolbox | None = None
        self._register_specialists()

        self._mirror: OpenAlgoMirror | None = None
        self._execution: ExecutionClient | None = None
        if settings.execution_enabled:
            self._mirror = OpenAlgoMirror(settings)
            self._execution = ExecutionClient(settings)

        self._slots: list[SlotRuntime] = []
        self._span_processor = None
        self._provider = None
        for index, slot_id in enumerate(resolve_slot_ids(settings)):
            runtime = self._build_slot(slot_id, redactor, index == 0)
            self._slots.append(runtime)

        head = self._slots[0]
        self._journal = head.journal
        self._gate = head.gate
        self._monitor = head.monitor
        self._runner = head.runner
        self._watcher = head.watcher
        self._fade = head.fade
        self._checkpoint_conn = head.checkpoint_conn

        self._scheduler = BackgroundScheduler(timezone=IST)
        self._stop = threading.Event()
        self._manual = threading.Event()
        self._journal_failures = 0

    def _build_slot(self, slot_id: str, redactor: Redactor, first: bool) -> SlotRuntime:
        slot = bind_slot(self._host_settings, slot_id)
        slot.state_dir.mkdir(parents=True, exist_ok=True)
        journal = Journal(slot.db_path)
        journal.create_schema()
        if first:
            self._provider, self._span_processor = configure_tracing(
                self._host_settings, journal, redactor
            )

        gate: ApprovalGate | None = None
        monitor: PositionMonitor | None = None
        if self._host_settings.execution_enabled:
            assert self._mirror is not None and self._execution is not None
            gate = ApprovalGate(
                slot, journal, self._mirror, self._execution, reader=self._client
            )
            if slot.monitor_enabled:
                executor = ExitExecutor(
                    slot,
                    self._execution,
                    self._client,
                    f"{slot.order_strategy_prefix}-{slot.index_symbol}",
                )
                monitor = PositionMonitor(slot, journal, self._client, executor, self._mirror)

        checkpoint_conn = sqlite3.connect(str(slot.checkpoint_path), check_same_thread=False)
        checkpointer = SqliteSaver(checkpoint_conn)
        checkpointer.setup()

        fade: FadeSession | None = None
        if slot.ltp_fade_enabled:
            fade = FadeSession(snapshot_dir=slot.state_dir)
        deps = TickDeps(
            settings=slot,
            client=self._client,
            journal=journal,
            registry=self.registry,
            prompts=self._prompts,
            span_processor=self._span_processor,
            checkpointer=checkpointer,
            gate=gate,
            monitor=monitor,
            mirror=self._mirror,
            fade=fade,
        )
        runner = TickRunner(deps, SessionGate(self._client, slot))
        watcher: ApprovalWatcher | None = None
        if gate is not None and self._mirror is not None and self._execution is not None:
            watcher = ApprovalWatcher(
                slot,
                journal,
                self._mirror,
                self._execution,
                runner.resume_approval,
                monitor=monitor,
            )
        logger.info(
            "slot %s ready: mode=%s prefix=%s state=%s",
            slot_id,
            slot.playbook_mode,
            slot.order_strategy_prefix,
            slot.state_dir,
        )
        return SlotRuntime(
            slot_id=slot_id,
            settings=slot,
            journal=journal,
            runner=runner,
            checkpoint_conn=checkpoint_conn,
            gate=gate,
            monitor=monitor,
            watcher=watcher,
            fade=fade,
        )

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

    def _safe_tick(self, trigger: str, slot_id: str | None = None) -> None:
        """Never let one bad tick kill the daemon — but never let it hide, either."""
        targets = [item for item in self._slots if slot_id is None or item.slot_id == slot_id]
        for runtime in targets:
            self._safe_slot_tick(runtime, trigger)

    def _safe_slot_tick(self, runtime: SlotRuntime, trigger: str) -> None:
        try:
            runtime.runner.run_tick(trigger)
        except JournalWriteError:
            runtime.journal_failures += 1
            self._journal_failures = runtime.journal_failures
            logger.critical(
                "journal write failed on slot %s (%d consecutive) — the tick failed closed",
                runtime.slot_id,
                runtime.journal_failures,
            )
            if runtime.journal_failures >= MAX_CONSECUTIVE_JOURNAL_FAILURES:
                self.engage_kill_switch("journal unwritable — desk stopped automatically")
        except Exception:  # noqa: BLE001 — the scheduler thread must survive
            logger.exception("trigger %s failed on slot %s", trigger, runtime.slot_id)
        else:
            runtime.journal_failures = 0

    def engage_kill_switch(self, reason: str) -> None:
        engage_kill_switch(self._host_settings, reason)

    def _fade_watch(self) -> None:
        """Poll spot against frozen lines; fire one tick when a tag first prints."""
        overlay = next((item for item in self._slots if item.fade is not None), None)
        if overlay is None or overlay.fade is None:
            return
        if overlay.monitor is not None and overlay.monitor.snapshot().get("managed"):
            return
        try:
            overlay.fade.sync(self._client, datetime.now(tz=IST), overlay.settings)
        except Exception:  # noqa: BLE001 — watch must not kill the scheduler
            logger.exception("ltp fade watch sync failed")
            return
        if overlay.fade.consume_watch_trigger():
            logger.info("ltp fade tag — running a decision tick on %s", overlay.slot_id)
            self._safe_slot_tick(overlay, "fade-tag")

    def _poll_approvals(self, slot_id: str) -> None:
        """Never let one bad poll kill the scheduler thread."""
        runtime = next((item for item in self._slots if item.slot_id == slot_id), None)
        if runtime is None or runtime.watcher is None:
            return
        try:
            runtime.watcher.poll_once()
        except Exception:  # noqa: BLE001
            logger.exception("approval watch failed on slot %s", slot_id)

    def _handle_stop(self, _signum: int, _frame: FrameType | None) -> None:
        self._stop.set()

    def _handle_manual(self, _signum: int, _frame: FrameType | None) -> None:
        self._manual.set()

    def start(self) -> None:
        self._client.ping()
        names = ",".join(item.slot_id for item in self._slots)
        logger.info(
            "strike-desk starting: index=%s slots=%s cadence=%ss prompts=%s roles=%s model=%s",
            self._settings.index_symbol,
            names,
            self._settings.tick_interval_seconds,
            self._prompts.set_version,
            self.registry.registered_roles() or "(none)",
            self._settings.regime_model,
        )
        self._host_settings.pid_path.write_text(str(os.getpid()), encoding="utf-8")
        for runtime in self._slots:
            self._scheduler.add_job(
                self._safe_tick,
                IntervalTrigger(seconds=runtime.settings.tick_interval_seconds),
                args=("schedule", runtime.slot_id),
                id=f"decision-tick-{runtime.slot_id}",
                max_instances=1,
                coalesce=True,
                misfire_grace_time=30,
            )
            if runtime.gate is not None:
                health = runtime.gate.health()
                if health.ok:
                    logger.info("approval gate [%s]: %s", runtime.slot_id, health.detail)
                else:
                    logger.critical(
                        "approval gate [%s] is NOT usable: %s — that slot will decline "
                        "with approval-gate-unavailable until this is fixed",
                        runtime.slot_id,
                        health.detail,
                    )
            if runtime.watcher is not None:
                self._scheduler.add_job(
                    self._poll_approvals,
                    IntervalTrigger(seconds=runtime.settings.approval_poll_seconds),
                    args=(runtime.slot_id,),
                    id=f"approval-watch-{runtime.slot_id}",
                    max_instances=1,
                    coalesce=True,
                    misfire_grace_time=30,
                )
        if any(item.fade is not None for item in self._slots):
            overlay = next(item for item in self._slots if item.fade is not None)
            self._scheduler.add_job(
                self._fade_watch,
                IntervalTrigger(seconds=overlay.settings.ltp_fade_watch_seconds),
                id="fade-watch",
                max_instances=1,
                coalesce=True,
                misfire_grace_time=15,
            )
        self._scheduler.start()
        for runtime in self._slots:
            if runtime.monitor is not None:
                runtime.monitor.start()
        signal.signal(signal.SIGTERM, self._handle_stop)
        signal.signal(signal.SIGINT, self._handle_stop)
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
        for runtime in self._slots:
            if runtime.monitor is not None:
                runtime.monitor.close()
        try:
            self._scheduler.shutdown(wait=True)
        except Exception:  # noqa: BLE001
            logger.exception("scheduler shutdown failed")
        shutdown_executor()
        if self._toolbox is not None:
            self._toolbox.close()
        if self._provider is not None:
            self._provider.shutdown()
        self._client.close()
        if self._execution is not None:
            self._execution.close()
        close_tradetron_client()
        if self._mirror is not None:
            self._mirror.close()
        for runtime in self._slots:
            try:
                runtime.checkpoint_conn.close()
            finally:
                runtime.journal.close()
        self._host_settings.pid_path.unlink(missing_ok=True)
