"""Encrypted nightly backups of Maxie's data folder, plus any companion app folders you add (read-only on those).

One archive per run: `maxie-YYYYMMDD-HHMMSS.tar.gz.age`. Inside, per app: a consistent copy of
each SQLite database (the sqlite3 backup API, safe while its server runs), the folders listed for it
(secrets skipped, see SECRET_PATTERNS) and `RESTORE.md`, a checklist of the skipped secrets to re-add.
Encrypted with the age passphrase recipient using the `BACKUP_PASSPHRASE` secret from Settings (never
logged); big archives are split into `<name>.NNN` parts so encryption stays under ~200 MB of memory.
Written to <data>/backups (keeps 7 daily + 4 weekly) and, on a Mac with iCloud Drive turned on, to
iCloud Drive/maxie-backups (keeps 3 daily + 2 weekly, skipped with a warning when the disk has under
5 GB free). Runs at 03:00 (launchd on a Mac), before model upgrades and before schema changes. Skipped when
nothing changed. `verify_latest` restores the newest one into a temp dir and opens every database.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import sqlite3
import tarfile
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

DAILY_KEEP = 7          # local
WEEKLY_KEEP = 4
ICLOUD_DAILY_KEEP = 3   # the off-Mac copy is smaller
ICLOUD_WEEKLY_KEEP = 2
ICLOUD_MIN_FREE = 5 * 1024 ** 3  # skip the iCloud copy below this much free space, and warn
STALE_AFTER = 2 * 86400
PREFIX = "maxie-"
SUFFIX = ".tar.gz.age"
CHUNK = 200 * 1024 * 1024
SECRET_PATTERNS = re.compile(r"(^|/)(\.env[^/]*|.*secret.*|.*token.*|.*credential.*|.*password.*|google_client\.json|"
                             r"google_access.*\.json|.*\.pem|.*\.key|.*\.p12)$", re.I)
SKIP_PATTERNS = re.compile(r"(^|/)(\.git|__pycache__|node_modules|\.venv|backups|.*\.db(-wal|-shm)?|.*\.sock|.*\.lock|.*\.log)$")
RESTORE_HINTS = {"lan_token": "re-add the jobhunt LAN token (jobhunt serve --lan prints one)",
                 "gmail_token": "sign in to Gmail again", "google_token": "sign in to Google again",
                 "google_access": "sign in to Google again", "secrets.yaml": "re-create secrets.yaml from the example",
                 "google_client.json": "download the OAuth client file from Google Cloud console again",
                 "token": "re-create the token file", ".env": "re-create .env from .env.example"}


@dataclass(frozen=True)
class Source:
    """One app: its root, the SQLite databases to copy, and the folders/files to include (relative to root)."""
    name: str
    root: Path
    dbs: tuple[str, ...] = ()
    paths: tuple[str, ...] = ()


def default_sources(data: Path, extra: list[Source] | None = None) -> list[Source]:
    """Maxie's own data folder, plus companion apps (my jobhunt and resumegen, for example) when you list them."""
    return [Source("maxie", data, ("hub.db",), ("settings.json", "router_calibration.json")), *(extra or [])]


@dataclass(frozen=True)
class Dest:
    path: Path
    daily: int = DAILY_KEEP
    weekly: int = WEEKLY_KEEP
    min_free: int = 0  # bytes that must stay free, else this copy is skipped with a warning

    @property
    def name(self) -> str:
        return "iCloud" if "Mobile Documents" in str(self.path) else "local"


def default_dirs(home: Path | None = None) -> list[Dest]:
    home = home or Path.home()
    dests = [Dest(home / ".maxie" / "backups")]
    icloud = home / "Library" / "Mobile Documents" / "com~apple~CloudDocs"
    if icloud.is_dir():
        dests.append(Dest(icloud / "maxie-backups", ICLOUD_DAILY_KEEP, ICLOUD_WEEKLY_KEEP, ICLOUD_MIN_FREE))
    return dests


