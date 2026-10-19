import SwiftUI
import XCTest
@testable import ClaudeNotch

/// What may leave the app when Maxie watches, and the eye toggles (panel, Settings).
@MainActor
final class WatchTests: XCTestCase {
    private func hub() -> HubClient { HubClient(base: URL(string: "http://127.0.0.1:9")!, token: nil) }

    private func items(_ bundle: String, name: String = "App", category: String? = nil, title: String? = nil,
                       secure: Bool = false, switched: Bool = true, excluded: [String] = []) -> [[String: Any]] {
        WatchRules.items(bundle: bundle, name: name, category: category, title: title, secure: secure,
                         switched: switched, excluded: excluded)
    }

    func testAnAppSwitchAndASafeTitleLeave() {
        let out = items("com.microsoft.VSCode", name: "Code", title: "watch.py - maxie")
        XCTAssertEqual(out.map { $0["kind"] as? String }, ["app", "title"])
        XCTAssertEqual(out.last?["detail"] as? String, "watch.py - maxie")
    }

    func testBankingHealthAndPasswordAppsLeaveNothingAtAll() {
        XCTAssertTrue(items("com.chase.sig.Chase", name: "Chase").isEmpty)
        XCTAssertTrue(items("com.1password.1password", name: "1Password").isEmpty)
        XCTAssertTrue(items("com.apple.Passwords", name: "Passwords").isEmpty)
        XCTAssertTrue(items("com.example.money", category: "public.app-category.finance").isEmpty)
        XCTAssertTrue(items("com.example.clinic", category: "public.app-category.healthcare-fitness").isEmpty)
    }

    func testNoBrowserPageTitleEverLeaves() {
        for b in ["com.apple.Safari", "com.google.Chrome", "org.mozilla.firefox", "company.thebrowser.Browser"] {
            let out = items(b, name: "Browser", title: "flights to austin - Google Search")
            XCTAssertEqual(out.map { $0["kind"] as? String }, ["app"], b)
        }
        let webApp = items("com.google.Chrome.app.jobhunt", name: "jobhunt", title: "Applications")
        XCTAssertEqual(webApp.count, 2, "a Chrome web app is its own app")
    }

    func testPasswordFieldsAndSensitiveTitlesLeaveNoTitle() {
        XCTAssertEqual(items("com.apple.mail", title: "Inbox", secure: true).count, 1)
        for t in ["Your verification code is 482913", "Sign in", "Private Browsing", "MyChart"] {
            XCTAssertEqual(items("com.apple.mail", title: t).count, 1, t)
        }
    }

    func testMessagesSaysWhoNotWhat() {
        let out = items(WatchRules.messages, name: "Messages", title: "Mom", switched: false)
        XCTAssertEqual(out.first?["kind"] as? String, "message")
        XCTAssertEqual(out.first?["detail"] as? String, "Mom")
    }

    func testAppsHeLeavesOutLeaveNothing() {
        XCTAssertTrue(items("com.tinyspeck.slackmacgap", name: "Slack", excluded: ["com.tinyspeck.slackmacgap"]).isEmpty)
        XCTAssertTrue(items("com.tinyspeck.slackmacgap", name: "Slack", excluded: ["slack"]).isEmpty)
    }

    func testTheHubsWatchEventsDecode() throws {
        let sug = try JSONDecoder().decode(HubEvent.self, from: Data(#"{"type":"suggestion","sid":3,"text":"want me to do it?"}"#.utf8))
        XCTAssertEqual(sug.sid, 3)
        let open = try JSONDecoder().decode(HubEvent.self, from: Data(#"{"type":"open_apps","apps":["com.example.a"]}"#.utf8))
        XCTAssertEqual(open.apps, ["com.example.a"])
    }

    func testTheBaseStateSaysWhetherHisEyesAreOpen() {
        let h = hub()
        h.noteEyes(base: "closed")
        XCTAssertFalse(h.eyesOpen)
        h.noteEyes(base: "sleep")
        XCTAssertFalse(h.eyesOpen, "asleep says nothing about watching")
        h.noteEyes(base: "idle")
        XCTAssertTrue(h.eyesOpen)
    }

    func testThePanelEyeClosesAndOpensWithOneClick() {
        let h = hub()
        let harness = ClickHarness(EyeToggle(hub: h).padding(20), size: CGSize(width: 220, height: 80))
        defer { harness.close() }
        harness.settle(0.2)
        harness.click(target: "panel.eyes")
        XCTAssertFalse(h.eyesOpen)
        harness.settle(0.1)
        harness.click(target: "panel.eyes")
        XCTAssertTrue(h.eyesOpen)
    }

    func testSettingsWatchingToggleAndSuggestion() {
        let h = hub()
        h.suggestion = WatchSuggestion(id: 1, text: "You open jobhunt and Gmail every weekday morning at 9: want me to do it?")
        let harness = ClickHarness(WatchingSection(hub: h).frame(width: 520).padding(20), size: CGSize(width: 560, height: 900))
        defer { harness.close() }
        harness.settle(0.3)
        harness.click(target: "watch.eyes")
        XCTAssertFalse(h.eyesOpen, "one click closes his eyes")
        harness.click(target: "suggestion.yes")
        XCTAssertNil(h.suggestion, "answered")
    }
}
