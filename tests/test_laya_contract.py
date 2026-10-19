"""Contract test: our LayaClient against the real `laya.serve` HTTP app with a stub model.

Checks the wire format, auth and response parsing without downloading weights. Install with
`uv pip install --no-deps laya==0.4.7` (CI does); skipped when laya isn't installed.
"""

import pytest

laya_serve = pytest.importorskip("laya.serve")
from fastapi.testclient import TestClient  # noqa: E402

from maxie.router.laya import QUESTIONS, LayaClient  # noqa: E402


class StubRouter:
    """Answers like a Laya Router: typed answers with probabilities."""

    def __init__(self):
        self.seen = []

    def predict(self, state, questions, model=None, **kw):
        self.seen.append((state, questions, model))
        return {
            "model": "laya",
            "answers": {
                "route": {"type": "choice", "choice": "mac_action", "confidence": 0.8,
                          "probabilities": {k: (0.94 if k == "mac_action" else 0.01) for k in questions["route"]["criteria"]}},
                "action": {"type": "choice", "choice": "music", "confidence": 0.7,
                           "probabilities": {k: (0.9 if k == "music" else 0.025) for k in questions["action"]["criteria"]}},
                "risk": {"type": "score", "score": 0.1, "confidence": 0.9, "legend": questions["risk"]["criteria"],
                         "probabilities": {"0": 0.92, "1": 0.06, "2": 0.02}},
            },
            "usage": {"input_tokens": 20, "output_tokens": 0},
        }


def test_client_speaks_laya_wire_protocol(monkeypatch):
    monkeypatch.setenv("LAYA_API_KEY", "secret")
    stub = StubRouter()
    http = TestClient(laya_serve.create_app(router=stub))
    client = LayaClient("http://testserver", "secret", client=http)
    r = client.classify("put on some jazz")
    assert (r.route, r.action) == ("mac_action", "music")
    assert r.route_p == 0.94 and r.risk == pytest.approx(0.05)
    assert stub.seen[0][0] == "put on some jazz" and stub.seen[0][1] == QUESTIONS
    assert LayaClient("http://testserver", "wrong", client=http).classify("x") is None  # 401 -> fall back


class StubChooser:
    def predict(self, state, questions, model=None, **kw):
        crit = questions["q"]["criteria"]
        pick = "no" if state.startswith("not") else "yes"
        return {"model": "laya", "usage": {"input_tokens": 5, "output_tokens": 0},
                "answers": {"q": {"type": "choice", "choice": pick, "confidence": 0.9,
                                  "probabilities": {k: (0.97 if k == pick else 0.03) for k in crit}}}}


def test_decide_asks_one_typed_choice(monkeypatch):
    monkeypatch.setenv("LAYA_API_KEY", "secret")
    http = TestClient(laya_serve.create_app(router=StubChooser()))
    client = LayaClient("http://testserver", "secret", client=http)
    crit = {"yes": "agrees", "no": "declines"}
    assert client.decide("not now", "Yes or no?", crit) == ("no", 0.97)
    assert client.decide("sure", "Yes or no?", crit) == ("yes", 0.97)
    assert LayaClient("http://testserver", "wrong", client=http).decide("x", "Yes or no?", crit) is None


def test_laya_server_loads_on_demand_and_gives_memory_back():
    import importlib.util
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / "deploy" / "laya_serve.py"
    spec = importlib.util.spec_from_file_location("laya_serve_cfg", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    env = mod.ENV
    assert env["LAYA_HOST"] == "127.0.0.1" and env["LAYA_PRELOAD"] == "0"
    assert int(env["LAYA_IDLE_UNLOAD_SECONDS"]) > 0 and env["LAYA_MPS_AMP_MIN_ROWS"] == "1"
    plist = (path.parent / "dev.maxie.laya.plist").read_text()
    assert "<key>Nice</key><integer>10</integer>" in plist and "Background" in plist
