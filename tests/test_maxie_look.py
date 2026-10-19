"""GOAL5 Phase 3 › Settings › Maxie: his look (Mochi without headphones by default), the Classic robot as an
option, personality, chattiness, voice and speed, and the notch loops re-rendered on the Mac when the look
changes (a background, low-priority job; the bundled loops stay until the new ones are complete)."""

import json
import re
from pathlib import Path

import pytest

from maxie import maxie_look as ml
from maxie.brain import context_line
from maxie.config import Settings

WEB = Path(__file__).resolve().parents[1] / "web" / "maxie.js"


def js_list(name: str) -> list[str]:
    src = WEB.read_text()
    m = re.search(rf"const {name} = (\[[^\]]*\]|\{{[^}}]*\}});", src)
    body = m.group(1)
    return re.findall(r'"([a-z]+)"', body) if body.startswith("[") else re.findall(r"(\w+):", body)


def test_the_default_is_exactly_his_pick():
    assert ml.DEFAULT_LOOK == {"fur": "mochi", "furLength": "long", "ears": "round", "eyes": "glossy",
                               "accessory": "none"}
    assert ml.is_default({}) and ml.is_default(dict(ml.DEFAULT_LOOK))


def test_options_match_the_web_character():
    assert list(ml.FURS) == re.findall(r"^\s{4}(\w+):\s+\{ name:", WEB.read_text(), re.M)
    assert list(ml.FUR_LENGTHS) == js_list("FUR_LENGTHS")
    assert list(ml.EARS) == js_list("EARS")
    assert list(ml.EYES) == js_list("EYES")
    assert list(ml.ACCESSORIES) == js_list("ACCESSORIES")
    assert len(ml.FURS) >= 12 and {"sunset", "ocean", "cream", "charcoal"} <= set(ml.FURS)
    roots = dict(re.findall(r'^\s{4}(\w+):\s+\{ name: "[^"]+",\s+root: "(#[0-9a-f]+)"', WEB.read_text(), re.M))
    assert ml.SWATCHES == roots


def test_validate_fills_defaults_and_returns_a_new_dict():
    given = {"fur": "ocean"}
    out = ml.validate_look(given)
    assert out == {**ml.DEFAULT_LOOK, "fur": "ocean"} and given == {"fur": "ocean"}


@pytest.mark.parametrize("bad", [{"fur": "plaid"}, {"ears": "square"}, {"accessory": "crown"}, {"wings": "yes"},
                                 "mochi", {"eyes": 3}])
def test_validate_rejects_anything_else(bad):
    with pytest.raises(ValueError):
        ml.validate_look(bad)


def test_look_key_is_stable_and_order_free():
    a = ml.look_key({"fur": "mint", "ears": "pointy"})
    assert a == ml.look_key({"ears": "pointy", "fur": "mint"}) and re.fullmatch(r"[0-9a-f]{12}", a)
    assert a != ml.look_key({"fur": "mint"})


@pytest.mark.parametrize("key,value", [("character", "r2d2"), ("maxie_size", "huge"), ("personality", "grumpy"),
                                        ("chattiness", "silent"), ("voice_speed", 3.0), ("voice", "xx_nobody"),
                                        ("maxie_look", {"fur": "plaid"})])
def test_settings_refuse_unknown_maxie_values(key, value):
    with pytest.raises(ValueError):
        Settings().update({key: value})


def test_settings_store_a_clean_look():
    s = Settings().update({"maxie_look": {"fur": "cream", "accessory": "scarf"}, "voice_speed": 0.9,
                           "personality": "coach", "chattiness": "quiet", "voice": "bm_fable"})
    assert s.maxie_look == {**ml.DEFAULT_LOOK, "fur": "cream", "accessory": "scarf"}
    assert (s.voice_speed, s.personality, s.chattiness, s.voice) == (0.9, "coach", "quiet", "bm_fable")


def test_all_kokoro_english_voices_are_offered():
    ids = [v["id"] for v in ml.VOICES]
    assert len(ids) == 28 and "af_heart" in ids and "bm_fable" in ids and len(set(ids)) == 28


def test_default_style_adds_nothing_to_the_turn():
    assert ml.style_line("friend", "normal") == ""
    line = context_line(Settings(), clock=lambda: 1_760_000_000)
    assert "Style" not in line


@pytest.mark.parametrize("personality,word", [("coach", "coach"), ("butler", "butler"), ("playful", "playful")])
def test_personality_rides_in_the_turn(personality, word):
    s = Settings(personality=personality)
    assert word in context_line(s, clock=lambda: 1_760_000_000).lower()


