import json
from datetime import date, datetime
from types import SimpleNamespace

from strike_desk.coa_boards import BANKNIFTY, BTC, NATGAS
from strike_desk.config import IST
from strike_desk.errors import AnalyzerOff, OpenAlgoError
from strike_desk.ltp_coa import CoaSnapshot
from strike_desk.ltp_coa_intel import IntelState
from strike_desk.ltp_coa_live import CoaLiveRunner
from strike_desk.tradetron_hands import COA_BUY_CE, COA_EXIT, NoopCoaHands

_CHAIN = [
    {
        "strike": 22700.0,
        "ce": {"symbol": "NIFTY27OCT2622700CE", "lotsize": 65, "ltp": 120, "oi": 1},
        "pe": {"symbol": "NIFTY27OCT2622700PE", "lotsize": 65, "ltp": 110, "oi": 1},
    }
]


def _snap(signal: str = "BUY_CE") -> CoaSnapshot:
    return CoaSnapshot(
        spot=22450.0,
        atm=22700.0,
        support=None,
        resistance=None,
        scenario="1.1",
        eos=22480.0,
        eor=22820.0,
        allow_ce=True,
        allow_pe=True,
        signal=signal,  # type: ignore[arg-type]
        reason="test",
        action="Buy CE" if signal == "BUY_CE" else "Stand aside.",
    )


class _FakeHands:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.boards: list[str] = []

    def send(self, action: str, board: str = "nifty") -> None:
        self.sent.append(action)
        self.boards.append(board)


class _FakeAnalyzer:
    def __init__(self, *, on: bool = True) -> None:
        self.on = on
        self.placed: list[tuple[str, str, int]] = []

    def resolve_atm(self, option_type: str, expiry_day: date, **_kwargs) -> tuple[str, int]:
        return f"NIFTY27OCT2622700{option_type}", 65

    def place(self, symbol: str, action: str, quantity: int, **_kwargs):
        if not self.on:
            raise AnalyzerOff("OpenAlgo analyze_mode is off; refusing placeorder")
        self.placed.append((symbol, action, quantity))


class _FakeQuotes:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def optionchain(self, underlying, exchange, expiry, **_kwargs):
        self.calls.append((underlying, exchange, expiry))
        return {"chain": _CHAIN, "underlying_ltp": 22450.0, "atm_strike": 22700.0}


def _passthrough_handbook(snap, _state, now, **_kwargs):
    return snap, IntelState(session_date=now.astimezone(IST).date().isoformat())


def _wired_runner(settings, *, analyzer, hands=None):
    return CoaLiveRunner(
        settings,
        client=_FakeQuotes(),
        hands=hands or _FakeHands(),
        analyzer=analyzer,
        is_trading_day=lambda _day: True,
        expiry_date_for=lambda _day: date(2026, 10, 27),
    )


def test_coa_live_heartbeat_when_market_closed(settings):
    runner = CoaLiveRunner(
        settings,
        client=SimpleNamespace(),
        hands=SimpleNamespace(send=lambda *_a, **_k: None),
        is_trading_day=lambda _day: False,
        expiry_date_for=lambda _day: None,
    )
    sent = runner.step(datetime(2026, 10, 20, 10, 0, tzinfo=IST))
    assert sent == []
    assert settings.coa_heartbeat_path.exists()


def test_coa_live_fires_analyzer_then_hands(settings, monkeypatch):
    monkeypatch.setattr("strike_desk.ltp_coa_live.evaluate", lambda *_a, **_k: _snap("BUY_CE"))
    monkeypatch.setattr("strike_desk.ltp_coa_live.apply_handbook", _passthrough_handbook)
    hands = _FakeHands()
    analyzer = _FakeAnalyzer()
    runner = _wired_runner(settings, analyzer=analyzer, hands=hands)
    sent = runner.step(datetime(2026, 10, 26, 10, 30, tzinfo=IST))
    assert sent == [COA_BUY_CE]
    assert hands.sent == [COA_BUY_CE]
    assert hands.boards == ["nifty"]
    assert analyzer.placed == [("NIFTY27OCT2622700CE", "BUY", 65)]
    state = (settings.state_dir / "coa_live.json").read_text(encoding="utf-8")
    assert "NIFTY27OCT2622700CE" in state


