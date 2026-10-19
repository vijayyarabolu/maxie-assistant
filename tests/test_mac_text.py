"""text he can read in Maxie he can select and copy. SwiftUI's textSelection can't be read back at
run time, so this checks the one place each surface turns it on for everything under it."""

import re
from pathlib import Path

SOURCES = Path(__file__).resolve().parents[1] / "mac" / "ClaudeNotch" / "Sources"


def body(swift: str, start: str, end: str) -> str:
    text = (SOURCES / swift).read_text()
    return text[text.index(start):text.index(end, text.index(start))]


def test_every_settings_section_is_selectable():
    content = body("Settings/SettingsView.swift", "ScrollView {", ".scrollIndicators")
    assert re.search(r"\.textSelection\(\.enabled\)", content), "Settings: the section header and content"


def test_every_panel_tab_is_selectable():
    tabs = body("Panel/PanelView.swift", "switch tab {", ".frame(maxWidth: .infinity, maxHeight: .infinity")
    assert ".textSelection(.enabled)" in tabs, "the panel: Chat, Activity and Memory (notes)"