def test_chattiness_shapes_reply_length():
    assert "one or two sentences" in ml.style_line("friend", "quiet")
    assert "a little more" in ml.style_line("friend", "chatty")


class Proc:
    def __init__(self, code=0):
        self.code = code

    def wait(self):
        return self.code

    def poll(self):
        return self.code


class Spawn:
    """Stands in for `taskpolicy -b render_mascot.py`: writes the idle meta the way the script would."""

    def __init__(self, code=0, write=True):
        self.calls, self.code, self.write = [], code, write

    def __call__(self, args, **kw):
        self.calls.append(args)
        out = Path(args[args.index("--out") + 1])
        if self.write:
            (out / "idle").mkdir(parents=True)
            (out / "idle" / "meta.json").write_text("{}")
        return Proc(self.code)


def test_the_default_look_uses_the_bundled_loops(tmp_path):
    r = ml.LoopRenderer(tmp_path, spawn=Spawn(), sync=True)
    assert r.render(ml.DEFAULT_LOOK) == "bundled" and r.loops_dir(ml.DEFAULT_LOOK) is None


def test_a_new_look_renders_at_low_priority_then_is_ready(tmp_path):
    spawn = Spawn()
    r = ml.LoopRenderer(tmp_path, spawn=spawn, sync=True)
    look = ml.validate_look({"fur": "ocean"})
    assert r.render(look) == "ready"
    args = spawn.calls[0]
    assert args[:2] == ["/usr/sbin/taskpolicy", "-b"] and "--lean" in args
    assert json.loads(args[args.index("--custom") + 1]) == look
    assert r.loops_dir(look) == tmp_path / ml.look_key(look)
    assert r.render(look) == "ready" and len(spawn.calls) == 1, "rendered once"


def test_a_failed_render_keeps_the_old_loops(tmp_path):
    r = ml.LoopRenderer(tmp_path, spawn=Spawn(code=1), sync=True)
    look = ml.validate_look({"fur": "mint"})
    assert r.render(look) == "failed" and r.loops_dir(look) is None
    assert not any(tmp_path.iterdir()), "no half-rendered folder left behind"


def test_old_looks_are_pruned(tmp_path):
    r = ml.LoopRenderer(tmp_path, spawn=Spawn(), sync=True, keep=2)
    looks = [ml.validate_look({"fur": f}) for f in ("mint", "rose", "sky")]
    for look in looks:
        r.render(look)
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(ml.look_key(x) for x in looks[1:])


def test_classic_never_renders(tmp_path):
    spawn = Spawn()
    r = ml.LoopRenderer(tmp_path, spawn=spawn, sync=True)
    assert r.render(ml.validate_look({"fur": "mint"}), character="classic") == "bundled" and not spawn.calls


def test_taskpolicy_is_called_by_its_full_path(tmp_path):
    """launchd's PATH has no /usr/sbin, so a bare `taskpolicy` is not found when the hub runs as a service."""
    spawn = Spawn()
    ml.LoopRenderer(tmp_path, spawn=spawn, sync=True).render(ml.validate_look({"fur": "ocean"}))
    assert spawn.calls[0][0] == "/usr/sbin/taskpolicy"


@pytest.mark.skipif(not Path("/usr/sbin/taskpolicy").exists(), reason="taskpolicy is macOS only")
def test_render_really_spawns_with_launchds_path(tmp_path, monkeypatch):
    """No fake spawn: the real Popen with the PATH the hub has under launchd (deploy/dev.maxie.hub.plist)."""
    script = tmp_path / "render.py"
    script.write_text("import pathlib, sys\nout = pathlib.Path(sys.argv[sys.argv.index('--out') + 1])\n"
                      "(out / 'idle').mkdir(parents=True)\n(out / 'idle' / 'meta.json').write_text('{}')\n")
    monkeypatch.setattr(ml, "RENDER", script)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    r = ml.LoopRenderer(tmp_path / "mascot", sync=True)
    look = ml.validate_look({"fur": "ocean"})
    assert r.render(look) == "ready" and r.loops_dir(look) is not None


class BrokenSpawn:
    def __call__(self, args, **kw):
        raise FileNotFoundError(2, "No such file or directory", args[0])


def test_a_render_that_cannot_start_is_failed_not_a_crash(tmp_path):
    r = ml.LoopRenderer(tmp_path, spawn=BrokenSpawn(), sync=True)
    look = ml.validate_look({"fur": "mint"})
    assert r.render(look) == "failed" and r.loops_dir(look) is None
    assert not any(tmp_path.iterdir()), "no empty .part folder left behind"


