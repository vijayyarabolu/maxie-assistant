"""jobhunt + resumegen: two job-search apps of my own that run next to Maxie. Optional: with no URL in
Settings the job tools say they're off. Maxie only calls their HTTP APIs, never changes them.

The interface is what the brief, alerts and tools need. `FakeJobs` replays fixtures/jobhunt.json.
`JobsHTTP` is the real client, mapped to jobhunt's routes (jobhunt proxies resumegen). Maxie never
submits an application: there is no tool for it.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Protocol

import httpx

from .registry import Registry, Tool, schema

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "jobhunt.json"


log = logging.getLogger(__name__)
UNLINKED_LINE = ("jobhunt isn't linked yet, so there's nothing to show. Link it in Settings, under Job search apps.")
OFF_LINE = "The job search apps aren't set up on this hub. They're optional; Settings explains them."


class Jobs(Protocol):
    def new_jobs(self, sponsor_only: bool = True) -> list[dict]: ...
    def followups_due(self) -> list[dict]: ...
    def applied_on(self, day: str) -> int: ...
    def resumes_ready(self) -> list[dict]: ...
    def find_job(self, company: str, role: str = "") -> dict | None: ...
    def queue_resume(self, job_id: str) -> str: ...


class FakeJobs:
    def __init__(self, data: dict | None = None):
        self.data = data if data is not None else json.loads(FIXTURES.read_text())
        self.queued: list[str] = []

    def new_jobs(self, sponsor_only=True):
        return [j for j in self.data["jobs"] if j.get("new") and (j.get("sponsor_friendly") or not sponsor_only)]

    def followups_due(self):
        return self.data.get("followups", [])

    def applied_on(self, day):
        return sum(1 for a in self.data.get("applications", []) if a["date"] == day)

    def resumes_ready(self):
        return self.data.get("resumes_ready", [])

    def find_job(self, company, role=""):
        for j in self.data["jobs"]:
            if company.lower() in j["company"].lower() and (not role or role.lower() in j["title"].lower()):
                return j
        return next((j for j in self.data["jobs"] if company.lower() in j["company"].lower()), None)

    def queue_resume(self, job_id):
        self.queued.append(job_id)
        return f"resume queued for job {job_id}"


class JobsOff:
    """No job search apps configured: empty answers, and one plain line for anything that needs them."""

    error = None
    unlinked = False
    problem = None
    off = True

    def new_jobs(self, sponsor_only=True):
        return []

    def followups_due(self):
        return []

    def applied_on(self, day):
        return 0

    def resumes_ready(self):
        return []

    def find_job(self, company, role=""):
        return None

    def queue_resume(self, job_id):
        raise RuntimeError(OFF_LINE)


class JobsHTTP:
    """The real jobhunt (127.0.0.1:8765, cookie jh_token) and resumegen (8770, Bearer) APIs.

    jobhunt proxies resumegen for the resume queue, so the hub only talks to jobhunt. Tokens come from the
    secret store (JOBHUNT_TOKEN, RESUMEGEN_TOKEN); without them every call fails closed (empty answers,
    logged once).
    """

    def __init__(self, jobhunt_url: str, resumegen_url: str, client: httpx.Client | None = None,
                 jobhunt_token: str | None = None, resumegen_token: str | None = None, profile: str = ""):
        self.jh, self.rg = jobhunt_url.rstrip("/"), resumegen_url.rstrip("/")
        self.http = client or httpx.Client(timeout=15)
        self.jh_headers = {"Cookie": f"jh_token={jobhunt_token}"} if jobhunt_token else {}
        self.rg_headers = {"Authorization": f"Bearer {resumegen_token}"} if resumegen_token else {}
        self.profile = profile  # "" = jobhunt's default profile
        self.error: str | None = None  # last failure, e.g. "401 Unauthorized (is the jobhunt token set in Settings?)"

    @property
    def unlinked(self) -> bool:
        """jobhunt is up but turned the token away (or there is none): "jobhunt not linked · Link"."""
        return bool(self.error) and self.error.split(" ")[0] in ("401", "403")

    @property
    def problem(self) -> str | None:
        """What Maxie says instead of a raw error; None when jobhunt answered."""
        if not self.error:
            return None
        if self.unlinked:
            return UNLINKED_LINE
        return "jobhunt isn't answering right now. Is it running?"

    def set_tokens(self, jobhunt_token: str | None = None, resumegen_token: str | None = None) -> None:
        if jobhunt_token:
            self.jh_headers = {"Cookie": f"jh_token={jobhunt_token}"}
        if resumegen_token:
            self.rg_headers = {"Authorization": f"Bearer {resumegen_token}"}
        self.error = None

    def _safe(self, call, default):
        """Fail closed: jobhunt down or rejecting the token gives an empty answer, never a crash."""
        try:
            out = call()
        except httpx.HTTPStatusError as e:
            hint = " (is the jobhunt token set in Settings?)" if e.response.status_code in (401, 403) else ""
            self._fail(f"{e.response.status_code} {e.response.reason_phrase}{hint}")
            return default
        except (httpx.HTTPError, ValueError) as e:
            self._fail(f"{type(e).__name__}: {e}")
            return default
        self.error = None
        return out

    def _fail(self, msg: str) -> None:
        if msg != self.error:
            log.warning("jobhunt call failed: %s", msg)
        self.error = msg

    def _get(self, path: str, **params) -> dict:
        if self.profile:
            params["profile"] = self.profile
        r = self.http.get(f"{self.jh}{path}", params=params, headers=self.jh_headers)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, body: dict) -> dict:
        if self.profile:
            body = {**body, "profile": self.profile}
        r = self.http.post(f"{self.jh}{path}", json=body, headers=self.jh_headers)
        r.raise_for_status()
        return r.json()

    @staticmethod
    def _cards(answer: dict) -> list[dict]:
        return answer.get("jobs") or answer.get("cards") or answer.get("applications") or []

    @staticmethod
    def _slim(card: dict) -> dict:
        """The few fields the brief and the model need (a full card is several KB)."""
        sponsor = card.get("sponsor") or {}
        return {"id": card.get("uid") or card.get("id"), "title": card.get("title") or card.get("role_title"),
                "company": card.get("company"), "location": card.get("location"), "url": card.get("apply_url") or card.get("url"),
                "sponsor": (sponsor.get("label") or sponsor.get("word")) if isinstance(sponsor, dict) else sponsor,
                "fit": card.get("fit"), "posted": card.get("first_posted_at") or card.get("posted")}

    def new_jobs(self, sponsor_only=True):
        return self._safe(lambda: self._new_jobs(sponsor_only), [])

    def _new_jobs(self, sponsor_only):
        cards = [self._slim(c) for c in self._cards(self._get("/api/jobs", tab="recommended"))]
        if sponsor_only:
            friendly = [c for c in cards if c["sponsor"] and "no " not in str(c["sponsor"]).lower()]
            cards = friendly or cards  # no badges at all (profile does not need sponsorship): keep the feed
        return cards

    def followups_due(self):
        return self._safe(self._followups_due, [])

    def _followups_due(self):
        f = self._get("/api/tracker/followups")
        rows = list(f.get("overdue", [])) + list(f.get("today", []))
        return [{"company": r.get("company"), "role": r.get("role_title") or r.get("title"), "id": r.get("id"),
                 "applied_on": r.get("applied_on"), "due": "overdue" if r in f.get("overdue", []) else "today"} for r in rows]

    def applied_on(self, day):
        def count():
            apps = self._cards(self._get("/api/jobs", tab="applied"))
            return sum(1 for a in apps if str(a.get("applied_on", ""))[:10] == str(day)[:10])
        return self._safe(count, 0)

    def resumes_ready(self):
        return self._safe(lambda: [self._slim(c) for c in self._cards(self._get("/api/jobs", tab="resumes", rq="ready"))], [])

    def find_job(self, company, role=""):
        return self._safe(lambda: self._find_job(company, role), None)

    def _find_job(self, company, role):
        want_c, want_r = company.lower(), role.lower()
        for c in self._cards(self._get("/api/jobs", tab="recommended")):
            if want_c in str(c.get("company", "")).lower() and (not want_r or want_r in str(c.get("title", "")).lower()):
                slim = self._slim(c)
                try:
                    detail = self._get("/api/job", uid=slim["id"])
                    slim["jd"] = (detail.get("jd") or detail.get("jd_text") or "")[:4000]
                except httpx.HTTPError:
                    pass
                return slim
        return None

    def queue_resume(self, job_id):
        out = self._safe(lambda: self._post("/api/resumes/generate", {"uids": [job_id]}), None)
        if out is None:
            return self.problem or "jobhunt isn't answering right now."
        return out.get("message") or f"resume queued for job {job_id}"


def job_status(jobs: Jobs) -> dict:
    """New jobs, follow-ups, resumes; when jobhunt didn't answer, says so instead of looking empty."""
    out = {"new_jobs": jobs.new_jobs()[:15], "followups_due": jobs.followups_due(), "resumes_ready": jobs.resumes_ready()}
    problem = getattr(jobs, "problem", None)
    return {**out, "note": problem} if problem else out


def register(reg: Registry, jobs: Jobs) -> None:
    s = {"type": "string"}
    reg.add(Tool("job_search_status", "New sponsor-friendly jobs, follow-ups due, resumes ready.",
                 schema({}), lambda a: job_status(jobs), "read", untrusted_output=True))
    reg.add(Tool("find_job", "Look up a job (and its JD) in jobhunt by company and optional role.",
                 schema({"company": s, "role": s}, ["company"]),
                 lambda a: jobs.find_job(a["company"], a.get("role", "")) or "not found", "read", untrusted_output=True))
    reg.add(Tool("queue_resume", "Ask resumegen to tailor a resume for a jobhunt job id. Does not apply.",
                 schema({"job_id": s}), lambda a: jobs.queue_resume(a["job_id"]), "action"))
