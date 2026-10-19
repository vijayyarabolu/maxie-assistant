"""Live-fact eval: 20 questions whose honest answer needs a tool (weather, news, prices, his mail, calendar,
files, jobhunt...). A case passes when the turn used a tool, answered a direct local action, or said
"I can't check that yet". Answering from memory fails. `python -m maxie.evals_live [--check]`
runs them through the real agent (fake Google/jobhunt/Mac, real Claude: about 20 Haiku calls)."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

from . import testdata

ROOT = Path(__file__).resolve().parents[1]
HONEST = re.compile(r"can'?t check that yet|can'?t check (?:that|this|it)|no way to check|don'?t have a (?:tool|way)", re.I)

CASES = [
    {"id": 1, "text": "What's the weather like right now?", "needs": "weather"},
    {"id": 2, "text": "Will it rain tomorrow?", "needs": "weather"},
    {"id": 3, "text": "What's the latest news about Anthropic?", "needs": "web_search"},
    {"id": 4, "text": "Who won the Chiefs game yesterday?", "needs": "web_search"},
    {"id": 5, "text": "What's the price of bitcoin?", "needs": "web_search"},
    {"id": 6, "text": "How is Nvidia stock doing today?", "needs": "web_search"},
    {"id": 7, "text": "What's on my calendar today?", "needs": "calendar_events"},
    {"id": 8, "text": "Do I have anything tomorrow morning?", "needs": "calendar_events"},
    {"id": 9, "text": "Any new emails from recruiters?", "needs": "search_email"},
    {"id": 10, "text": "Did the landlord email me this week?", "needs": "search_email"},
    {"id": 11, "text": "How many jobs did I apply to today?", "needs": "job_search_status"},
    {"id": 12, "text": "Any follow-ups due in jobhunt?", "needs": "job_search_status"},
    {"id": 13, "text": "What's my battery at?", "needs": "mac_status"},
    {"id": 14, "text": "How much disk space is left?", "needs": "mac_status"},
    {"id": 15, "text": "What did I just copy?", "needs": "clipboard"},
    {"id": 16, "text": "Is there a file called offer letter in my Documents?", "needs": "find_files"},
    {"id": 17, "text": "What's the exchange rate from dollars to rupees today?", "needs": "web_search"},
    {"id": 18, "text": "What time is sunset today?", "needs": "web_search"},
    {"id": 19, "text": "Is there a resume ready for me to review?", "needs": "job_search_status"},
    {"id": 20, "text": "What's the current temperature in Madison?", "needs": "weather"},
]


def judge(events: list[dict]) -> dict:
    """pass: a tool ran, or it was a direct local action, or the reply says it can't check."""
    tools = [e["name"] for e in events if e.get("type") == "tool"]
    done = next((e for e in events if e.get("type") in ("done", "error")), {})
    text = done.get("text", "")
    direct = any(e.get("type") == "route" and e.get("direct") for e in events)
    if tools:
        return {"pass": True, "why": "used " + ", ".join(tools), "tools": tools, "text": text}
    if HONEST.search(text):
        return {"pass": True, "why": "said it can't check", "tools": [], "text": text}
    if direct:
        return {"pass": True, "why": "direct local action", "tools": [], "text": text}
    return {"pass": False, "why": "answered without a tool", "tools": [], "text": text}


async def run(agent, cases: list[dict] = CASES) -> dict:
    results = []
    for c in cases:
        events = [e async for e in agent.handle(c["text"], conv=f"eval-live-{c['id']}")]
        results.append({**c, **judge(events)})
    passed = sum(r["pass"] for r in results)
    return {"n": len(results), "passed": passed, "score": round(passed / len(results), 4), "results": results}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="exit 1 unless every case passes")
    ap.add_argument("--out", default=str(ROOT / "evals" / "live_report.json"))
    args = ap.parse_args(argv)
    import anthropic

    from .config import Settings, secret
    from .hub.services import build
    from .testdata import sandbox
    key = secret("ANTHROPIC_API_KEY")
    if not key:
        sys.exit("ANTHROPIC_API_KEY is not in the Keychain")
    sandbox()  # throwaway data folder: nothing an eval does reaches his memory, settings or history
    s = build(Settings.load(), db_path=":memory:", fakes=True, anthropic_client=anthropic.AsyncAnthropic(api_key=key),
              anthropic_sync=anthropic.Anthropic(api_key=key))
    before = s.budget.status().month_usd
    report = asyncio.run(run(s.agent))
    report["cost_usd"] = round(s.budget.status().month_usd - before, 4)
    Path(args.out).write_text(json.dumps(testdata.redact_report(report), indent=2))  # no fixture text kept
    print(f"live facts: {report['passed']}/{report['n']} passed ({report['score']:.0%}), cost ${report['cost_usd']:.4f}")
    for r in report["results"]:
        mark = "ok  " if r["pass"] else "FAIL"
        print(f"  {mark} #{r['id']:>2} {r['text']!r}: {r['why']} | {r['text'][:70]!r}")
    return 0 if not args.check or report["passed"] == report["n"] else 1


if __name__ == "__main__":
    sys.exit(main())
