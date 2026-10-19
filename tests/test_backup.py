"""Encrypted backups: three apps, skip-if-unchanged, retention per destination, iCloud free-space rule,
secrets skipped with a restore checklist, restore test of every database, panel lines and API."""

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from maxie.backup import (
    ICLOUD_MIN_FREE,
    PREFIX,
    SUFFIX,
    Backup,
    Dest,
    Source,
    is_secret,
    status_dict,
)
from tests.conftest import TZ

PASS = "test-passphrase-not-real"


def make_db(path: Path, rows: int = 3) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("CREATE TABLE IF NOT EXISTS t(x)")
    c.executemany("INSERT INTO t VALUES (?)", [(i,) for i in range(rows)])
    c.commit()
    c.close()


@pytest.fixture
def world(tmp_path, clock):
    """Three fake apps, a local and an 'iCloud' destination."""
    claude, jobhunt, resumegen = tmp_path / "claude", tmp_path / "jobhunt", tmp_path / "resumegen"
    make_db(claude / "hub.db")
    (claude / "settings.json").write_text("{}")
    make_db(jobhunt / "data" / "jobhunt.db", 10)
    (jobhunt / "Prompts").mkdir(parents=True)
    (jobhunt / "Prompts" / "GOAL1.md").write_text("plan")
    (jobhunt / "extension").mkdir()
    (jobhunt / "extension" / "panel.js").write_text("js")
    (jobhunt / "config").mkdir()
    (jobhunt / "config" / "settings.yaml").write_text("a: 1")
    (jobhunt / "config" / "secrets.yaml").write_text("key: SECRET")
    (jobhunt / "config" / "google_client.json").write_text("{}")
    (jobhunt / "data" / "lan_token").write_text("tok")
    (jobhunt / ".env").write_text("X=1")
    make_db(resumegen / "data" / "resumegen.db", 5)
    (resumegen / "data" / "runs").mkdir(parents=True)
    (resumegen / "data" / "runs" / "resume.pdf").write_bytes(b"%PDF")
    (resumegen / "data" / "google_access.json").write_text("{}")
    (resumegen / "data" / "serve.log").write_text("log")
    sources = [Source("claude", claude, ("hub.db",), ("settings.json", "router_calibration.json")),
               Source("jobhunt", jobhunt, ("data/jobhunt.db",), ("Prompts", "extension", "config")),
               Source("resumegen", resumegen, ("data/resumegen.db",), ("data", "config"))]
    local = Dest(tmp_path / "local")
    icloud = Dest(tmp_path / "Mobile Documents" / "maxie-backups", 3, 2, ICLOUD_MIN_FREE)
    free = {"value": 50 * 1024 ** 3}
    b = Backup([local, icloud], sources, PASS, clock=clock, log=lambda m: None, free=lambda p: free["value"])
    return b, free, local.path, icloud.path, jobhunt


def test_backup_writes_both_places_and_skips_when_unchanged(world):
    b, _, local, icloud, _ = world
    name = b.run("test")
    assert name and name.startswith(PREFIX) and name.endswith(SUFFIX)
    assert (local / name).exists() and (icloud / name).exists()
    assert (local / name).read_bytes()[:3] == b"age"  # age format, not plaintext
    st = b.status()
    assert st.last_ok and not st.stale and st.count == 1
    assert set(st.apps) == {"claude", "jobhunt", "resumegen"} and all(a.ok for a in st.apps.values())
    assert st.apps["jobhunt"].bytes > st.apps["claude"].bytes > 0
    assert b.run("again") is None  # nothing changed
    assert b.run("forced", force=True) is not None


def test_secrets_are_skipped_and_listed_in_the_checklist(world, tmp_path):
    b, _, local, _, jobhunt = world
    name = b.run("test")
    out = tmp_path / "restored"
    dbs = b.restore(name, out)
    assert sorted(d.name for d in dbs) == ["hub.db.sqlite", "jobhunt.db.sqlite", "resumegen.db.sqlite"]
    files = {p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file()}
    assert "jobhunt/config/settings.yaml" in files and "jobhunt/Prompts/GOAL1.md" in files
    assert "resumegen/data/runs/resume.pdf" in files
    assert not any("secrets.yaml" in f or "google_client" in f or "lan_token" in f or "google_access" in f
                   or f.endswith(".env") or f.endswith(".log") for f in files)
    checklist = (out / "RESTORE.md").read_text()
    assert "jobhunt/config/secrets.yaml" in checklist and "jobhunt/.env" in checklist
    assert "resumegen/data/google_access.json" in checklist and "sign in to Google" in checklist
    # the restored jobhunt db has its rows
    assert sqlite3.connect(out / "jobhunt" / "jobhunt.db.sqlite").execute("SELECT count(*) FROM t").fetchone()[0] == 10


