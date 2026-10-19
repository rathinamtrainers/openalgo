import os
import sys
from pathlib import Path

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.stack_status_service import build_stack_status, write_desk_snapshot


def test_missing_snapshot_marks_mind_down(tmp_path, monkeypatch):
    monkeypatch.setattr("services.stack_status_service.STACK_STATUS_PATH", tmp_path / "missing.json")
    payload = build_stack_status(analyze_mode=True, broker="zerodha", broker_connected=True)
    by_id = {row["id"]: row for row in payload["components"]}
    assert by_id["mind"]["status"] == "fail"
    assert by_id["analyze"]["status"] == "pass"
    assert payload["overall"] == "fail"


def test_fresh_shield_snapshot_is_all_up(tmp_path, monkeypatch):
    path = tmp_path / "stack.json"
    monkeypatch.setattr("services.stack_status_service.STACK_STATUS_PATH", path)
    write_desk_snapshot(
        path,
        openalgo="active",
        strike_desk="active",
        strikeshield_enabled=True,
        strikeshield_heartbeat_age_s=8,
        set1=True,
        set2open=True,
    )
    payload = build_stack_status(analyze_mode=True, broker="zerodha", broker_connected=True)
    by_id = {row["id"]: row for row in payload["components"]}
    assert by_id["mind"]["status"] == "pass"
    assert by_id["strikeshield"]["status"] == "pass"
    assert by_id["hands"]["detail"] == "wings + shorts"
    assert Path(path).stat().st_mode & 0o777 == 0o644 or Path(path).exists()