def test_coa_live_hands_still_fire_when_analyze_off(settings, monkeypatch):
    monkeypatch.setattr("strike_desk.ltp_coa_live.evaluate", lambda *_a, **_k: _snap("BUY_CE"))
    monkeypatch.setattr("strike_desk.ltp_coa_live.apply_handbook", _passthrough_handbook)
    hands = _FakeHands()
    analyzer = _FakeAnalyzer(on=False)
    runner = _wired_runner(settings, analyzer=analyzer, hands=hands)
    sent = runner.step(datetime(2026, 10, 26, 10, 30, tzinfo=IST))
    assert sent == [COA_BUY_CE]
    assert hands.sent == [COA_BUY_CE]
    assert analyzer.placed == []


def test_coa_live_exit_sells_analyzer_then_hands(settings, monkeypatch):
    monkeypatch.setattr("strike_desk.ltp_coa_live.evaluate", lambda *_a, **_k: _snap("FLAT"))
    monkeypatch.setattr("strike_desk.ltp_coa_live.apply_handbook", _passthrough_handbook)
    monkeypatch.setattr("strike_desk.ltp_coa_live.decide_exit", lambda *_a, **_k: "stop")
    (settings.state_dir / "coa_live.json").write_text(
        '{"open": {"side": "CE", "entry": 120, "entry_ts": "2026-10-26T10:00:00+05:30",'
        ' "peak": 130, "eos": 22480, "eor": 22820, "support": null, "resistance": null,'
        ' "scenario": "1.1", "reclaimed": false, "strike": 22700},'
        ' "analyzer_symbol": "NIFTY27OCT2622700CE", "analyzer_qty": 65, "last_exit": null}',
        encoding="utf-8",
    )
    hands = _FakeHands()
    analyzer = _FakeAnalyzer()
    runner = _wired_runner(settings, analyzer=analyzer, hands=hands)
    sent = runner.step(datetime(2026, 10, 26, 11, 0, tzinfo=IST))
    assert sent == [COA_EXIT]
    assert hands.sent == [COA_EXIT]
    assert analyzer.placed == [("NIFTY27OCT2622700CE", "SELL", 65)]


def test_coa_live_banknifty_posts_own_flags(settings, monkeypatch):
    monkeypatch.setattr("strike_desk.ltp_coa_live.evaluate", lambda *_a, **_k: _snap("BUY_CE"))
    monkeypatch.setattr("strike_desk.ltp_coa_live.apply_handbook", _passthrough_handbook)
    quotes = _FakeQuotes()
    hands = _FakeHands()
    runner = CoaLiveRunner(
        settings,
        client=quotes,
        hands=hands,
        analyzer=_FakeAnalyzer(),
        board=BANKNIFTY,
        is_trading_day=lambda _day: True,
        expiry_date_for=lambda _day: date(2026, 10, 28),
    )
    sent = runner.step(datetime(2026, 10, 26, 10, 30, tzinfo=IST))
    assert sent == [COA_BUY_CE]
    assert hands.sent == [COA_BUY_CE]
    assert hands.boards == ["banknifty"]
    assert quotes.calls[0][0] == "BANKNIFTY"
    assert quotes.calls[0][1] == "NSE_INDEX"
    assert (settings.state_dir / "coa_live_bn.json").exists()
    brief = json.loads(
        (settings.state_dir / "public-brief-banknifty.json").read_text(encoding="utf-8")
    )
    assert brief["market"] == "banknifty"
    assert brief["index"] == "BANKNIFTY"
    assert brief["opportunities"][0]["trigger"].startswith("BANKNIFTY ")
    assert "NIFTY at" not in brief["do_now"]
    assert brief["scenario_label"] == "CALL TAGGED"


