from datetime import datetime

from maxie.hands.google import Email
from maxie.proactive import classify_job_mail
from tests.conftest import TZ


def test_morning_brief(make_services):
    s = make_services()
    text = s.proactive.morning()
    assert text.startswith("Good morning. It's Thursday, October 15. Calendar:")
    b = {x.name: x for x in s.proactive.brief().sections}
    assert list(b) == ["Calendar", "Email", "Jobs", "Spend"]
    assert b["Calendar"].lines == ["10:00 AM Mock interview with Riley", "6:00 PM Gym"]
    assert "Landlord Co: Rent receipt for October" in b["Email"].lines and "promo" not in text.lower()
    assert "Interview: AcmeAI, Machine Learning Engineer, Thursday at 2:00 pm CT" in b["Email"].lines
    assert any(line.startswith("Recruiter: Nimbus") for line in b["Email"].lines)
    assert b["Jobs"].lines[0].startswith("2 new sponsor-friendly jobs")  # Initech doesn't sponsor
    assert "2 follow-ups due: Hooli, Umbrella" in b["Jobs"].lines and "1 resume ready to review" in b["Jobs"].lines
    assert b["Spend"].lines[0].startswith("API spend: $0.00 today")


def test_evening_wrap(make_services, clock):
    s = make_services()
    clock.set(datetime(2026, 10, 15, 21, 0, tzinfo=TZ))
    text = s.proactive.evening()
    assert text.startswith("Wrap-up. You applied to 2 of 10 today.")
    assert "Tomorrow: 12:30 PM Lunch with Jordan." in text


def test_classify_job_mail():
    def mail(subject, body):
        return classify_job_mail(Email("x", "job", "a <a@corp.example>", subject, body, "2026-10-15T00:00:00-05:00"))
    jm = mail("Interview for Data Scientist at Initech", "Can we schedule a phone screen Monday at 10 am?")
    assert (jm.kind, jm.company, jm.role, jm.when) == ("interview", "Initech", "Data Scientist", "Monday at 10 am")
    assert mail("Update", "Unfortunately we are not moving forward").kind == "rejection"
    assert mail("Weekly digest", "Top stories").kind == "other"


def test_interview_alert_once_with_prep_sheet_and_win(make_services):
    s = make_services()
    s.settings.quiet_end = "07:00"
    alerted = s.proactive.check_job_inbox()
    assert sorted(a.kind for a in alerted) == ["interview", "recruiter", "rejection"]
    sent = s.fakes["notifier"].sent
    interview = next(p for p in sent if p["tag"] == "interview")
    assert interview["title"] == "Interview: AcmeAI" and interview["mood"] == "happy"
    assert "Prep: Machine Learning Engineer at AcmeAI" in interview["body"] and "JD: Build and ship" in interview["body"]
    assert s.fakes["jobs"].queued == ["101"]  # no resume yet -> queued in resumegen
    assert next(p for p in sent if p["tag"] == "rejection")["mood"] == "sad"
    assert s.proactive.check_job_inbox() == []  # never twice
    assert any("AcmeAI" in w["text"] for w in s.proactive.wins.since(1))


def test_quiet_hours_hold_then_release(make_services, clock):
    s = make_services()
    clock.set(datetime(2026, 10, 15, 23, 30, tzinfo=TZ))
    assert s.proactive.quiet()
    s.proactive.check_job_inbox()
    assert s.fakes["notifier"].sent == []
    clock.set(datetime(2026, 10, 16, 7, 0, tzinfo=TZ))
    assert s.proactive.release_held() == 3 and len(s.fakes["notifier"].sent) == 3


def test_muted_sends_nothing(make_services):
    s = make_services()
    s.settings.proactive_muted = True
    s.proactive.morning_job()
    assert s.fakes["notifier"].sent == []


def test_scheduler_tick_morning_and_meeting_prep(make_services, clock):
    s = make_services()
    clock.set(datetime(2026, 10, 15, 8, 0, tzinfo=TZ))
    s.proactive.tick()
    s.proactive.tick()  # same minute again: still one brief
    briefs = [p for p in s.fakes["notifier"].sent if p["tag"] == "brief"]
    assert len(briefs) == 1 and briefs[0]["title"] == "Morning brief"
    clock.set(datetime(2026, 10, 15, 9, 30, tzinfo=TZ))
    s.proactive.tick()
    prep = [p for p in s.fakes["notifier"].sent if p["tag"] == "meeting"]
    assert len(prep) == 1 and "riley@example.com" in prep[0]["body"] and "feature store" in prep[0]["body"]


async def test_brief_me_command(make_services):
    s = make_services()
    events = [e async for e in s.agent.handle("Brief me.")]
    assert events[-1]["text"].startswith("Good morning.")


def test_one_failing_check_never_stops_the_rest_of_the_minute(make_services, clock):
    """Live, 2026-10-16: Google Calendar answered 403 every minute, the whole tick died at meeting prep, and
    watch-and-learn, presence, moments and the warm-Sonnet run never ticked."""
    s = make_services()
    errors = []
    s.proactive.on_step_error = errors.append

    def forbidden(*a, **kw):
        raise RuntimeError("Client error '403 Forbidden'")
    s.proactive.cal.events = forbidden
    s.proactive.tick()
    assert s.experiment.status()["phase"] == "off", "later steps still ran"
    assert errors == ["meeting prep: Client error '403 Forbidden'"]
    s.proactive.tick()
    assert len(errors) == 1, "the same failure is reported once an hour, not every minute"
