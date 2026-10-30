import json

import pytest
import respx
from httpx import Response

from strike_desk.coa_analyzer import STRATEGY, CoaAnalyzer
from strike_desk.errors import AnalyzerOff, OpenAlgoError
from tests.conftest import BASE_URL


def test_coa_analyzer_refuses_when_analyze_mode_off(settings, client):
    analyzer = CoaAnalyzer(settings, client)
    try:
        with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
            router.post("/api/v1/analyzer").mock(
                return_value=Response(200, json={"status": "success", "analyze_mode": False})
            )
            with pytest.raises(AnalyzerOff):
                analyzer.place("NIFTY27OCT2622700CE", "BUY", 65)
            assert not any(
                call.request.url.path == "/api/v1/placeorder" for call in router.calls
            )
    finally:
        analyzer.close()


def test_coa_analyzer_places_with_tag_coa(settings, client):
    analyzer = CoaAnalyzer(settings, client)
    try:
        with respx.mock(base_url=BASE_URL, assert_all_called=False) as router:
            router.post("/api/v1/analyzer").mock(
                return_value=Response(200, json={"status": "success", "analyze_mode": True})
            )
            router.post("/api/v1/placeorder").mock(
                return_value=Response(
                    200,
                    json={"status": "success", "orderid": "COA-1", "mode": "analyze"},
                )
            )
            fill = analyzer.place("NIFTY27OCT2622700CE", "BUY", 65)
            assert fill.orderid == "COA-1"
            assert fill.mode == "analyze"
            body = json.loads(router.calls[-1].request.content)
            assert body["strategy"] == STRATEGY
            assert body["strategy"] == "COA"
            assert body["product"] == "MIS"
            assert body["action"] == "BUY"
    finally:
        analyzer.close()


def test_coa_analyzer_will_not_toggle_analyze_mode(settings, client):
    analyzer = CoaAnalyzer(settings, client)
    try:
        with pytest.raises(OpenAlgoError, match="will not toggle"):
            analyzer._post("/api/v1/analyzer/toggle", {})
    finally:
        analyzer.close()