class _ListedQuotes(_FakeQuotes):
    def __init__(self) -> None:
        super().__init__()
        self.expiry_calls: list[tuple] = []

    def expiry_dates(self, symbol, exchange):
        self.expiry_calls.append((symbol, exchange))
        return ["03-NOV-26", "10-NOV-26", "17-NOV-26", "23-NOV-26", "29-DEC-26"]


class _FailListQuotes(_FakeQuotes):
    def expiry_dates(self, symbol, exchange):
        raise OpenAlgoError("expiry down")


def test_banknifty_uses_listed_expiry_not_calendar_wednesday(settings, monkeypatch):
    monkeypatch.setattr("strike_desk.ltp_coa_live.evaluate", lambda *_a, **_k: _snap("FLAT"))
    monkeypatch.setattr("strike_desk.ltp_coa_live.apply_handbook", _passthrough_handbook)
    quotes = _ListedQuotes()
    runner = CoaLiveRunner(
        settings,
        client=quotes,
        hands=_FakeHands(),
        analyzer=_FakeAnalyzer(),
        board=BANKNIFTY,
        is_trading_day=lambda _day: True,
        expiry_date_for=lambda _day: date(2026, 11, 25),
    )
    runner.step(datetime(2026, 10, 30, 10, 57, tzinfo=IST))
    assert quotes.expiry_calls == [("BANKNIFTY", "NFO")]
    assert quotes.calls[0][2] == "23NOV26"
    assert (settings.state_dir / "public-brief-banknifty.json").exists()


def test_listed_expiry_error_falls_back_to_calendar(settings, monkeypatch):
    monkeypatch.setattr("strike_desk.ltp_coa_live.evaluate", lambda *_a, **_k: _snap("FLAT"))
    monkeypatch.setattr("strike_desk.ltp_coa_live.apply_handbook", _passthrough_handbook)
    quotes = _FailListQuotes()
    runner = CoaLiveRunner(
        settings,
        client=quotes,
        hands=_FakeHands(),
        analyzer=_FakeAnalyzer(),
        board=BANKNIFTY,
        is_trading_day=lambda _day: True,
        expiry_date_for=lambda _day: date(2026, 10, 27),
    )
    runner.step(datetime(2026, 10, 26, 10, 30, tzinfo=IST))
    assert quotes.calls[0][2] == "27OCT26"


class _BoomHands:
    def send(self, action: str, board: str = "nifty"):
        raise RuntimeError("webhook down")


def test_cash_chip_stays_watching_when_hands_fail(settings, monkeypatch):
    monkeypatch.setattr("strike_desk.ltp_coa_live.evaluate", lambda *_a, **_k: _snap("BUY_CE"))
    monkeypatch.setattr("strike_desk.ltp_coa_live.apply_handbook", _passthrough_handbook)
    runner = _wired_runner(settings, analyzer=_FakeAnalyzer(), hands=_BoomHands())
    sent = runner.step(datetime(2026, 10, 26, 10, 30, tzinfo=IST))
    assert sent == []
    brief = json.loads((settings.state_dir / "public-brief.json").read_text(encoding="utf-8"))
    assert brief["scenario_label"] == "WATCHING"


