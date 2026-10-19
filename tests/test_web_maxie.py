"""GOAL5 Phase 3 on the web: the panel's live bot (bot.html) and the phone (PWA) draw Maxie, his pick, by
default; the Classic robot only when chosen. Headless Chromium, off-screen (never a window on his screen)."""

import functools
import http.server
import json
import threading
from pathlib import Path

import pytest

sync_api = pytest.importorskip("playwright.sync_api")
WEB = Path(__file__).resolve().parents[1] / "web"
ARGS = ["--use-angle=metal", "--enable-gpu", "--ignore-gpu-blocklist"]  # headless, still off-screen


@pytest.fixture(scope="module")
def site():
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(WEB)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch(args=ARGS)
        except Exception as e:  # no Chromium installed for Playwright
            pytest.skip(f"headless Chromium unavailable: {e}")
        yield b
        b.close()


def open_page(browser, url, routes=None):
    page = browser.new_page(viewport={"width": 390, "height": 700})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    for pattern, handler in (routes or {}).items():
        page.route(pattern, handler)
    page.goto(url)
    page.wait_for_function("window.bot !== undefined && window.bot !== null")
    return page, errors


def test_bot_html_is_maxie_by_default(browser, site):
    page, errors = open_page(browser, f"{site}/bot.html?state=idle")
    custom = page.evaluate("bot.custom")
    assert custom == {"fur": "mochi", "furLength": "long", "ears": "round", "eyes": "glossy", "accessory": "none"}
    page.evaluate("bot.setState('wave')")
    assert page.evaluate("bot.state") == "wave" and errors == []
    page.close()


def test_bot_html_takes_a_custom_look(browser, site):
    look = json.dumps({"fur": "ocean", "ears": "floppy"})
    page, errors = open_page(browser, f"{site}/bot.html?custom={look}")
    assert page.evaluate("[bot.custom.fur, bot.custom.ears, bot.custom.accessory]") == ["ocean", "floppy", "none"]
    assert errors == []
    page.close()


def test_classic_robot_borrows_maxie_only_states(browser, site):
    page, errors = open_page(browser, f"{site}/bot.html?character=classic")
    assert page.evaluate("bot.custom") is None, "the Classic robot has no Maxie look"
    page.evaluate("bot.setState('closed')")
    assert page.evaluate("bot.state") == "sleep" and errors == []
    page.close()


def maxie_api(state):
    def handle(route):
        req = route.request
        if req.method == "PUT":
            state["puts"].append(json.loads(req.post_data))
            state["m"] = {**state["m"], **state["puts"][-1]}
        route.fulfill(content_type="application/json", body=json.dumps(state["m"]))
    return handle


def test_phone_shows_his_look_and_the_picker_syncs_with_the_mac(browser, site):
    state = {"m": {"character": "maxie", "look": {"fur": "mochi"}}, "puts": []}
    ok = lambda r: r.fulfill(content_type="application/json", body="{}")  # noqa: E731
    # Playwright tries the newest route first, so the specific one goes last.
    routes = {"**/api/**": ok, "**/health": ok, "**/sw.js": ok, "**/api/maxie": maxie_api(state)}
    page = browser.new_page(viewport={"width": 390, "height": 800})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.add_init_script("localStorage.setItem('hubToken', 't')")
    for pattern, handler in routes.items():
        page.route(pattern, handler)
    page.goto(f"{site}/index.html")
    page.wait_for_function("document.querySelectorAll('#look option').length > 10")
    names = page.evaluate("[...document.querySelectorAll('#look option')].map(o => o.value)")
    assert names[0] == "mochi" and names[-1] == "classic" and len(names) == 15
    assert page.evaluate("document.querySelector('#look').value") == "mochi"
    page.evaluate("document.querySelector('#look').closest('section').hidden = false")  # the Settings tab
    page.select_option("#look", "cream")
    page.wait_for_function("JSON.parse(localStorage.getItem('maxie') || '{}').look?.fur === 'cream'")
    assert state["puts"][-1] == {"character": "maxie", "look": {"fur": "cream"}}
    page.select_option("#look", "classic")
    page.wait_for_function("JSON.parse(localStorage.getItem('maxie') || '{}').character === 'classic'")
    assert errors == []
    page.close()
