import SwiftUI
import XCTest
@testable import ClaudeNotch

/// One press, the right target, everywhere. Real views in real (off-screen, invisible) windows,
/// clicked with synthetic mouse-down/up pairs at the centre of every control.
@MainActor
final class ClickTests: XCTestCase {
    private func hub() -> HubClient { HubClient(base: URL(string: "http://127.0.0.1:9")!, token: nil) }

    static let maxieJSON = """
    {"character":"maxie","look":{"fur":"mochi","furLength":"long","ears":"round","eyes":"glossy","accessory":"none"},
     "render":"bundled","loops_dir":null,"maxie_size":"medium","personality":"friend","chattiness":"normal",
     "voice":"af_heart","voice_speed":1.05,"quiet_start":"23:00","quiet_end":"07:00",
     "options":{"fur":[{"id":"mochi","name":"Mochi periwinkle","color":"#6a7bc0"},{"id":"sunset","name":"Sunset","color":"#e39a6c"},
       {"id":"ocean","name":"Ocean blue","color":"#3f6fb0"},{"id":"cream","name":"Cream","color":"#d9c3a0"},
       {"id":"charcoal","name":"Charcoal","color":"#34343f"},{"id":"mint","name":"Mint","color":"#4fa58e"}],
      "furLength":["short","medium","long","extra"],"ears":["round","floppy","pointy"],"eyes":["glossy","sparkle","sleepy","button"],
      "accessory":["none","headphones","cap"],"maxie_size":["small","medium","large"],
      "personality":["friend","coach","butler","playful"],"chattiness":["quiet","normal","chatty"],
      "voice":[{"id":"af_heart","label":"Heart (US, female)"}],"voice_speed":[0.7,1.4]}}
    """

    /// Settings in the window SettingsWindow really makes, not key, with another window key in front of it.
    private func settings(_ section: SettingsSection, hub: HubClient) -> (ClickHarness, NSWindow) {
        let other = KeyPanel(contentRect: NSRect(x: -30_000, y: -30_000, width: 200, height: 200), styleMask: [.borderless], backing: .buffered, defer: false)
        other.alphaValue = 0
        other.orderBack(nil)
        other.makeKey()
        let view = SettingsView(hub: hub, notch: NotchController(hub: hub), initial: section)
        let h = ClickHarness(view, size: SettingsWindow.size, window: { SettingsWindow.makeWindow(content: $0) })
        XCTAssertFalse(h.window.isKeyWindow)
        return (h, other)
    }

    /// It moves from its (transparent) title bar like any Mac window, never from a control.
    func testSettingsWindowOnlyMovesFromItsTitleBar() {
        let (h, other) = settings(.maxie, hub: hub())
        defer { h.close(); other.close() }
        XCTAssertFalse(h.window.isMovableByWindowBackground)
        XCTAssertTrue(h.window.styleMask.contains(.titled), "the title bar moves it")
        XCTAssertNil(ClickTargets.frames["settings.drag"], "no home-made drag strip any more")
        let ids = ClickTargets.frames.keys.filter { $0 != "settings.content" }.sorted()  // an area, not a control
        XCTAssertGreaterThan(ids.count, 10)
        for id in ids {
            guard let p = h.center(target: id) else { continue }
            XCTAssertEqual(h.window.contentView?.hitTest(p)?.mouseDownCanMoveWindow, false, "\(id) must never start a window drag")
        }
    }

    func testOneClickOpensEverySectionEvenInQuickSuccession() {
        let (h, other) = settings(.account, hub: hub())
        defer { h.close(); other.close() }
        for s in SettingsSection.allCases.shuffled() + [.general, .backups, .general] {
            h.click(target: "settings.row.\(s.rawValue)")
            h.settle(0.05)
            XCTAssertEqual(ClickTargets.values["settings.section"], s.rawValue, "one click on \(s.rawValue)")
        }
    }

    func testSettingsOpensOnTheLastSectionUsed() throws {
        let name = "maxie.tests.\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: name))
        defer { defaults.removePersistentDomain(forName: name) }
        XCTAssertEqual(SettingsModel(defaults: defaults).section, .maxie, "first time: Maxie")
        SettingsModel(defaults: defaults).section = .voice
        XCTAssertEqual(SettingsModel(defaults: defaults).section, .voice, "next time: where the user left off")
        XCTAssertEqual(SettingsModel(section: .account, defaults: defaults).section, .account, "an explicit section wins")
    }

    func testGeneralLinksToMaxieInsteadOfRepeatingHisHome() {
        let (h, other) = settings(.general, hub: hub())
        defer { h.close(); other.close() }
        XCTAssertNil(ClickTargets.frames["word.Notch"], "Home lives in Settings › Maxie only")
        h.click(target: "general.openMaxie")
        h.settle(0.05)
        XCTAssertEqual(ClickTargets.values["settings.section"], SettingsSection.maxie.rawValue)
    }

