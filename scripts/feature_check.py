"""One real test of every feature against the live hub and your real data, read-only (nothing sent,
nothing deleted, your memory untouched; the music test only pauses). Chats go to their own conversation.
Settings tabs are checked with the off-screen renders (DebugSnapshot) separately.

Usage: .venv/bin/python scripts/feature_check.py [--label pass1] [--out f.json]

The phone check reaches the hub the way the phone does: set MAXIE_PHONE_URL (say your Tailscale name,
https://your-mac.your-tailnet.ts.net:8780). Without it the check is skipped.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import struct
import time
import zlib
from pathlib import Path

import httpx

from maxie.config import secret

HUB = "http://127.0.0.1:8780"
PHONE_URL = os.environ.get("MAXIE_PHONE_URL", "").rstrip("/")
FAILED = re.compile(r"couldn'?t|can'?t reach|isn'?t running|turn on .* in settings|not connected|went wrong|error", re.I)


def _png(width: int = 160, height: int = 60) -> bytes:
    """A plain image with a big red square on the left half: what the attachment test asks about."""
    rows = b"".join(b"\x00" + b"".join(b"\xdd\x22\x22" if x < width // 2 else b"\xff\xff\xff" for x in range(width))
                    for _ in range(height))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


class Check:
    def __init__(self, label: str):
        self.h = {"Authorization": f"Bearer {secret('HUB_TOKEN')}"}
        self.http = httpx.Client(timeout=180)
        self.conversation = f"feature-check-{label}"
        self.rows: list[dict] = []

    def row(self, feature: str, ok: bool, detail: str) -> None:
        self.rows.append({"feature": feature, "ok": bool(ok), "detail": detail[:400]})
        print(f"{'PASS' if ok else 'FAIL'} {feature}: {detail[:200]}", flush=True)

    def get(self, path: str, base: str = HUB):
        return self.http.get(f"{base}{path}", headers=self.h)

    def chat(self, text: str, **extra) -> tuple[str, list[dict]]:
        answer, tools = "", []
        with self.http.stream("POST", f"{HUB}/api/chat", headers=self.h,
                              json={"text": text, "conversation": self.conversation, "device": "mac", **extra}) as r:
            for line in r.iter_lines():
                if not line.strip():
                    continue
                ev = json.loads(line)
                if ev.get("type") == "tool":
                    tools.append(ev)
                elif ev.get("type") in ("done", "error"):
                    answer = ("ERROR: " if ev["type"] == "error" else "") + ev.get("text", "")
        return answer, tools

    def asked(self, feature: str, text: str, tool: str | None, want: str = "", **extra) -> None:
        answer, tools = self.chat(text, **extra)
        used = [t["name"] for t in tools]
        ok = bool(answer) and not answer.startswith("ERROR") and (tool is None or any(
            t["name"] == tool and t.get("ok", True) for t in tools))
        ok = ok and not FAILED.search(answer) and (not want or re.search(want, answer, re.I) is not None)
        self.row(feature, ok, f"tools {used}: {answer}")

    def run(self) -> list[dict]:
        self.asked("chat", "Say hello to me in one short sentence.", None)
        st = self.get("/api/voice/status").json()
        audio = self.http.post(f"{HUB}/api/voice/preview", headers=self.h, json={"voice": "af_jessica", "speed": 1.05})
        self.row("voice", st.get("alive") and audio.status_code == 200 and len(audio.content) > 10_000,
                 f"helper alive={st.get('alive')} muted={st.get('muted')} mic={st.get('mic')}; Kokoro preview "
                 f"{audio.status_code}, {len(audio.content)} bytes")
        self.asked("calendar", "What's on my calendar today?", "calendar_events", r"google|calendar")
        self.asked("mail", "Any new emails from LinkedIn this week?", "search_email")
        self.asked("notes", "Search my Apple Notes for insurance.", "notes_search")
        self.asked("reminders", "What are my open reminders?", "apple_reminders")
        self.asked("weather", "What's the weather right now?", "weather", r"degree|°")
        self.asked("music", "Pause the music.", "music")
        self.asked("screen look", "What's on my screen right now?", None, screen=True)
        image = "data:image/png;base64," + base64.b64encode(_png()).decode()
        self.asked("attachments (image)", "What colour is the square in this picture? One word.", None, r"red",
                   images=[image])
        note = Path.home() / "Documents" / "maxie-attachment-test.txt"
        note.write_text("The secret word for this test is marigold.\n")
        try:
            self.asked("attachments (file)", "What is the secret word in this file?", None, r"marigold", files=[str(note)])
        finally:
            note.unlink(missing_ok=True)
        mem = self.get("/api/memory").json()
        found = self.get("/api/memory/search?q=calendar")
        self.row("memory", bool(mem.get("facts")) and found.status_code == 200,
                 f"{len(mem.get('facts', []))} facts; search {found.status_code}: {found.json().get('line', '')[:80]}")
        tasks = self.get("/api/tasks").json()
        self.row("activity", bool(tasks.get("tasks")), f"{len(tasks.get('tasks', []))} tasks, newest: "
                 f"{(tasks.get('tasks') or [{}])[0].get('text', '')[:80]}")
        brief = self.get("/api/brief").json()
        self.row("brief", bool(brief.get("title")) and bool(brief.get("sections")),
                 f"{brief.get('title')} / {[s.get('name') for s in brief.get('sections', [])]}")
        self.background()
        self.pwa()
        return self.rows

    def background(self) -> None:
        job = self.http.post(f"{HUB}/api/background", headers=self.h,
                             json={"text": "Write one sentence on why sleep matters. Test task, no tools needed."}).json()
        end, status = time.monotonic() + 240, job.get("status")
        while time.monotonic() < end and status not in ("done", "failed", "cancelled"):
            time.sleep(3)
            now = next((j for j in self.get("/api/background").json() if j.get("id") == job.get("id")), {})
            status = now.get("status")
        self.row("background tasks", status == "done", f"job {job.get('id')}: {status}")

    def pwa(self) -> None:
        if not PHONE_URL:
            self.row("phone PWA", True, "skipped: set MAXIE_PHONE_URL to check the phone's way in")
            return
        try:
            page = self.http.get(f"{PHONE_URL}/")
            manifest = self.http.get(f"{PHONE_URL}/manifest.webmanifest")
            sw = self.http.get(f"{PHONE_URL}/sw.js")
            api = self.http.get(f"{PHONE_URL}/api/pause", headers=self.h)
        except httpx.HTTPError as e:
            self.row("phone PWA", False, f"{PHONE_URL}: {e}")
            return
        ok = page.status_code == 200 and "manifest" in page.text and manifest.status_code == 200 \
            and sw.status_code == 200 and api.status_code == 200
        self.row("phone PWA", ok, f"{PHONE_URL}: / {page.status_code}, manifest {manifest.status_code}, "
                 f"sw.js {sw.status_code}, API {api.status_code}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="pass1")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    rows = Check(args.label).run()
    print(f"{sum(r['ok'] for r in rows)}/{len(rows)} passed")
    if args.out:
        Path(args.out).write_text(json.dumps(rows, indent=1))


if __name__ == "__main__":
    main()