def test_btc_runs_after_cash_hours_and_does_not_flatten(settings, monkeypatch):
    monkeypatch.setattr("strike_desk.ltp_coa_live.evaluate", lambda *_a, **_k: _snap("BUY_CE"))
    monkeypatch.setattr("strike_desk.ltp_coa_live.apply_handbook", _passthrough_handbook)
    quotes = _FakeQuotes()
    hands = _FakeHands()
    analyzer = _FakeAnalyzer()
    runner = CoaLiveRunner(
        settings,
        client=quotes,
        hands=hands,
        analyzer=analyzer,
        board=BTC,
        is_trading_day=lambda _day: True,
        expiry_date_for=lambda _day: date(2026, 10, 30),
        trading_window=None,
    )
    sent = runner.step(datetime(2026, 10, 30, 23, 10, tzinfo=IST))
    assert sent == [COA_BUY_CE]
    assert hands.boards == ["btc-options"]
    assert analyzer.placed == []
    brief = json.loads(
        (settings.state_dir / "public-brief-btc-options.json").read_text(encoding="utf-8")
    )
    assert brief["market"] == "btc-options"
    assert brief["scenario_label"] == "CALL TAGGED"
    assert "Cash is closed" not in brief["do_now"]

    monkeypatch.setattr("strike_desk.ltp_coa_live.evaluate", lambda *_a, **_k: _snap("FLAT"))
    sent = runner.step(datetime(2026, 10, 30, 15, 16, tzinfo=IST))
    assert COA_EXIT not in sent
    assert json.loads((settings.state_dir / "coa_live_btc.json").read_text(encoding="utf-8"))[
        "open"
    ]["side"] == "CE"


def test_btc_noop_hands_keeps_chip_watching(settings, monkeypatch):
    monkeypatch.setattr("strike_desk.ltp_coa_live.evaluate", lambda *_a, **_k: _snap("BUY_CE"))
    monkeypatch.setattr("strike_desk.ltp_coa_live.apply_handbook", _passthrough_handbook)
    runner = CoaLiveRunner(
        settings,
        client=_FakeQuotes(),
        hands=NoopCoaHands(),
        analyzer=None,
        board=BTC,
        is_trading_day=lambda _day: True,
        expiry_date_for=lambda _day: date(2026, 10, 30),
        trading_window=None,
    )
    sent = runner.step(datetime(2026, 10, 30, 3, 5, tzinfo=IST))
    assert sent == []
    brief = json.loads(
        (settings.state_dir / "public-brief-btc-options.json").read_text(encoding="utf-8")
    )
    assert brief["scenario_label"] == "WATCHING"


def test_coa_live_natgas_analyze_qty_is_1250(settings, monkeypatch):
    monkeypatch.setattr("strike_desk.ltp_coa_live.evaluate", lambda *_a, **_k: _snap("BUY_CE"))
    monkeypatch.setattr("strike_desk.ltp_coa_live.apply_handbook", _passthrough_handbook)
    quotes = SimpleNamespace(
        optionchain=lambda *_a, **_k: {
            "chain": [
                {
                    "strike": 22700.0,
                    "ce": {
                        "symbol": "NATURALGAS20NOV26280CE",
                        "lotsize": 1,
                        "ltp": 17,
                        "oi": 1,
                    },
                    "pe": {
                        "symbol": "NATURALGAS20NOV26280PE",
                        "lotsize": 1,
                        "ltp": 16,
                        "oi": 1,
                    },
                }
            ],
            "underlying_ltp": 22450.0,
            "atm_strike": 22700.0,
        }
    )
    analyzer = _FakeAnalyzer()
    runner = CoaLiveRunner(
        settings,
        client=quotes,
        hands=_FakeHands(),
        analyzer=analyzer,
        board=NATGAS,
        is_trading_day=lambda _day: True,
        expiry_date_for=lambda _day: date(2026, 11, 20),
        trading_window=lambda _day: (
            datetime(2026, 10, 26, 9, 0, tzinfo=IST),
            datetime(2026, 10, 26, 23, 30, tzinfo=IST),
        ),
    )
    sent = runner.step(datetime(2026, 10, 26, 10, 30, tzinfo=IST))
    assert sent == [COA_BUY_CE]
    assert analyzer.placed == [("NATURALGAS20NOV26280CE", "BUY", 1250)]