    func testOneClickOnEveryMaxieControl() throws {
        let hub = hub()
        hub.maxie = try JSONDecoder().decode(MaxieInfo.self, from: Data(Self.maxieJSON.utf8))
        var saves: [[String: Any]] = []
        hub.maxieSink = { saves.append($0) }
        // The whole section, unscrolled, so the lower cards are on screen too (Settings scrolls; that's AppKit's job).
        let h = ClickHarness(MaxieSection(hub: hub, notch: NotchController(hub: hub)).frame(width: 500).padding(20),
                             size: CGSize(width: 540, height: 1800))
        defer { h.close() }
        h.settle(0.5)
        let clicks: [(String, String, String)] = [  // target, saved key, saved value (look parts: inside "look")
            ("swatch.ocean", "fur", "ocean"), ("swatch.mint", "fur", "mint"), ("swatch.mochi", "fur", "mochi"),
            ("furLength.Short", "furLength", "short"), ("furLength.Extra", "furLength", "extra"),
            ("ears.Floppy", "ears", "floppy"), ("ears.Pointy", "ears", "pointy"), ("eyes.Sparkle", "eyes", "sparkle"),
            ("size.Large", "maxie_size", "large"), ("size.Small", "maxie_size", "small"),
            ("personality.Coach", "personality", "coach"), ("personality.Calm butler", "personality", "butler"),
            ("chattiness.Quiet", "chattiness", "quiet"), ("chattiness.Chatty", "chattiness", "chatty"),
            ("sleepMode.Fixed quiet hours", "sleep_mode", "fixed"), ("sleepMode.Sleeps when you sleep", "sleep_mode", "presence"),
        ]
        for (target, key, value) in clicks {
            let before = saves.count
            h.click(target: target)
            XCTAssertEqual(saves.count, before + 1, "one click on \(target) saves once")
            let saved = saves.last ?? [:]
            let got = (saved["look"] as? [String: String])?[key] ?? saved[key] as? String
            XCTAssertEqual(got, value, target)
        }
        h.click(target: "maxie.briefSleep")  // "Slept 2:05 to 9:40" in the brief, off until the user turns it on
        XCTAssertEqual(saves.last?["brief_sleep"] as? Bool, true)
    }

    func testThePanelsSoftEdgeLetsClicksThrough() {
        let v = PanelHostingView(rootView: Color.red.contentShape(Rectangle()).onTapGesture {}.frame(width: 300, height: 300))
        v.frame = NSRect(x: 0, y: 0, width: 300, height: 300)
        let container = NSView(frame: v.frame)
        container.addSubview(v)
        let w = KeyPanel(contentRect: NSRect(x: -40_000, y: -40_000, width: 300, height: 300), styleMask: [.borderless], backing: .buffered, defer: false)
        w.alphaValue = 0
        w.contentView = container
        w.orderBack(nil)
        defer { w.orderOut(nil) }
        RunLoop.main.run(until: Date().addingTimeInterval(0.3))
        XCTAssertNil(v.hitTest(NSPoint(x: 2, y: 150)), "the see-through edge")
        XCTAssertNil(v.hitTest(NSPoint(x: 150, y: 298)))
        XCTAssertNotNil(v.hitTest(NSPoint(x: 150, y: 150)), "the light itself")
    }

    func testBotWebViewNeverTakesTheMouse() {
        let web = BotWebView(frame: NSRect(x: 0, y: 0, width: 100, height: 100))
        XCTAssertNil(web.hitTest(NSPoint(x: 50, y: 50)))
        XCTAssertFalse(web.acceptsFirstResponder)
    }
}

/// Geometry: where Settings and the panel go, and when a click closes the panel.
final class PanelPlacementTests: XCTestCase {
    let screen = CGRect(x: 0, y: 0, width: 1512, height: 944)
    let size = CGSize(width: 720, height: 560)

    func testSettingsCentresWithoutAPanel() {
        let f = SettingsPlacement.frame(size: size, avoiding: nil, in: screen)!
        XCTAssertEqual(f.midX, screen.midX)
    }

    func testSettingsNeverOverlapsThePanel() {
        for panel in [CGRect(x: 850, y: 300, width: 420, height: 620), CGRect(x: 40, y: 300, width: 420, height: 620),
                      CGRect(x: 546, y: 600, width: 420, height: 340)] {
            let f = SettingsPlacement.frame(size: size, avoiding: panel, in: screen)
            XCTAssertNotNil(f, "\(panel)")
            if let f { XCTAssertFalse(f.intersects(panel)); XCTAssertTrue(screen.contains(f)) }
        }
    }

    func testSettingsSaysWhenItCannotAvoidThePanel() {
        XCTAssertNil(SettingsPlacement.frame(size: size, avoiding: CGRect(x: 546, y: 100, width: 420, height: 800), in: screen))
    }

    func testOneDismissRule() {
        let panel = CGRect(x: 100, y: 100, width: 400, height: 600), bot = CGRect(x: 280, y: 720, width: 54, height: 63)
        XCTAssertTrue(PanelRules.closes(clickAt: CGPoint(x: 900, y: 400), panel: panel, bot: bot, pinned: false), "in Settings or anywhere else")
        XCTAssertFalse(PanelRules.closes(clickAt: CGPoint(x: 900, y: 400), panel: panel, bot: bot, pinned: true), "pinned stays")
        XCTAssertFalse(PanelRules.closes(clickAt: CGPoint(x: 200, y: 300), panel: panel, bot: bot, pinned: false), "inside the panel")
        XCTAssertFalse(PanelRules.closes(clickAt: CGPoint(x: 300, y: 740), panel: panel, bot: bot, pinned: false), "on Maxie")
    }

    func testPanelRidesAlongUnlessAHoverOpenedIt() {
        XCTAssertTrue(PanelRules.follows(openedByHover: false, pinned: false))
        XCTAssertTrue(PanelRules.follows(openedByHover: true, pinned: true))
        XCTAssertFalse(PanelRules.follows(openedByHover: true, pinned: false))
    }

    func testPanelFlipsBesideHimWhenAboveAndBelowDontFit() {
        let short = CGRect(x: 0, y: 0, width: 1512, height: 700)
        let bot = CGRect(x: 300, y: 330, width: 54, height: 63)
        let o = BotGeometry.panelOrigin(bot: bot, panel: CGSize(width: 420, height: 620), screen: short, atNotch: false)
        let f = CGRect(origin: o, size: CGSize(width: 420, height: 620))
        XCTAssertTrue(short.contains(f))
        XCTAssertFalse(f.intersects(bot), "never covers him")
    }
}
