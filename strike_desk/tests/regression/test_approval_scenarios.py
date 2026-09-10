"""The frozen approval set: a queue state in, one settlement out."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from freezegun import freeze_time

from strike_desk.approval_watcher import ApprovalWatcher

from ..approval_fixtures import (
    approve_pending,
    cleared_context,
    delete_pending,
    queue_pending_order,
    reject_pending,
)

CASES = json.loads((Path(__file__).parent / "approval_scenarios.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_scenario(
    case, gate, journal, mirror, execution_client, execution_settings, openalgo, openalgo_db, today
):
    pending_order_id = queue_pending_order(openalgo_db)
    openalgo.post("/api/v1/placeorder").mock(
        return_value=httpx.Response(
            200,
            json={"status": "success", "mode": "semi_auto", "pending_order_id": pending_order_id},
        )
    )
    openalgo.post("/api/v1/orderstatus").mock(
        return_value=httpx.Response(
            200,
            json={
                "status": "success",
                "data": {
                    "order_status": case.get("order_status", "open"),
                    "average_price": 0.0,
                },
            },
        )
    )
    approval_id = gate.submit(cleared_context()).approval_id

    if case["pending_status"] == "approved":
        approve_pending(
            openalgo_db,
            pending_order_id,
            by=case.get("approved_by", "amit"),
            broker_order_id=case.get("broker_order_id"),
        )
    elif case["pending_status"] == "rejected":
        reject_pending(openalgo_db, pending_order_id, reason=case.get("rejected_reason", ""))
    elif case["pending_status"] == "deleted":
        delete_pending(openalgo_db, pending_order_id)

    if case["kill_switch"]:
        execution_settings.kill_switch_path.write_text("frozen case", encoding="utf-8")

    watcher = ApprovalWatcher(
        execution_settings, journal, mirror, execution_client, lambda _tick, _payload: False
    )
    with freeze_time(datetime.now(tz=UTC) + timedelta(seconds=case["seconds_elapsed"])):
        watcher.poll_once()

    terminal = [row for row in journal.list_approvals(today) if row.status != "pending"]
    if case["expect_status"] is None:
        assert terminal == []
        return
    assert [row.status for row in terminal] == [case["expect_status"]]
    assert terminal[0].withdrawal == case["expect_withdrawal"]
    assert bool(terminal[0].defect) is case["expect_defect"]
    assert len(journal.orders_for_approval(approval_id)) == case["expect_order_rows"]