def free_bytes(path: Path) -> int:
    p = path
    while not p.exists() and p.parent != p:
        p = p.parent
    return shutil.disk_usage(p).free


def is_secret(rel: str) -> bool:
    return bool(SECRET_PATTERNS.search(rel))


def restore_hint(rel: str) -> str:
    name = rel.rsplit("/", 1)[-1]
    return next((hint for key, hint in RESTORE_HINTS.items() if key in name), "re-add by hand")


@dataclass
class AppStatus:
    ok: bool
    files: int = 0
    bytes: int = 0  # uncompressed size of what went in
    note: str = ""


def human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}".replace(".0 ", " ")
        n /= 1024
    return f"{n:.1f} GB"


@dataclass
class BackupStatus:
    last_time: float | None
    last_file: str | None
    last_ok: bool
    last_error: str
    last_restore_check: float | None
    last_restore_ok: bool
    count: int
    dirs: list[str]
    stale: bool  # older than 2 days, or never
    apps: dict[str, AppStatus] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)  # e.g. the iCloud copy was skipped
    last_bytes: int = 0  # size of the encrypted backup

    def when(self, tz=None) -> str:
        return datetime.fromtimestamp(self.last_time, tz).strftime("%b %-d, %-I:%M %p") if self.last_time else "never"

    def line(self, tz=None) -> str:
        if not self.last_time:
            return "Last backup: never"
        mark = "✓" if self.last_ok and not self.stale else "⚠︎"
        return f"Last backup: {self.when(tz)} {mark}" + (" (older than 2 days)" if self.stale else "")

    def app_lines(self, tz=None) -> list[str]:
        """Per app: 'jobhunt ✓ Oct 15, 3:00 AM, 1.8 GB' or 'resumegen ⚠︎ not found'."""
        out = []
        for name, a in self.apps.items():
            mark = "✓" if a.ok and self.last_ok and not self.stale else "⚠︎"
            out.append(f"{name} {mark} " + (f"{self.when(tz)}, {human(a.bytes)}" if a.ok else (a.note or "skipped")))
        return out


LIST_TIMEOUT_S = 20


def _with_timeout(fn, seconds: float):
    """Run fn in a thread; None if it has not returned in time (the thread is left to finish on its own)."""
    import threading
    box: list = []
    t = threading.Thread(target=lambda: box.append(fn()), daemon=True)
    t.start()
    t.join(seconds)
    return box[0] if box else None


