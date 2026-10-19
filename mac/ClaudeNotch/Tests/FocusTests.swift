import AppKit
import XCTest
@testable import ClaudeNotch

/// When the chat panel folds away, the keyboard goes back to the app the user clicked. AppKit hands key
/// to the next window of the same app that can take it; Maxie's own window was a KeyPanel too, so the user's typing went to Maxie.
@MainActor
final class FocusTests: XCTestCase {
    private let offscreen = CGRect(x: -40_000, y: -40_000, width: 60, height: 40)

    func testTheBotNeverTakesTheKeyboard() {
        let bot = NotchController.makeBotWindow(offscreen)
        XCTAssertFalse(bot.canBecomeKey, "after the panel closes, nothing of Maxie's left on screen may take the user's typing")
        XCTAssertFalse(bot.canBecomeMain)
        XCTAssertTrue(bot.styleMask.contains(.nonactivatingPanel))
        bot.close()
    }

    /// macOS's own prompt (Allow / Don't Allow) takes the keyboard, and when it closes macOS hands it
    /// to the last regular app (Claude), never to Maxie (a menu-bar app). Maxie brings his Settings back himself:
    /// right after a prompt he asked for, or once a System Settings switch he sent the user to has changed.
    func testAPromptMaxieAskedForBringsHimBackRightAway() {
        for p in [Permission.microphone, .notifications, .calendars, .reminders, .contacts] {
            XCTAssertTrue(PermissionCenter.asksInPlace(p), "\(p): the answer is in when the request returns")
        }
        for p in [Permission.accessibility, .screen, .fullDisk, .location] {
            XCTAssertFalse(PermissionCenter.asksInPlace(p), "\(p): back once its state changes")
        }
    }

    func testBackFromSystemSettingsOnlyForWhatMaxieAskedFor() {
        let before: [Permission: String] = [.fullDisk: "notAsked", .screen: "turnOn", .contacts: "allowed"]
        XCTAssertEqual(PermissionCenter.answered(waiting: [.fullDisk], before: before, after: before), [], "nothing yet")
        var after = before
        after[.fullDisk] = "allowed"
        after[.contacts] = "denied"
        XCTAssertEqual(PermissionCenter.answered(waiting: [.fullDisk], before: before, after: after), [.fullDisk])
        XCTAssertEqual(PermissionCenter.answered(waiting: [], before: before, after: after), [], "the user changed it: no jump")
    }

    func testComingBackIsOneSignalSettingsListensFor() {
        let back = expectation(forNotification: .maxieComesBack, object: nil)
        PermissionCenter.comeBack()
        wait(for: [back], timeout: 1)
    }

    /// "Turn on" opened System Settings somewhere behind; since macOS 14 an app opened by another only
    /// comes to the front if the opener yields to it. One click: the exact pane, in front, the floating chat aside.
    func testSystemSettingsOpensInFrontOnTheExactPane() throws {
        var opened: (URL, NSWorkspace.OpenConfiguration)?
        let aside = expectation(forNotification: .maxieStepsAside, object: nil)
        MacHelpers.openSystemSettings(Permission.fullDisk.pane) { opened = ($0, $1) }
        wait(for: [aside], timeout: 1)
        let (url, config) = try XCTUnwrap(opened)
        XCTAssertEqual(url.absoluteString, "x-apple.systempreferences:com.apple.preference.security?Privacy_AllFiles")
        XCTAssertTrue(config.activates)
    }

    func testTheFloatingChatDropsBelowSystemSettingsAndFloatsAgainAfter() {
        XCTAssertEqual(PanelRules.level(steppedAside: true), .normal)
        XCTAssertEqual(PanelRules.level(steppedAside: false), .statusBar)
    }

    func testTheChatPanelStillTakesTypingAndEsc() {
        let panel = NotchController.makeChatPanel(offscreen)
        XCTAssertTrue(panel.canBecomeKey)
        XCTAssertTrue(panel.styleMask.contains(.nonactivatingPanel), "opening it never activates Maxie")
        panel.close()
    }
}
