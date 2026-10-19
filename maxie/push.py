"""Web push to the iPhone home-screen app (iOS 16.4+), signed with VAPID keys.

Keys: VAPID_PRIVATE_KEY / VAPID_PUBLIC_KEY (Keychain on the Mac). `python -m maxie.push keys`
prints a fresh pair. Subscriptions are stored per device; dead ones (404/410) are dropped.
"""

from __future__ import annotations

import base64
import json
import sys

from .db import DB


class WebPush:
    def __init__(self, db: DB, private_key: str | None, public_key: str | None, contact: str = "mailto:admin@localhost",
                 send=None):
        self.db, self.private_key, self.public_key, self.contact = db, private_key, public_key, contact
        self._send = send  # injectable for tests: (subscription, payload) -> status code
        db.script("CREATE TABLE IF NOT EXISTS push_subs(endpoint TEXT PRIMARY KEY, sub TEXT)")

    @property
    def enabled(self) -> bool:
        return bool(self.private_key and self.public_key) or self._send is not None

    def subscribe(self, sub: dict) -> None:
        if not sub.get("endpoint", "").startswith("https://"):
            raise ValueError("bad subscription")
        self.db.execute("INSERT OR REPLACE INTO push_subs VALUES (?,?)", (sub["endpoint"], json.dumps(sub)))

    def subscriptions(self) -> list[dict]:
        return [json.loads(r["sub"]) for r in self.db.all("SELECT sub FROM push_subs")]

    def _deliver(self, sub: dict, payload: str) -> int:
        if self._send:
            return self._send(sub, payload)
        from pywebpush import WebPushException, webpush
        try:
            r = webpush(sub, payload, vapid_private_key=self.private_key, vapid_claims={"sub": self.contact})
            return r.status_code
        except WebPushException as e:
            return e.response.status_code if e.response is not None else 500

    def push(self, title: str, body: str, tag: str = "", mood: str = "") -> None:
        if not self.enabled:
            return
        payload = json.dumps({"title": title, "body": body, "tag": tag, "mood": mood})
        for sub in self.subscriptions():
            if self._deliver(sub, payload) in (404, 410):
                self.db.execute("DELETE FROM push_subs WHERE endpoint=?", (sub["endpoint"],))


def new_keys() -> tuple[str, str]:
    """(private, public) in the base64url forms pywebpush and the browser expect."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    priv = key.private_numbers().private_value.to_bytes(32, "big")
    pub = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)

    def b64(b):
        return base64.urlsafe_b64encode(b).decode().rstrip("=")

    return b64(priv), b64(pub)


if __name__ == "__main__" and sys.argv[1:] == ["keys"]:
    private, public = new_keys()
    print(f"VAPID_PRIVATE_KEY={private}\nVAPID_PUBLIC_KEY={public}")