class Backup:
    def __init__(self, dirs: list[Dest | Path], sources: list[Source], passphrase: str | None, clock=time.time,
                 log=print, free=free_bytes):
        self.dests = [d if isinstance(d, Dest) else Dest(Path(d)) for d in dirs]
        self.sources, self.passphrase, self.clock, self.log, self.free = sources, passphrase, clock, log, free

    @property
    def dirs(self) -> list[Path]:
        return [d.path for d in self.dests]

    # ---- state (kept in the first destination)

    @property
    def state_path(self) -> Path:
        return self.dirs[0] / "state.json"

    def _state(self) -> dict:
        try:
            return json.loads(self.state_path.read_text())
        except (OSError, ValueError):
            return {}

    def _save_state(self, **changes) -> None:
        self.dirs[0].mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps({**self._state(), **changes}, indent=1))

    def status(self) -> BackupStatus:
        s = self._state()
        last = s.get("last_time")
        apps = {k: AppStatus(**v) for k, v in s.get("apps", {}).items()}
        return BackupStatus(last, s.get("last_file"), bool(s.get("last_ok", False)), s.get("last_error", ""),
                            s.get("last_restore_check"), bool(s.get("last_restore_ok", False)),
                            len(self.list_backups()), [str(d) for d in self.dirs],
                            stale=not last or self.clock() - last > STALE_AFTER, apps=apps,
                            warnings=list(s.get("warnings", [])), last_bytes=int(s.get("last_cipher_bytes", 0)))

    def list_backups(self, where: Path | None = None) -> list[str]:
        """Backup names (without part numbers), oldest first."""
        d = where or self.dirs[0]
        if not d.is_dir():
            return []
        names = {re.sub(r"\.\d{3}$", "", p.name) for p in d.glob(f"{PREFIX}*{SUFFIX}*") if p.is_file()}
        return sorted(names)

    def parts(self, name: str, where: Path | None = None) -> list[Path]:
        d = where or self.dirs[0]
        return sorted(p for p in d.glob(f"{name}*") if p.is_file() and (p.name == name or re.fullmatch(re.escape(name) + r"\.\d{3}", p.name)))

    # ---- what changed

    def signature(self) -> str:
        """Cheap change detection: db file sizes and mtimes, and every included file's size and mtime."""
        h = hashlib.sha256()
        for src in self.sources:
            for rel in src.dbs:
                for p in (src.root / rel, src.root / (rel + "-wal")):
                    # an empty -wal appears when our own read-only copy opens the db: not a change
                    if p.exists() and (p.stat().st_size or not p.name.endswith("-wal")):
                        st = p.stat()
                        h.update(f"{src.name}/{rel}:{st.st_size}:{st.st_mtime_ns}\n".encode())
            for rel, p in self._files(src):
                st = p.stat()
                h.update(f"{src.name}/{rel}:{st.st_size}:{st.st_mtime_ns}\n".encode())
        return h.hexdigest()

    def _files(self, src: Source) -> list[tuple[str, Path]]:
        """(relative path, file) for every included, non-secret, non-skipped file of a source."""
        out = []
        for rel in src.paths:
            base = src.root / rel
            if base.is_file():
                candidates = [base]
            elif base.is_dir():
                candidates = sorted(p for p in base.rglob("*") if p.is_file())
            else:
                continue
            for p in candidates:
                r = p.relative_to(src.root).as_posix()
                if SKIP_PATTERNS.search(r) or is_secret(r):
                    continue
                out.append((r, p))
        return out

    def _secrets(self, src: Source) -> list[str]:
        found = []
        for rel in src.paths:
            base = src.root / rel
            items = [base] if base.is_file() else sorted(base.rglob("*")) if base.is_dir() else []
            for p in items:
                r = p.relative_to(src.root).as_posix()
                if p.is_file() and is_secret(r) and not SKIP_PATTERNS.search(r):
                    found.append(r)
        for extra in (".env",):
            if (src.root / extra).is_file():
                found.append(extra)
        return sorted(set(found))

    # ---- making one

    def _archive(self, tmp: Path) -> tuple[Path, dict[str, AppStatus], str]:
        """Streams the tar.gz to disk. Returns (path, per-app status, RESTORE.md text)."""
        out = tmp / "backup.tar.gz"
        apps: dict[str, AppStatus] = {}
        checklist = ["# Restore checklist", "",
                     "Restore: `maxie restore <backup> <folder>` (or `age -d` + `tar xz`), then put each",
                     "app's `<app>/<db>.sqlite` back as its database and copy the folders back.", "",
                     "Secrets were skipped on purpose. After restoring, re-add:"]
        with tarfile.open(out, "w:gz") as tar:
            for src in self.sources:
                if not src.root.is_dir():
                    apps[src.name] = AppStatus(False, note="not found")
                    continue
                n, size = 0, 0
                for rel in src.dbs:
                    db = src.root / rel
                    if not db.exists():
                        continue
                    copy = tmp / f"{src.name}-{Path(rel).name}"
                    dst = sqlite3.connect(copy)
                    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
                    conn.backup(dst)
                    conn.close()
                    dst.close()
                    tar.add(copy, arcname=f"{src.name}/{Path(rel).name}.sqlite")
                    n, size = n + 1, size + copy.stat().st_size
                    copy.unlink()
                for rel, p in self._files(src):
                    tar.add(p, arcname=f"{src.name}/{rel}", recursive=False)
                    n, size = n + 1, size + p.stat().st_size
                apps[src.name] = AppStatus(True, n, size)
                for rel in self._secrets(src):
                    checklist.append(f"- {src.name}/{rel}: {restore_hint(rel)}")
            text = "\n".join(checklist) + "\n"
            info = tarfile.TarInfo("RESTORE.md")
            info.size, info.mtime = len(text.encode()), int(self.clock())
            tar.addfile(info, io.BytesIO(text.encode()))
        return out, apps, text

    def encrypt(self, plain: bytes) -> bytes:
        from pyrage import passphrase
        if not self.passphrase:
            raise RuntimeError("BACKUP_PASSPHRASE is not in the Keychain")
        return passphrase.encrypt(plain, self.passphrase)

    def decrypt(self, cipher: bytes) -> bytes:
        from pyrage import passphrase
        if not self.passphrase:
            raise RuntimeError("BACKUP_PASSPHRASE is not in the Keychain")
        return passphrase.decrypt(cipher, self.passphrase)

    def run(self, reason: str = "scheduled", force: bool = False) -> str | None:
        """Write a new encrypted backup to every destination. Returns its name, or None (unchanged / failed)."""
        try:
            sig = self.signature()
            if not force and sig == self._state().get("last_hash"):
                self.log(f"backup: nothing changed since the last one ({reason})")
                return None
            if not self.passphrase:
                raise RuntimeError("BACKUP_PASSPHRASE is not in the Keychain")
            name = f"{PREFIX}{datetime.fromtimestamp(self.clock()):%Y%m%d-%H%M%S}{SUFFIX}"
            warnings, cipher_total = [], 0
            with tempfile.TemporaryDirectory() as tmp:
                archive, apps, _ = self._archive(Path(tmp))
                total = archive.stat().st_size
                parts = max(1, -(-total // CHUNK))
                targets = []
                for d in self.dests:
                    free = self.free(d.path)
                    if d.min_free and free - total < d.min_free:
                        warnings.append(f"{d.name} copy skipped: {human(free)} free, under {human(d.min_free)} + backup")
                        self.log("backup: " + warnings[-1])
                        continue
                    targets.append(d.path)
                written = dict.fromkeys(targets, 0)
                with open(archive, "rb") as fh:
                    for i in range(parts):
                        cipher = self.encrypt(fh.read(CHUNK))
                        cipher_total += len(cipher)
                        part = name if parts == 1 else f"{name}.{i:03d}"
                        for d in targets:
                            try:
                                d.mkdir(parents=True, exist_ok=True)
                                (d / part).write_bytes(cipher)
                                written[d] += 1
                            except OSError as e:
                                self.log(f"backup: could not write to {d}: {e}")
            ok_dirs = [d for d, n in written.items() if n == parts]
            if not ok_dirs:
                raise OSError("no destination was writable")
            for d in set(targets) - set(ok_dirs):  # half-written sets are useless: drop them
                warnings.append(f"{d} failed")
                for p in self.parts(name, d):
                    p.unlink(missing_ok=True)
            self._save_state(last_hash=sig, last_time=self.clock(), last_file=name, last_ok=True, last_error="",
                             last_reason=reason, apps={k: asdict(v) for k, v in apps.items()}, warnings=warnings,
                             last_bytes=total, last_cipher_bytes=cipher_total, last_parts=parts,
                             dirs_ok=[str(d) for d in ok_dirs])
            self.prune()
            self.log(f"backup: {name} ({human(cipher_total)}, {parts} part(s)) to {len(ok_dirs)} place(s), {reason}")
            return name
        except Exception as e:  # a backup failure is reported in the status, never raised  # noqa: BLE001
            self._save_state(last_ok=False, last_error=str(e)[:200])
            self.log(f"backup failed: {e}")
            return None

    # ---- retention: the newest 7, plus the newest of each of the 4 most recent earlier ISO weeks

    @staticmethod
    def keep_set(names: list[str], daily: int = DAILY_KEEP, weekly: int = WEEKLY_KEEP) -> set[str]:
        newest_first = sorted(names, reverse=True)
        keep = set(newest_first[:daily])
        weeks: dict[tuple[int, int], str] = {}
        for n in newest_first[daily:]:
            try:
                when = datetime.strptime(n[len(PREFIX):-len(SUFFIX)], "%Y%m%d-%H%M%S")
            except ValueError:
                continue
            weeks.setdefault(when.isocalendar()[:2], n)
        for key in sorted(weeks, reverse=True)[:weekly]:
            keep.add(weeks[key])
        return keep

    def prune(self) -> list[Path]:
        """Best effort: a listing that hangs (iCloud Drive syncing or asking for access) is skipped, never waited on."""
        removed = []
        for dest in self.dests:
            d = dest.path
            names = _with_timeout(lambda d=d: self.list_backups(d), LIST_TIMEOUT_S)
            if names is None:
                self.log(f"backup: listing {d} took more than {LIST_TIMEOUT_S}s; prune skipped there")
                continue
            for n in set(names) - self.keep_set(names, dest.daily, dest.weekly):
                for p in self.parts(n, d):
                    try:
                        p.unlink()
                        removed.append(p)
                    except OSError as e:
                        self.log(f"backup: could not delete {p}: {e}")
        return removed

    # ---- restoring

    def restore(self, name: str, into: Path, where: Path | None = None) -> list[Path]:
        """Decrypts backup `name` into `into`. Returns the restored .sqlite database files."""
        into.mkdir(parents=True, exist_ok=True)
        parts = self.parts(name, where)
        if not parts:
            raise FileNotFoundError(name)
        archive = into / "backup.tar.gz"
        with open(archive, "wb") as out:
            for p in parts:
                out.write(self.decrypt(p.read_bytes()))
        dbs = []
        with tarfile.open(archive, "r:gz") as tar:
            for m in tar.getmembers():
                if m.name.startswith(("/", "..")) or "/../" in m.name or not (m.isfile() or m.isdir()):
                    raise ValueError(f"unsafe member in backup: {m.name}")
            tar.extractall(into, filter="data")
            dbs = [into / m.name for m in tar.getmembers() if m.isfile() and m.name.endswith(".sqlite")]
        archive.unlink()
        return dbs

    def verify_latest(self) -> bool:
        """Restore the newest backup into a temp dir and open every database. Recorded in the state."""
        names = self.list_backups()
        ok, error = False, ""
        if not names:
            error = "no backup to verify"
        else:
            try:
                with tempfile.TemporaryDirectory() as tmp:
                    dbs = self.restore(names[-1], Path(tmp))
                    bad = []
                    for db in dbs:
                        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
                        check = conn.execute("PRAGMA integrity_check").fetchone()[0]
                        tables = conn.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
                        conn.close()
                        if check != "ok" or tables == 0:
                            bad.append(f"{db.parent.name}/{db.name}: {check}, {tables} tables")
                    ok = bool(dbs) and not bad
                    error = "; ".join(bad) if bad else ("" if dbs else "no databases in the backup")
            except Exception as e:  # reported in the status  # noqa: BLE001
                error = str(e)[:200]
        self._save_state(last_restore_check=self.clock(), last_restore_ok=ok, last_restore_error=error)
        self.log("backup: restore check " + ("ok" if ok else f"FAILED: {error}"))
        return ok


def status_dict(b: Backup, tz=None) -> dict:
    s = b.status()
    return {**asdict(s), "line": s.line(tz) + (f", {human(s.last_bytes)}" if s.last_bytes else ""),
            "app_lines": s.app_lines(tz)}
