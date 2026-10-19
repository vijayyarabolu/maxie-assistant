"""Client for a local Laya server (Jev-compatible `/v1/systemone` wire protocol).

The server runs on the Mac (deploy/laya-serve.sh): bound to 127.0.0.1, `LAYA_API_KEY` set,
checkpoint `laya` (English) on MPS, revision pinned with `LAYA_REVISION=reviewed` and
SHA-256 checked with `LAYA_SHA256_DIGESTS`. One forward pass answers three typed questions:
which route, which harmless action (if any), and how risky.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

ROUTE_CRITERIA = {
    "mac_action": "do something on the user's Mac right now: open an app or site, music, volume, timer, reminder, find a file",
    "quick_question": "a short factual question or the weather, answered in a sentence",
    "calendar": "the user's calendar: meetings, schedule, free time, events",
    "email": "the user's email: read, search, reply, draft",
    "jobhunt": "the user's job search: jobs, applications, resumes, recruiters, interviews, follow-ups",
    "multi_step": "a bigger task with several steps, planning, writing or coding",
    "chitchat": "small talk, feelings, greetings, thanks",
}
ACTION_CRITERIA = {
    "open_app": "open or launch an app or website",
    "music": "play, pause or skip music",
    "volume": "change or mute the volume",
    "timer": "start a timer",
    "none": "anything else",
}
RISK_LEVELS = ["harmless: looks something up or a small local action",
               "changes something he would want to check first",
               "sends, deletes, pays, submits or invites someone"]

QUESTIONS = {
    "route": {"type": "choice", "instructions": "What kind of request is this?", "criteria": ROUTE_CRITERIA},
    "action": {"type": "choice", "instructions": "Which direct Mac action is it, if any?", "criteria": ACTION_CRITERIA},
    "risk": {"type": "score", "instructions": "How risky would acting on this be?", "criteria": RISK_LEVELS},
}


@dataclass
class LayaResult:
    route: str
    route_p: float
    action: str
    action_p: float
    risk: float  # 0 harmless .. 1 risky
    probabilities: dict[str, float]
    latency_ms: float


class LayaClient:
    def __init__(self, url: str, api_key: str | None = None, client: httpx.Client | None = None,
                 timeout: float = 1.5, model: str = "english"):
        self.url = url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.http = client or httpx.Client(timeout=timeout)
        self.model = model
        self._down_until = 0.0

    def healthy(self) -> bool:
        try:
            return self.http.get(f"{self.url}/health", headers=self.headers).status_code == 200
        except Exception:  # any failure means "down"  # noqa: BLE001
            return False

    def decide(self, text: str, instructions: str, criteria: dict[str, str]) -> tuple[str, float] | None:
        """One typed choice (yes/no, which option…): (choice, probability), or None when Laya is down."""
        if time.monotonic() < self._down_until:
            return None
        try:
            r = self.http.post(f"{self.url}/v1/systemone", headers=self.headers, json={
                "state": text, "model": self.model,
                "questions": {"q": {"type": "choice", "instructions": instructions, "criteria": criteria}}})
            r.raise_for_status()
            a = r.json()["answers"]["q"]
            return a["choice"], float(a["probabilities"][a["choice"]])
        except Exception:  # same fallback as classify  # noqa: BLE001
            self._down_until = time.monotonic() + 30
            return None

    def classify(self, text: str) -> LayaResult | None:
        """None when Laya is down (then the router falls back). Backs off 30 s after a failure."""
        if time.monotonic() < self._down_until:
            return None
        t0 = time.perf_counter()
        try:
            r = self.http.post(f"{self.url}/v1/systemone", headers=self.headers,
                               json={"state": text, "questions": QUESTIONS, "model": self.model})
            r.raise_for_status()
            answers = r.json()["answers"]
            route, action, risk = answers["route"], answers["action"], answers["risk"]
            return LayaResult(
                route=route["choice"], route_p=float(route["probabilities"][route["choice"]]),
                action=action["choice"], action_p=float(action["probabilities"][action["choice"]]),
                risk=float(risk["score"]) / (len(RISK_LEVELS) - 1),
                probabilities={k: float(v) for k, v in route["probabilities"].items()},
                latency_ms=(time.perf_counter() - t0) * 1000,
            )
        except Exception:  # down, refused or malformed: fall back, retry in 30 s  # noqa: BLE001
            self._down_until = time.monotonic() + 30
            return None
