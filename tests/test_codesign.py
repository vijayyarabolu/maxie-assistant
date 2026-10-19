"""his Maxie.app is only ever replaced by a build that xcodebuild signed with the stable
identity and that passed verification. Ad-hoc builds get a new code identity every time and macOS drops his
grants (Calendars, Reminders, Full Disk Access...). Item 19: `make notch` and `make notch-test` built ad-hoc straight
into his build/…/Maxie.app (and the tests launched it) before re-signing it, so every build knocked his
permissions off again."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
LIB = (SCRIPTS / "notch_lib.zsh").read_text()
BUILD = (SCRIPTS / "notch_build.sh").read_text()
INSTALL = (SCRIPTS / "notch_install.sh").read_text()
PROJECT = (ROOT / "mac" / "ClaudeNotch" / "project.yml").read_text()
HIS_APP = "build/Build/Products/Debug/Maxie.app"


def make_targets() -> dict[str, str]:
    """target name -> its recipe (the tab-indented lines under it)."""
    targets, name = {}, None
    for line in (ROOT / "Makefile").read_text().splitlines():
        m = re.match(r"^([\w-]+):", line)
        if m:
            name = m.group(1)
            targets[name] = ""
        elif name and line.startswith("\t"):
            targets[name] += line + "\n"
    return targets


def test_the_app_build_signs_with_the_stable_identity_itself():
    notch = make_targets()["notch"]
    assert "notch_build.sh" in notch and 'CODE_SIGN_IDENTITY="-"' not in notch
    assert re.search(r"CODE_SIGN_STYLE=Manual", BUILD) and re.search(r'CODE_SIGN_IDENTITY="?\$', BUILD)
    assert "-derivedDataPath" in BUILD and HIS_APP not in BUILD, "it builds into a staging folder, never his app"


def test_a_build_is_verified_before_anything_uses_it():
    assert "verify_app" in BUILD and "verify_app" in INSTALL
    assert "codesign -v --strict" in LIB
    assert "cdhash" in LIB and "return 1" in LIB.split("cdhash")[-1], "an ad-hoc signature is refused"


def test_tests_get_their_own_copy_and_bundle_id():
    test = make_targets()["notch-test"]
    assert "-derivedDataPath build-tests" in test
    assert "MAXIE_BUNDLE_ID=dev.maxie.notch.tests" in test and "MAXIE_NAME=" in test
    assert "notch_install" not in test and "notch_sign" not in test, "a test run never touches his Maxie.app"
    assert "PRODUCT_BUNDLE_IDENTIFIER: $(MAXIE_BUNDLE_ID)" in PROJECT
    assert re.search(r"MAXIE_BUNDLE_ID: dev\.maxie\.notch\s", PROJECT), "the real app keeps its id by default"


def test_only_install_swaps_a_verified_build_in():
    targets = make_targets()
    assert "notch_install.sh" in targets["notch-install"]
    assert [n for n, r in targets.items() if "notch_install.sh" in r] == ["notch-install"]
    assert re.search(r"^notch-run:\s*notch-install\b", (ROOT / "Makefile").read_text(), re.M)
    swap = INSTALL.split("verify_app", 1)[1]
    assert "mv " in swap and "ditto" in INSTALL, "copied next to his app, verified, then renamed into place"


def test_no_build_writes_into_his_app():
    for name, recipe in make_targets().items():
        if "xcodebuild" in recipe:
            assert "-derivedDataPath build " not in recipe + " ", f"make {name} builds into his Maxie.app"


def test_signing_never_falls_back_to_ad_hoc():
    assert 'ID="-"' not in LIB + BUILD and "-s -" not in LIB + BUILD
    assert "make_signing_identity.sh" in LIB, "no certificate: make the local one instead of ad-hoc"


def test_only_his_app_can_be_opened_as_maxie():
    """At 8:47 PM he opened Maxie and macOS launched the staging copy (same bundle id, also in Spotlight), which the
    next `make notch` rebuilds under him. The staging folder is skipped by Spotlight and LaunchServices forgets it;
    the install points LaunchServices back at his app."""
    notch = make_targets()["notch"]
    assert re.search(r"notch_build\.sh \S+\.noindex\b", notch), "Spotlight skips a folder named *.noindex"
    assert re.search(r"lsregister\S* -u ", BUILD, re.I), "the staging copy is unregistered after it's built"
    assert re.search(r"lsregister\S* -f ", INSTALL, re.I), "his app is what LaunchServices opens"


def test_a_build_never_rewrites_a_running_copy():
    assert "pgrep" in BUILD and "exit 1" in BUILD.split("pgrep", 1)[1], "Maxie running from the staging folder: stop"
