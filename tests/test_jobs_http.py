"""JobsHTTP against responses shaped like jobhunt's real routes (jobhunt/server.py, read on the Mac)."""

import httpx

from maxie.hands.jobs import JobsHTTP

CARD = {"uid": "acme-1", "title": "ML Engineer", "company": "AcmeAI", "location": "Remote", "url": "https://acme/jobs/1",
        "apply_url": "https://acme/apply/1", "sponsor": {"label": "Sponsors H-1B", "shape": "circle"}, "fit": "good",
        "first_posted_at": "2026-10-13"}
NO_SPONSOR = {**CARD, "uid": "nope-2", "company": "NopeCo", "sponsor": {"label": "No sponsorship"}}
APPLIED = [{"id": 7, "company": "AcmeAI", "role_title": "ML Engineer", "applied_on": "2026-10-15", "status": "applied"},
           {"id": 8, "company": "Old", "role_title": "SWE", "applied_on": "2026-10-01", "status": "applied"}]


def transport(seen):
    def handle(req: httpx.Request) -> httpx.Response:
        seen.append((req.method, req.url.path, dict(req.url.params), (req.headers.get("cookie") or "").removeprefix("jh_token=") or None))
        p, q = req.url.path, req.url.params
        if p == "/api/jobs" and q.get("tab") == "recommended":
            return httpx.Response(200, json={"tab": "recommended", "jobs": [CARD, NO_SPONSOR]})
        if p == "/api/jobs" and q.get("tab") == "applied":
            return httpx.Response(200, json={"tab": "applied", "applications": APPLIED})
        if p == "/api/jobs" and q.get("tab") == "resumes":
            return httpx.Response(200, json={"tab": "resumes", "rq": "ready", "jobs": [CARD], "total": 1})
        if p == "/api/tracker/followups":
            return httpx.Response(200, json={"overdue": [APPLIED[1]], "today": [APPLIED[0]], "upcoming": []})
        if p == "/api/job":
            return httpx.Response(200, json={"uid": q["uid"], "jd": "Build models. " * 10})
        if p == "/api/resumes/generate" and req.method == "POST":
            return httpx.Response(200, json={"ok": True, "message": "queued 1"})
        return httpx.Response(404)
    return httpx.MockTransport(handle)


def client(seen):
    return JobsHTTP("http://jh", "http://rg", httpx.Client(transport=transport(seen)), jobhunt_token="tok")


def test_new_jobs_keeps_sponsor_friendly_and_sends_token():
    seen = []
    jobs = client(seen).new_jobs()
    assert [j["company"] for j in jobs] == ["AcmeAI"]
    assert jobs[0] == {"id": "acme-1", "title": "ML Engineer", "company": "AcmeAI", "location": "Remote",
                       "url": "https://acme/apply/1", "sponsor": "Sponsors H-1B", "fit": "good", "posted": "2026-10-13"}
    assert seen[0][1:] == ("/api/jobs", {"tab": "recommended"}, "tok")


def test_followups_applied_count_resumes_find_and_queue():
    seen = []
    c = client(seen)
    assert [(f["company"], f["due"]) for f in c.followups_due()] == [("Old", "overdue"), ("AcmeAI", "today")]
    assert c.applied_on("2026-10-15") == 1 and c.applied_on("2026-10-02") == 0
    assert c.resumes_ready()[0]["company"] == "AcmeAI"
    found = c.find_job("acme", "ml")
    assert found["id"] == "acme-1" and found["jd"].startswith("Build models")
    assert c.find_job("nowhere") is None
    assert c.queue_resume("acme-1") == "queued 1"
    assert ("POST", "/api/resumes/generate", {}, "tok") in seen


def test_fails_closed_when_jobhunt_rejects_the_token():
    # no JOBHUNT_TOKEN in the Keychain yet: jobhunt answers 401; the brief and the tools must carry on
    def handle(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "bad token"})
    j = JobsHTTP("http://jh", "http://rg", httpx.Client(transport=httpx.MockTransport(handle)))
    assert j.new_jobs() == [] and j.followups_due() == [] and j.applied_on("2026-10-14") == 0
    assert j.resumes_ready() == [] and j.find_job("Acme") is None
    assert "401" in (j.error or "") and "jobhunt" in j.queue_resume("acme-1")
