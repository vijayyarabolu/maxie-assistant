"""The usefulness eval's judge and sample screenshot."""

from maxie.evals_useful import CASES, judge, sample_screen


def test_judge_checks_tools_and_reply():
    case = {"tools": ["weather"], "reply": r"rain"}
    assert judge(case, [{"type": "tool", "name": "weather", "ok": True}, {"type": "done", "text": "Heavy rain."}])["pass"]
    assert judge(case, [{"type": "done", "text": "Heavy rain."}])["why"] == "missing tool weather"
    assert judge(case, [{"type": "tool", "name": "weather", "ok": True}, {"type": "done", "text": "Sunny."}])["why"] == "reply didn't match"
    assert not judge(case, [{"type": "tool", "name": "weather", "ok": True}, {"type": "error", "text": "broke"}])["pass"]


def test_forty_cases_and_a_screenshot():
    assert len(CASES) == 40 and len({c["id"] for c in CASES}) == 40
    assert sample_screen().startswith("data:image/png;base64,")