def test_is_secret_patterns():
    assert is_secret("config/secrets.yaml") and is_secret("data/gmail_token_alex.json") and is_secret(".env.local")
    assert is_secret("data/token") and is_secret("config/google_client.json") and is_secret("x/server.key")
    assert not is_secret("config/settings.yaml") and not is_secret("Prompts/GOAL1.md")


def test_restore_check_opens_every_db(world):
    b, _, local, _, _ = world
    assert not b.verify_latest()  # nothing yet
    b.run("test")
    assert b.verify_latest()
    st = b.status()
    assert st.last_restore_ok and st.last_restore_check
    # a corrupted archive fails the check
    for p in b.parts(st.last_file, local):
        p.write_bytes(b"age-encryption.org/v1\nbroken")
    assert not b.verify_latest() and not b.status().last_restore_ok


def test_retention_local_7_plus_4_and_icloud_3_plus_2(world, clock):
    b, _, local, icloud, _ = world
    b.encrypt = b.decrypt = lambda data: data  # age's scrypt costs ~1 s a call; retention is about names
    start = datetime(2026, 8, 1, 3, 0, tzinfo=TZ)
    for day in range(40):
        clock.set(start + timedelta(days=day))
        b.run("nightly", force=True)
    local_names, icloud_names = b.list_backups(local), b.list_backups(icloud)
    assert len(local_names) == 7 + 4 and len(icloud_names) == 3 + 2
    newest = sorted(local_names)[-7:]
    assert sorted(icloud_names)[-3:] == newest[-3:]
    weeks = {datetime.strptime(n[len(PREFIX):-len(SUFFIX)], "%Y%m%d-%H%M%S").isocalendar()[:2]
             for n in set(local_names) - set(newest)}
    assert len(weeks) == 4  # one per earlier week


def test_icloud_skipped_when_disk_is_nearly_full(world):
    b, free, local, icloud, _ = world
    free["value"] = 2 * 1024 ** 3
    name = b.run("test")
    assert (local / name).exists() and not (icloud / name).exists()
    st = b.status()
    assert st.last_ok and st.warnings and "iCloud copy skipped" in st.warnings[0]


def test_lines_for_panel(world, clock):
    b, _, _, _, _ = world
    assert b.status().line() == "Last backup: never" and b.status().stale
    b.run("test")
    d = status_dict(b, TZ)
    assert d["line"].startswith("Last backup: Oct 15, 7:59 AM ✓")
    assert d["app_lines"][0].startswith("claude ✓ Oct 15, 7:59 AM, ")
    assert any(line.startswith("jobhunt ✓") for line in d["app_lines"])
    clock.t += 3 * 86400
    d = status_dict(b, TZ)
    assert d["stale"] and "older than 2 days" in d["line"] and d["app_lines"][0].startswith("claude ⚠︎")


def test_missing_app_is_reported_not_fatal(world, tmp_path):
    b, _, _, _, _ = world
    b.sources.append(Source("ghost", tmp_path / "nope", ("x.db",), ()))
    assert b.run("test")
    st = b.status()
    assert st.last_ok and not st.apps["ghost"].ok and st.apps["ghost"].note == "not found"
    assert any(line.startswith("ghost ⚠︎ not found") for line in st.app_lines())


def test_no_passphrase_is_a_reported_failure(world):
    b, _, _, _, _ = world
    b.passphrase = None
    assert b.run("test") is None
    assert not b.status().last_ok and "BACKUP_PASSPHRASE" in b.status().last_error


def test_api_and_hooks(make_services, tmp_path, clock):
    from fastapi.testclient import TestClient

    from maxie.hub.api import create_app
    from maxie.models import ModelInfo

    s = make_services(backup_dirs=[tmp_path / "bk"])
    s.backup.passphrase = PASS
    s.backup.sources = [Source("claude", tmp_path, ("hub.db",), ())]
    make_db(tmp_path / "hub.db")
    c = TestClient(create_app(s, "t", scheduler=False))
    h = {"Authorization": "Bearer t"}
    assert c.get("/api/backup", headers=h).json()["line"] == "Last backup: never"
    r = c.post("/api/backup?verify=true", headers=h).json()
    assert r["name"] and r["verified"] and r["last_ok"]
    # a model upgrade takes a backup first
    before = s.backup.status().last_time
    clock.t += 60
    s.models.check(lambda: [ModelInfo("claude-sonnet-6", clock.t)], lambda t, m: None,
                   before_switch=lambda tier, new: s.backup.run(f"before {tier} upgrade", force=True))
    assert s.backup.status().last_time > before


def test_restore_accepts_a_name_or_a_part_file(tmp_path):
    from maxie.cli import restore_target
    (tmp_path / "maxie-20261015-005918.tar.gz.age.002").write_bytes(b"x")
    assert restore_target("maxie-20261015-005918") == ("maxie-20261015-005918", None)
    assert restore_target(str(tmp_path / "maxie-20261015-005918.tar.gz.age.002")) == \
        ("maxie-20261015-005918.tar.gz.age", tmp_path)
