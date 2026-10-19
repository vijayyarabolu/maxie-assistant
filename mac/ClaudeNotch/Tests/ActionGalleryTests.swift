import SwiftUI
import XCTest
@testable import ClaudeNotch

/// Every state he can show, by its real name, under the live preview in Settings › Maxie.
@MainActor
final class ActionGalleryTests: XCTestCase {
    func testMaxieHasEveryStateInTheOrderHeAskedFor() {
        XCTAssertEqual(ActionGallery.states(classic: false),
                       [.idle, .listen, .think, .talk, .happy, .proud, .sleep, .wave, .closed, .stretch, .gentle])
        XCTAssertEqual(Set(ActionGallery.states(classic: false)), Set(BotState.allCases), "no state left out")
    }

    func testClassicOnlyOffersWhatTheClassicRobotCanDo() {
        XCTAssertEqual(ActionGallery.states(classic: true), [.idle, .listen, .think, .talk, .happy, .proud, .sleep, .gentle])
    }

    func testAutoPlayWalksThroughAllAndWrapsAround() {
        let list = ActionGallery.states(classic: false)
        XCTAssertEqual(ActionGallery.next(after: .idle, in: list), .listen)
        XCTAssertEqual(ActionGallery.next(after: .gentle, in: list), .idle)
        XCTAssertEqual(ActionGallery.next(after: .wave, in: ActionGallery.states(classic: true)), .idle, "a state Classic lacks starts over")
    }

    func testEveryChipHasItsRealLabel() {
        for s in BotState.allCases { XCTAssertFalse(ActionGallery.chip(s).isEmpty) }
        XCTAssertEqual(ActionGallery.chip(.proud), "Proud of you")
        XCTAssertEqual(ActionGallery.chip(.closed), "Eyes closed")
    }

    private func maxieSection(character: String = "maxie") throws -> ClickHarness {
        let hub = HubClient(base: URL(string: "http://127.0.0.1:9")!, token: nil)
        let json = ClickTests.maxieJSON.replacingOccurrences(of: "\"character\":\"maxie\"", with: "\"character\":\"\(character)\"")
        hub.maxie = try JSONDecoder().decode(MaxieInfo.self, from: Data(json.utf8))
        hub.maxieSink = { _ in }
        let h = ClickHarness(MaxieSection(hub: hub, notch: NotchController(hub: hub)).frame(width: 500).padding(20),
                             size: CGSize(width: 540, height: 1700))
        h.settle(0.5)
        return h
    }

    func testOneTapPlaysThatStateInThePreview() throws {
        let h = try maxieSection()
        defer { h.close() }
        for s in [BotState.happy, .sleep, .stretch, .closed, .idle] {
            h.click(target: "action.\(s.rawValue)")
            h.settle(0.05)
            XCTAssertEqual(ClickTargets.values["maxie.preview"], s.rawValue, "one tap on \(s.rawValue)")
            XCTAssertEqual(ClickTargets.values["maxie.notchPreview"], s.rawValue, "the notch-size preview follows")
        }
    }

    func testAutoPlayCyclesAndStops() throws {
        ActionGallery.interval = 0.2
        defer { ActionGallery.interval = ActionGallery.defaultInterval }
        let h = try maxieSection()
        defer { h.close() }
        h.click(target: "action.idle")
        h.click(target: "action.autoplay")
        h.settle(0.5)
        XCTAssertNotEqual(ClickTargets.values["maxie.preview"], "idle", "auto play moved on")
        h.click(target: "action.autoplay")
        let held = ClickTargets.values["maxie.preview"]
        h.settle(0.5)
        XCTAssertEqual(ClickTargets.values["maxie.preview"], held, "stopped")
    }

    func testClassicGetsItsOwnChips() throws {
        let h = try maxieSection(character: "classic")
        defer { h.close() }
        XCTAssertNotNil(ClickTargets.frames["action.happy"])
        XCTAssertNil(ClickTargets.frames["action.wave"], "the Classic robot can't wave")
    }
}