# ---- /api/maxie (Settings › Maxie on the Mac, the look picker on the phone) ----

def hub(make_services, tmp_path, spawn=None):
    from fastapi.testclient import TestClient

    from maxie.hub.api import create_app
    s = make_services()
    s.maxie_loops = ml.LoopRenderer(tmp_path / "mascot", spawn=spawn or Spawn(), sync=True)
    return TestClient(create_app(s, token="t", scheduler=False)), s


H = {"Authorization": "Bearer t"}


def test_get_describes_his_pick_and_every_option(make_services, tmp_path):
    c, _ = hub(make_services, tmp_path)
    r = c.get("/api/maxie", headers=H).json()
    assert r["character"] == "maxie" and r["look"] == ml.DEFAULT_LOOK and r["render"] == "bundled"
    assert r["loops_dir"] is None
    assert [f["id"] for f in r["options"]["fur"]][:1] == ["mochi"] and len(r["options"]["fur"]) == 14
    assert r["options"]["accessory"] == list(ml.ACCESSORIES) and len(r["options"]["voice"]) == 28
    assert r["options"]["personality"] == ["friend", "coach", "butler", "playful"]
    assert {"maxie_size", "personality", "chattiness", "voice", "voice_speed", "quiet_start", "quiet_end"} <= set(r)


def test_put_look_renders_new_loops_and_saves(make_services, tmp_path):
    spawn = Spawn()
    c, s = hub(make_services, tmp_path, spawn)
    r = c.put("/api/maxie", json={"look": {"fur": "ocean", "ears": "floppy"}}, headers=H)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["render"] == "ready" and body["loops_dir"].endswith(ml.look_key(s.settings.maxie_look))
    assert s.settings.maxie_look["fur"] == "ocean" and len(spawn.calls) == 1
    saved = json.loads((Path(tmp_path) / "settings.json").read_text())
    assert saved["maxie_look"]["ears"] == "floppy"


def test_put_other_settings_does_not_render(make_services, tmp_path):
    spawn = Spawn()
    c, s = hub(make_services, tmp_path, spawn)
    r = c.put("/api/maxie", json={"personality": "butler", "chattiness": "chatty", "voice": "am_onyx",
                                  "voice_speed": 1.2, "maxie_size": "large", "character": "classic"}, headers=H)
    assert r.status_code == 200 and r.json()["render"] == "bundled" and not spawn.calls
    assert (s.settings.personality, s.settings.voice, s.settings.character) == ("butler", "am_onyx", "classic")


def test_a_broken_render_never_fails_the_save(make_services, tmp_path):
    """The "click twice" bug: every PUT re-renders the current look, so a render that could not start turned
    personality, chattiness, sleep etc. into a 500 and the app snapped the control back."""
    c, s = hub(make_services, tmp_path, BrokenSpawn())
    r = c.put("/api/maxie", json={"look": {"fur": "ocean"}}, headers=H)
    assert r.status_code == 200, r.text
    assert r.json()["render"] == "failed" and s.settings.maxie_look["fur"] == "ocean"
    r = c.put("/api/maxie", json={"personality": "butler"}, headers=H)
    assert r.status_code == 200 and s.settings.personality == "butler"


def test_an_unexpected_render_error_still_saves(make_services, tmp_path):
    c, s = hub(make_services, tmp_path)

    def boom(*a, **kw):
        raise RuntimeError("disk full")
    s.maxie_loops.render = boom
    r = c.put("/api/maxie", json={"chattiness": "quiet"}, headers=H)
    assert r.status_code == 200 and r.json()["render"] == "failed" and s.settings.chattiness == "quiet"


@pytest.mark.parametrize("bad", [{"look": {"fur": "plaid"}}, {"personality": "grumpy"}, {"voice_speed": 9},
                                 {"monthly_budget_usd": 1}])
def test_put_rejects_bad_values_and_changes_nothing(make_services, tmp_path, bad):
    c, s = hub(make_services, tmp_path)
    before = (dict(s.settings.maxie_look), s.settings.personality, s.settings.monthly_budget_usd)
    assert c.put("/api/maxie", json={"personality": "coach", **bad}, headers=H).status_code == 400
    assert (dict(s.settings.maxie_look), s.settings.personality, s.settings.monthly_budget_usd) == before


def test_maxie_needs_the_token(make_services, tmp_path):
    c, _ = hub(make_services, tmp_path)
    assert c.get("/api/maxie").status_code == 401
