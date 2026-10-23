"""Typed question → Maxie's first word, through the real agent and router with real Claude models.

Everything outside is faked (Mac, Google, jobs) and the data folder is a scratch one, so his hub, history and panel
never see these turns. Cost: ~20 short Haiku turns, a few cents. Run it with the warm cache both cold and warm by
running twice in a row.

Usage: MAXIE_DATA=/tmp/somewhere .venv/bin/python scripts/measure_chat_latency.py [--tries 20]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from maxie import testdata  # noqa: E402
from maxie.config import data_dir, secret  # noqa: E402
from maxie.hub.services import build  # noqa: E402

QUESTIONS = ["what's two plus two", "say hi in five words", "what day comes after Tuesday", "give me a synonym for fast",
             "how many minutes in two hours", "what's the capital of France", "name a green vegetable",
             "spell the word cat backwards", "is a tomato a fruit", "what color is the sky on a clear day",
             "say thanks in Spanish", "what's ten percent of fifty", "what rhymes with moon", "how many legs does a spider have",
             "what's the opposite of cold", "give me one word for happy", "what's half of eighteen", "name a planet",
             "what's the boiling point of water in Celsius", "count from one to three"]


async def one(agent, text: str) -> dict:
    t0 = time.perf_counter()
    first, route, model = None, "", ""
    async for ev in agent.handle(text, "latency-bench", "mac"):
        if ev["type"] == "route":
            route, model = ev.get("route", ""), ev.get("model", "")
        if ev["type"] in ("ack", "text") and first is None and ev.get("text", "").strip():
            first = (time.perf_counter() - t0) * 1000
    return {"q": text, "first_ms": None if first is None else round(first), "total_ms": round((time.perf_counter() - t0) * 1000),
            "route": route, "model": model}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tries", type=int, default=20)
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    if testdata.is_live(data_dir()):
        raise SystemExit("Point MAXIE_DATA at a scratch folder: this never runs on your data.")
    import anthropic
    key = secret("ANTHROPIC_API_KEY")
    s = build(fakes=True, anthropic_client=anthropic.AsyncAnthropic(api_key=key), anthropic_sync=anthropic.Anthropic(api_key=key))
    rows = [await one(s.agent, q) for q in QUESTIONS[:args.tries]]
    firsts = [r["first_ms"] for r in rows if r["first_ms"] is not None]
    summary = {"tries": len(rows), "p50_ms": round(float(np.percentile(firsts, 50))), "p95_ms": round(float(np.percentile(firsts, 95))),
               "routes": sorted({f"{r['route']}:{r['model']}" for r in rows}), "data": os.environ.get("MAXIE_DATA")}
    for r in rows:
        print(f"{r['first_ms']:>6} ms  {r['route']:12s} {r['q']}")
    print(json.dumps(summary))
    if args.out:
        Path(args.out).write_text(json.dumps({"summary": summary, "rows": rows}, indent=1))


if __name__ == "__main__":
    asyncio.run(main())
