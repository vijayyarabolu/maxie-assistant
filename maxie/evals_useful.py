"""Usefulness eval: 40 things I ask on a normal day, run through the real agent (real Claude, fake
Mac / Google / jobhunt so nothing happens for real). A case passes when the expected tools ran and the
reply matches. `python -m maxie.evals_useful [--check]` (about 40 calls, mostly Haiku)."""

from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import re
import sys
from pathlib import Path

from . import testdata

ROOT = Path(__file__).resolve().parents[1]
THRESHOLD = 0.9

# tools: every one must have run (any order). reply: regex the final text must match. image: a screenshot goes along.
CASES = [
    {"id": 1, "text": "what time is it", "reply": r"\b\d{1,2}:\d{2}"},
    {"id": 2, "text": "what's the date today", "reply": r"October|Wednesday|Thursday|Friday|Saturday|Sunday|Monday|Tuesday"},
    {"id": 3, "text": "what's the weather like", "tools": ["weather"]},
    {"id": 4, "text": "do I need an umbrella tomorrow", "tools": ["weather"]},
    {"id": 5, "text": "open chrome", "tools": ["open_app"]},
    {"id": 6, "text": "open jobhunt in the browser", "tools": ["open_url"], "reply": r"8765|jobhunt"},
    {"id": 7, "text": "open linkedin", "tools": ["open_url"]},
    {"id": 8, "text": "how many jobs did I apply to today", "tools": ["job_search_status"]},
    {"id": 9, "text": "any follow-ups due", "tools": ["job_search_status"]},
    {"id": 10, "text": "is there a resume ready to review", "tools": ["job_search_status"]},
    {"id": 11, "text": "what's on my calendar today", "tools": ["calendar_events"]},
    {"id": 12, "text": "do I have anything tomorrow morning", "tools": ["calendar_events"]},
    {"id": 13, "text": "any new emails from recruiters", "tools": ["search_email"]},
    {"id": 14, "text": "read me the latest email from the landlord", "tools": ["search_email"]},
    {"id": 15, "text": "draft a reply to the landlord saying thanks, received", "tools": ["draft_email"], "reply": r"draft"},
    {"id": 16, "text": "remind me to call Riley at 5pm", "tools": ["add_reminder"]},
    {"id": 17, "text": "set a 20 minute timer", "tools": ["timer"]},
    {"id": 18, "text": "what's my API spend this month", "reply": r"\$\d"},
    {"id": 19, "text": "what's my battery at", "tools": ["mac_status"]},
    {"id": 20, "text": "what did I just copy", "tools": ["clipboard"]},
    {"id": 21, "text": "summarize what's on my screen", "image": True, "reply": r"pytest|test|fail", "tools": []},
    {"id": 22, "text": "what's the error in this window", "image": True, "reply": r"assert|fail|error"},
    {"id": 23, "text": "find my resume pdf", "tools": ["find_files"]},
    {"id": 24, "text": "make a note: ideas for the portfolio site", "tools": ["add_note"]},
    {"id": 25, "text": "play some music", "tools": ["music"]},
    {"id": 26, "text": "turn the volume down", "tools": ["volume"]},
    {"id": 27, "text": "turn on do not disturb", "tools": ["do_not_disturb"]},
    {"id": 28, "text": "remember that Jordan's birthday is March 3", "reply": r"remember|got it|saved|noted"},
    {"id": 29, "text": "what do you know about me", "reply": r"."},
    {"id": 30, "text": "brief me", "reply": r"calendar|email|job"},
    {"id": 31, "text": "what's the latest news about Anthropic", "tools": ["web_search"]},
    {"id": 32, "text": "how much is 18 percent of 240", "reply": r"43\.2"},
    {"id": 33, "text": "tell Riley I'm running 10 minutes late", "tools": ["draft_message"]},
    {"id": 34, "text": "search my drive for the offer letter", "tools": ["drive_search"]},
    {"id": 35, "text": "schedule a mock interview with Riley tomorrow at 10", "tools": ["create_event"]},
    {"id": 36, "text": "queue a resume for the AcmeAI job", "tools": ["find_job", "queue_resume"]},
    {"id": 37, "text": "how much disk space is left", "tools": ["mac_status"]},
    {"id": 38, "text": "open my downloads folder", "tools": ["open_file"]},
    {"id": 39, "text": "stop", "reply": r"[Ss]topped"},
    {"id": 40, "text": "mute notifications for today", "reply": r"[Mm]uted|[Dd]o [Nn]ot [Dd]isturb"},
]


def sample_screen() -> str:
    """A fake terminal screenshot (PNG data URL) with a failing pytest run on it."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (1000, 400), (20, 20, 24))
    d = ImageDraw.Draw(img)
    lines = ["$ pytest -q", "....F..", "FAILED tests/test_budget.py::test_cap - AssertionError: assert 96.0 <= 95.0",
             "1 failed, 6 passed in 2.31s"]
    for i, line in enumerate(lines):
        d.text((20, 30 + i * 40), line, fill=(230, 230, 230))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def judge(case: dict, events: list[dict]) -> dict:
    tools = [e["name"] for e in events if e.get("type") == "tool"]
    done = next((e for e in events if e.get("type") in ("done", "error")), {})
    text = done.get("text", "")
    missing = [t for t in case.get("tools", []) if t not in tools]
    pattern = case.get("reply")
    reply_ok = bool(re.search(pattern, text, re.I)) if pattern else True
    ok = not missing and reply_ok and done.get("type") != "error"
    why = "ok" if ok else ("missing tool " + ", ".join(missing) if missing else "reply didn't match" if not reply_ok else "error")
    return {"pass": ok, "why": why, "tools": tools, "text": text}


async def run(agent, cases: list[dict] = CASES) -> dict:
    shot = sample_screen()
    results = []
    for c in cases:
        images = [shot] if c.get("image") else None
        events = [e async for e in agent.handle(c["text"], conv=f"eval-useful-{c['id']}", images=images)]
        results.append({"id": c["id"], "text": c["text"], **judge(c, events)})
    passed = sum(r["pass"] for r in results)
    return {"n": len(results), "passed": passed, "score": round(passed / len(results), 4), "results": results}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help=f"exit 1 below {THRESHOLD:.0%}")
    ap.add_argument("--only", type=int, nargs="*", help="case ids to run")
    ap.add_argument("--out", default=str(ROOT / "evals" / "useful_report.json"))
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
    cases = [c for c in CASES if not args.only or c["id"] in args.only]
    before = s.budget.status().month_usd
    report = asyncio.run(run(s.agent, cases))
    report["cost_usd"] = round(s.budget.status().month_usd - before, 4)
    Path(args.out).write_text(json.dumps(testdata.redact_report(report), indent=2))  # no fixture text kept
    print(f"useful: {report['passed']}/{report['n']} passed ({report['score']:.0%}), cost ${report['cost_usd']:.4f}")
    for r in report["results"]:
        mark = "ok  " if r["pass"] else "FAIL"
        print(f"  {mark} #{r['id']:>2} {r['text']!r}: {r['why']} [{', '.join(r['tools'])}] | {r['text'][:60]!r}")
    return 0 if not args.check or report["score"] >= THRESHOLD else 1


if __name__ == "__main__":
    sys.exit(main())
