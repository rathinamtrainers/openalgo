from strike_desk.ltp_coa_narrate import polish_brief


def test_polish_keeps_template_without_invoke():
    payload = {
        "line": "Do nothing yet. NIFTY is 24,050.",
        "do_now": "Do nothing yet. NIFTY is 24,050.",
        "action": "Do nothing yet. NIFTY is 24,050.",
        "body": "Do nothing yet. NIFTY is 24,050. Floor at 24,000.",
    }
    out = polish_brief(payload, settings=None)
    assert out["line_source"] == "template"
    assert out["line"].startswith("Do nothing yet.")


def test_polish_accepts_grounded_rewrite():
    payload = {
        "line": (
            "Do nothing yet. NIFTY is 24,050. "
            "Wait until a call buy needs NIFTY at or below 23,980."
        ),
        "do_now": "Do nothing yet. NIFTY is 24,050.",
        "action": "Do nothing yet. NIFTY is 24,050.",
        "body": (
            "Do nothing yet. NIFTY is 24,050. "
            "Wait until a call buy needs NIFTY at or below 23,980."
        ),
    }

    def _invoke(_system: str, _user: str) -> str:
        return "Do nothing. NIFTY is 24,050, and a call buy is not armed until 23,980."

    out = polish_brief(payload, invoke=_invoke)
    assert out["line_source"] == "llm"
    assert out["do_now"].startswith("Do nothing.")
    assert "23,980" in out["line"]


def test_polish_rejects_invented_numbers_and_invalidation():
    payload = {
        "line": "Do nothing yet. NIFTY is 24,050.",
        "do_now": "Do nothing yet. NIFTY is 24,050.",
        "action": "Do nothing yet. NIFTY is 24,050.",
        "body": "Do nothing yet. NIFTY is 24,050.",
    }
    invented = polish_brief(
        dict(payload), invoke=lambda *_: "Do nothing. Target 25,000 next."
    )
    assert invented["line_source"] == "template"
    jargon = polish_brief(
        dict(payload), invoke=lambda *_: "Do nothing. This is an invalidation."
    )
    assert jargon["line_source"] == "template"
