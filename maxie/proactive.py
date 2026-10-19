"""Proactive: 8 AM brief, 9 PM wrap, interview/recruiter alerts, meeting prep, wins log.

Briefs are assembled in code from the sources (no model call, no API spend). The scheduler
is a small asyncio loop; every job is a plain method so tests call them with a fake clock.
While he sleeps every push is held and delivered when he wakes (maxie.presence; with no signal from
the Mac, or in "fixed" mode, that is quiet hours, default 11 PM - 7 AM).
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from .budget import Budget
from .config import Settings
from .db import DB
from .hands.google import JOB, MAIN, Calendar, Email, Mail
from .hands.google_real import GoogleRefused
from .hands.jobs import Jobs

log = logging.getLogger(__name__)
REFUSED = "Google said no (403)"  # a source's one line when Google refuses it; Settings › Connections says why

STEP_ERROR_EVERY_S = 3600  # a check that keeps failing is logged once an hour
MORNING_UNTIL_H = 12  # the brief says "Good morning" before noon, "Today so far" after


@dataclass(frozen=True)
class BriefSection:
    name: str
    lines: list[str] = field(default_factory=list)
    missing: str = ""  # "Gmail not connected": the source isn't there, and `action` fixes it
    action: str = ""   # connect_google | link_jobhunt | open_connections

    def text(self) -> str:
        return f"{self.missing}." if self.missing else "; ".join(self.lines) + "."


@dataclass(frozen=True)
class Brief:
    title: str
    date: str
    sections: list[BriefSection]

    def text(self) -> str:
        """Spoken / pushed: short sentences, one per section."""
        return " ".join([f"{self.title}. It's {self.date}." if self.title == "Good morning" else f"{self.title}."]
                        + [f"{s.name}: {s.text()}" for s in self.sections])

    def dict(self) -> dict:
        return {"title": self.title, "date": self.date, "text": self.text(), "sections": [asdict(x) for x in self.sections]}


class Notifier(Protocol):
    def push(self, title: str, body: str, tag: str = "", mood: str = "") -> None: ...


class FakeNotifier:
    def __init__(self):
        self.sent: list[dict] = []

    def push(self, title, body, tag="", mood=""):
        self.sent.append({"title": title, "body": body, "tag": tag, "mood": mood})


# ---- wins ----------------------------------------------------------------------------------------

class Wins:
    def __init__(self, db: DB, clock=time.time):
        self.db, self.clock = db, clock
        db.script("CREATE TABLE IF NOT EXISTS wins(id INTEGER PRIMARY KEY, ts REAL, kind TEXT, text TEXT)")

    def add(self, kind: str, text: str) -> None:
        self.db.execute("INSERT INTO wins(ts,kind,text) VALUES (?,?,?)", (self.clock(), kind, text))

    def since(self, days: float) -> list[dict]:
        return self.db.all("SELECT kind, text FROM wins WHERE ts >= ? ORDER BY ts", (self.clock() - days * 86400,))


# ---- job-inbox classification --------------------------------------------------------------------

INTERVIEW = re.compile(r"\b(interview|phone screen|technical screen|onsite|on-site|next round|hiring manager"
                       r"|schedule (?:a )?(?:call|time)|your availability|availability for)\b", re.I)
RECRUITER = re.compile(r"\b(recruiter|recruiting|talent acquisition|reaching out|came across your profile"
                       r"|opportunity at|role at)\b", re.I)
REJECTION = re.compile(r"\b(unfortunately|not (?:be )?moving forward|other candidates|decided to pursue"
                       r"|not selected|position has been filled)\b", re.I)
WHEN = re.compile(r"\b((?:mon|tues|wednes|thurs|fri|satur|sun)day(?:,? \w+ \d{1,2})?(?: at)? \d{1,2}(?::\d{2})?"
                  r" ?(?:am|pm)?(?: [A-Z]{2,4})?)", re.I)
COMPANY = re.compile(r"(?:\bat|\bfrom|\bwith) ([A-Z][\w&.]*(?: [A-Z][\w&.]*){0,2})")
ROLE = re.compile(r"\b((?:senior |staff |lead |sr\.? )?(?:machine learning|ml|ai|data|software|applied|research)"
                  r"[\w ]{0,20}?(?:engineer|scientist|analyst))\b", re.I)


@dataclass
class JobMail:
    kind: str  # interview | recruiter | rejection | other
    email: Email
    company: str = ""
    role: str = ""
    when: str = ""


def classify_job_mail(e: Email) -> JobMail:
    text = f"{e.subject}\n{e.body}"
    kind = ("rejection" if REJECTION.search(text) else "interview" if INTERVIEW.search(text)
            else "recruiter" if RECRUITER.search(text) else "other")
    company = ""
    if m := COMPANY.search(e.subject) or COMPANY.search(e.body):
        company = m.group(1)
    elif "@" in e.sender:
        company = e.sender.split("@")[-1].split(".")[0].title()
    role = m.group(1) if (m := ROLE.search(text)) else ""
    when = m.group(1) if (m := WHEN.search(e.body)) else ""
    return JobMail(kind, e, company, role, when)


# ---- briefs, alerts, scheduler -------------------------------------------------------------------

class Proactive:
    def __init__(self, db: DB, settings: Settings, mail: Mail, calendar: Calendar, jobs: Jobs, budget: Budget,
                 wins: Wins, notifier: Notifier, clock=time.time, models=None):
        self.db, self.settings, self.mail, self.cal, self.jobs = db, settings, mail, calendar, jobs
        self.budget, self.wins, self.notifier, self.clock = budget, wins, notifier, clock
        self.models = models  # maxie.models.Models: "Upgraded X -> Y" lines in the brief
        self.models_check = None  # () -> list, set by the hub when there is an API client
        self.sessions = None  # hands.sessions.Watcher, set by services.build
        self.events = None  # maxie.events.Events, set by the hub: drives the mascot
        self.moments = None  # maxie.mood.Moments, set by the hub: rare idle moments
        self.presence = None  # maxie.presence.Presence, set by the hub: is he asleep
        self.watch = None  # maxie.watch.Watch, set by the hub: habits, suggestions, routines
        self.experiment = None  # maxie.warm_experiment.WarmExperiment: 7 days off, 7 on, keep the winner
        self._applied: tuple[str, int] | None = None
        self._was_quiet: bool | None = None
        self.on_step_error: Callable[[str], None] = print  # the hub's log
        self._step_failed: dict[str, float] = {}  # step -> when its failure was last reported
        db.script("""CREATE TABLE IF NOT EXISTS alerts_sent(key TEXT PRIMARY KEY, ts REAL);
                     CREATE TABLE IF NOT EXISTS held(id INTEGER PRIMARY KEY, title TEXT, body TEXT, tag TEXT, mood TEXT)""")

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.settings.timezone)

    def now(self) -> datetime:
        return datetime.fromtimestamp(self.clock(), self.tz)

    # -- quiet hours + delivery --
    def quiet(self, at: datetime | None = None) -> bool:
        at = at or self.now()
        hm = at.strftime("%H:%M")
        start, end = self.settings.quiet_start, self.settings.quiet_end
        return (hm >= start or hm < end) if start > end else (start <= hm < end)

    def asleep(self) -> bool:
        """He is asleep: from presence when the hub has it, else the clock."""
        return self.presence.asleep() if self.presence is not None else self.quiet()

    def _once(self, key: str) -> bool:
        """True the first time a key is seen (so every alert is sent once)."""
        cur = self.db.execute("INSERT OR IGNORE INTO alerts_sent VALUES (?,?)", (key, self.clock()))
        return cur.rowcount == 1

    def deliver(self, title: str, body: str, tag: str = "", mood: str = "") -> bool:
        if self.settings.proactive_muted:
            return False
        if self.asleep():
            self.db.execute("INSERT INTO held(title,body,tag,mood) VALUES (?,?,?,?)", (title, body, tag, mood))
            return False
        self.notifier.push(title, body, tag, mood)
        return True

    def release_held(self) -> int:
        if self.asleep() or self.settings.proactive_muted:
            return 0
        rows = self.db.all("SELECT * FROM held ORDER BY id")
        for r in rows:
            self.notifier.push(r["title"], r["body"], r["tag"], r["mood"])
        self.db.execute("DELETE FROM held")
        return len(rows)

    # -- briefs --
    def _events_line(self, start: datetime, end: datetime, label: str) -> str:
        events = self.cal.events(start, end)
        if not events:
            return f"{label}: nothing on the calendar."
        items = [f"{datetime.fromisoformat(e.start).astimezone(self.tz).strftime('%-I:%M %p')} {e.title}" for e in events]
        return f"{label}: " + "; ".join(items) + "."

    def build(self, kind: str = "morning") -> str:
        return self.morning() if kind == "morning" else self.evening()

    # Real sources only: a source that isn't connected says so in one line, with the action that fixes it.
    @property
    def mail_connected(self) -> bool:
        return getattr(self.mail, "connected", True)

    @property
    def calendar_connected(self) -> bool:
        return getattr(self.cal, "connected", True)

    def brief(self) -> Brief:
        """The morning brief as short sections: Calendar, Email, Jobs, Spend. Before noon it is "Good
        morning"; later in the day the same brief is "Today so far" (no greeting at 1:37 PM)."""
        now = self.now()
        title = "Good morning" if now.hour < MORNING_UNTIL_H else "Today so far"
        sections = [self._guarded("Calendar", lambda: self._calendar(now)), self._guarded("Email", lambda: self._email(now)),
                    self._guarded("Jobs", self._jobs), self._guarded("Spend", self._spend)]
        return Brief(title, now.strftime("%A, %B %-d"), [*sections, *self._sleep()])

    @staticmethod
    def _guarded(name: str, build: Callable[[], BriefSection]) -> BriefSection:
        """One failing source never fails the brief. It
        becomes one line, with Settings › Connections to check; the rest still shows."""
        try:
            return build()
        except GoogleRefused as e:
            log.warning("brief: %s", e)
            return BriefSection(name, missing=REFUSED, action="open_connections")
        except Exception:
            log.exception("brief: %s failed", name)
            return BriefSection(name, missing=f"{name} isn't answering right now", action="open_connections")

    def _sleep(self) -> list[BriefSection]:
        """"Slept 2:05 to 9:40", only if he turned it on (Settings › Maxie) and Maxie saw him sleep."""
        line = self.presence.last_night() if self.presence is not None and self.settings.brief_sleep else None
        return [BriefSection("Sleep", [line])] if line else []

    def _calendar(self, now: datetime) -> BriefSection:
        if not self.calendar_connected:
            return BriefSection("Calendar", missing="Google Calendar not connected", action="connect_google")
        day_end = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
        events = self.cal.events(now, day_end)
        lines = [f"{datetime.fromisoformat(e.start).astimezone(self.tz):%-I:%M %p} {e.title}" for e in events[:6]]
        return BriefSection("Calendar", lines or ["Nothing else today."])

    def _email(self, now: datetime) -> BriefSection:
        if not self.mail_connected:
            return BriefSection("Email", missing="Gmail not connected", action="connect_google")
        since = now - timedelta(hours=18)
        important = [e for e in self.mail.search(MAIN, since=since) if e.important and e.unread]
        lines = [f"{e.sender.split('<')[0].strip()}: {e.subject.rstrip('.?!')}" for e in important[:3]]
        for jm in (classify_job_mail(e) for e in self.mail.search(JOB, since=since)):
            if jm.kind in ("interview", "recruiter"):
                lines.append(f"{'Interview' if jm.kind == 'interview' else 'Recruiter'}: {jm.company}"
                             + (f", {jm.role}" if jm.role else "") + (f", {jm.when}" if jm.when else ""))
        return BriefSection("Email", lines or ["Nothing important."])

    def _jobs(self) -> BriefSection:
        new, due, ready = self.jobs.new_jobs(), self.jobs.followups_due(), self.jobs.resumes_ready()
        if getattr(self.jobs, "error", None):  # never the raw error: what it means, and the fix
            if getattr(self.jobs, "unlinked", False):
                return BriefSection("Jobs", missing="jobhunt not linked", action="link_jobhunt")
            return BriefSection("Jobs", missing="jobhunt isn't answering", action="open_connections")
        lines = []
        if new:
            lines.append(f"{len(new)} new sponsor-friendly job{'s' if len(new) > 1 else ''}, top: {new[0]['title']} at {new[0]['company']}")
        if due:
            lines.append(f"{len(due)} follow-up{'s' if len(due) > 1 else ''} due: " + ", ".join(f['company'] for f in due[:3]))
        if ready:
            lines.append(f"{len(ready)} resume{'s' if len(ready) > 1 else ''} ready to review")
        if wins := self.wins.since(7):
            lines.append(f"{len(wins)} win{'s' if len(wins) > 1 else ''} this week, latest: {wins[-1]['text']}")
        return BriefSection("Jobs", lines or ["Nothing new."])

    def _spend(self) -> BriefSection:
        return BriefSection("Spend", [self.budget.status().line(), *(self.models.lines() if self.models else [])])

    def morning(self) -> str:
        return self.brief().text()

    def evening(self) -> str:
        now = self.now()
        today = now.strftime("%Y-%m-%d")
        applied, goal = self.jobs.applied_on(today), self.settings.daily_application_goal
        lines = [f"Wrap-up. You applied to {applied} of {goal} today."]
        if applied >= goal:
            lines.append("Goal hit. Nice work.")
        replies = [classify_job_mail(e) for e in self.mail.search(JOB, since=now.replace(hour=0, minute=0))] \
            if self.mail_connected else []
        positive = [r for r in replies if r.kind in ("interview", "recruiter")]
        if positive:
            lines.append(f"{len(positive)} repl{'ies' if len(positive) > 1 else 'y'} today: "
                         + ", ".join(r.company for r in positive) + ".")
        tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        if self.calendar_connected:
            lines.append(self._tomorrow_line(tomorrow))
        lines.append("Get some rest; tomorrow's a new batch.")
        return " ".join(lines)

    def _tomorrow_line(self, tomorrow: datetime) -> str:
        """The wrap's calendar line; a refused or broken calendar is one sentence, never a failed wrap."""
        try:
            return self._events_line(tomorrow, tomorrow + timedelta(days=1), "Tomorrow")
        except GoogleRefused as e:
            log.warning("wrap: %s", e)
            return f"Tomorrow: {REFUSED}."
        except Exception:
            log.exception("wrap: calendar failed")
            return "Tomorrow: the calendar isn't answering right now."

    # -- jobs run by the scheduler --
    def morning_job(self) -> None:
        if self._once(f"morning:{self.now():%Y-%m-%d}"):
            self.deliver("Morning brief", self.morning(), "brief")

    def evening_job(self) -> None:
        if self._once(f"evening:{self.now():%Y-%m-%d}"):
            self.deliver("Today's wrap", self.evening(), "brief")

    def check_job_inbox(self) -> list[JobMail]:
        """Push interview / recruiter / rejection mail within minutes. Returns what was alerted."""
        alerted = []
        if not self.mail_connected:
            return alerted
        for e in self.mail.search(JOB, since=self.now() - timedelta(days=2)):
            jm = classify_job_mail(e)
            if jm.kind == "other" or not self._once(f"mail:{e.id}"):
                continue
            alerted.append(jm)
            if jm.kind in ("interview", "recruiter") and self.events and not self.asleep():
                self.events.set_state("happy")
            if jm.kind == "interview":
                self.wins.add("interview", f"interview request from {jm.company}")
                body = (f"{jm.company}" + (f", {jm.role}" if jm.role else "") + (f", {jm.when}" if jm.when else "")
                        + ". " + self.prep_sheet(jm))
                self.deliver(f"Interview: {jm.company}", body, "interview", mood="happy")
            elif jm.kind == "recruiter":
                self.wins.add("reply", f"recruiter from {jm.company}")
                self.deliver(f"Recruiter: {jm.company}", f"{jm.email.subject}" + (f" ({jm.role})" if jm.role else ""),
                             "recruiter", mood="happy")
            else:
                self.deliver(f"{jm.company} replied", "Not this one. It's their loss, and it's a numbers game; "
                             "you're still moving.", "rejection", mood="sad")
        return alerted

    def check_applications(self) -> int:
        """When jobhunt marks another application as applied today: a win, and Maxie is proud."""
        day = self.now().strftime("%Y-%m-%d")
        n = self.jobs.applied_on(day)
        new = n - self._applied[1] if self._applied and self._applied[0] == day else 0
        self._applied = (day, n)
        if new > 0:
            self.wins.add("applied", f"application {n} today")
            if self.events:
                self.events.set_state("proud")
            if n == self.settings.daily_application_goal:
                self.deliver("Goal hit", f"{n} applications today. That's the goal. Take a real break.", "goal",
                             mood="proud")
        return new

    def prep_sheet(self, jm: JobMail) -> str:
        job = self.jobs.find_job(jm.company, jm.role)
        if not job:
            return "No matching job in jobhunt; I'll look up the company when you ask."
        parts = [f"Prep: {job['title']} at {job['company']}."]
        if job.get("jd"):
            parts.append("JD: " + job["jd"][:280])
        if job.get("resume_url"):
            parts.append(f"Your tailored resume: {job['resume_url']}")
        else:
            self.jobs.queue_resume(str(job["id"]))
            parts.append("Tailored resume queued in resumegen.")
        return " ".join(parts)

    def meeting_prep(self, lead_minutes: int = 30) -> list[str]:
        now = self.now()
        sent = []
        if not self.calendar_connected:
            return sent
        for e in self.cal.events(now + timedelta(minutes=lead_minutes - 5), now + timedelta(minutes=lead_minutes + 5)):
            if not self._once(f"prep:{e.id}"):
                continue
            who = ", ".join(e.attendees[:4]) or "just you"
            body = f"{e.title} at {datetime.fromisoformat(e.start).astimezone(self.tz):%-I:%M %p} with {who}."
            if e.description:
                body += " Notes: " + e.description[:200]
            body += " Lead with what you've shipped recently and one question about their roadmap."
            self.deliver(f"In {lead_minutes} min: {e.title}", body, "meeting")
            sent.append(e.id)
        return sent

    def tick(self) -> None:
        """Called every minute by the scheduler."""
        hm = self.now().strftime("%H:%M")
        if hm == self.settings.morning_brief_at:
            self._step("morning brief", self.morning_job)
        if hm == self.settings.evening_wrap_at:
            self._step("evening wrap", self.evening_job)
        if hm == self.settings.quiet_end:
            self._step("held alerts", self.release_held)
        if self.now().minute % 5 == 0:
            self._step("job inbox", self.check_job_inbox)
        self._step("meeting prep", self.meeting_prep)
        self._step("applications", self.check_applications)
        if self.sessions is not None:
            self._step("sessions", self.sessions.tick)  # a Claude Code session finished or waits for him
        if self.models_check and self.models and self.models.due():
            self._step("models check", self.models_check)  # once a day: newer stable models (maxie.models)
        if self.presence is not None:
            self._step("presence", self.presence.refresh)  # 30 min without input at night: asleep
        self._step("sleep state", self._follow_sleep)
        if self.watch is not None:
            self._step("watch", self.watch.tick)  # learn once a day, run his routines, now and then ask
        if self.experiment is not None:
            self._step("warm experiment", self.experiment.tick)  # warm Sonnet too? measured, not guessed (§14)
        if self.moments is not None:
            self._step("moments", self.moments.tick)  # rarely, a glance or a hop when nothing is happening

    def _follow_sleep(self) -> None:
        quiet = self.asleep()
        if self.events and quiet != self._was_quiet:
            self.events.rest()  # idle <-> sleep as he falls asleep or wakes
        self._was_quiet = quiet

    def _step(self, name: str, fn: Callable[[], object]) -> None:
        """One check of the minute. A failing one (Google answering 403) never stops the others, and is
        reported once an hour, not every minute."""
        try:
            fn()
        except Exception as e:  # the rest of the minute must still run  # noqa: BLE001
            now = self.clock()
            if now - self._step_failed.get(name, float("-inf")) >= STEP_ERROR_EVERY_S:
                self._step_failed[name] = now
                self.on_step_error(f"{name}: {e}")


async def run_scheduler(p: Proactive, every: float = 60.0, on_error=print) -> None:
    while True:
        try:
            await asyncio.to_thread(p.tick)
        except Exception as e:  # one bad tick must not kill the scheduler  # noqa: BLE001
            on_error(f"proactive tick failed: {e}")
        await asyncio.sleep(every - time.time() % every)
