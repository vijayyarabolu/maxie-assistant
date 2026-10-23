import XCTest
@testable import ClaudeNotch

/// "Hey Maxie" was ignored and nothing on screen said so. The notch now says why in words, the app restarts a
/// voice helper that stopped answering, and Settings › Voice explains a wake-word test.
@MainActor
final class VoiceNoticeTests: XCTestCase {
    private func hub() -> HubClient { HubClient(base: URL(string: "http://127.0.0.1:9")!, token: nil) }

    func testTheNoticeSitsUnderMaxieAndStaysOnTheScreen() {
        // Arrange: Maxie at the notch, and dragged to the left edge
        let screen = CGRect(x: 0, y: 0, width: 1512, height: 982)
        let size = CGSize(width: 220, height: 30)

        // Act
        let atNotch = NoticeLine.frame(size: size, under: CGRect(x: 800, y: 940, width: 36, height: 42), in: screen)
        let atEdge = NoticeLine.frame(size: size, under: CGRect(x: 2, y: 500, width: 36, height: 42), in: screen)
        let atBottom = NoticeLine.frame(size: size, under: CGRect(x: 700, y: 4, width: 36, height: 42), in: screen)

        // Assert
        XCTAssertEqual(atNotch.midX, 818, accuracy: 0.5, "centred under him")
        XCTAssertEqual(atNotch.maxY, 940 - NoticeLine.gap, accuracy: 0.5)
        XCTAssertGreaterThanOrEqual(atEdge.minX, screen.minX, "never off the left edge")
        XCTAssertGreaterThan(atBottom.minY, 4 + 42, "no room below: above him instead")
    }

    func testANewNoticeReplacesTheOldOne() {
        let h = hub()
        h.show(VoiceNotice(kind: "didnt_catch", text: "Didn't catch that."))
        h.show(VoiceNotice(kind: "not_your_voice", text: "That didn't sound like you."))
        XCTAssertEqual(h.notice?.kind, "not_your_voice")
    }

    func testEveryKindTheVoiceHelperSendsHasAnIcon() {
        for kind in ["not_your_voice", "not_enrolled", "didnt_catch", "mic_silent", "voice_down", "hub_down"] {
            XCTAssertNotNil(VoiceNotice.icons[kind], kind)
        }
    }

    func testAStoppedVoiceHelperIsSaidAndRestartedOncePerStop() {
        // Arrange
        let h = hub()
        var restarts = 0
        h.onVoiceDown = { restarts += 1 }

        // Act: stops (reported twice), comes back, stops again
        h.setVoiceAlive(false)
        h.setVoiceAlive(false)
        h.setVoiceAlive(true)
        h.setVoiceAlive(false)

        // Assert
        XCTAssertEqual(restarts, 2)
        XCTAssertEqual(h.notice?.kind, "voice_down")
    }

    func testTheSameDisplaysInAnyOrderAreNoReasonToRebuildMaxie() {
        let a = ScreenLayout([(1, CGRect(x: 0, y: 0, width: 1512, height: 982)), (2, CGRect(x: 1512, y: 0, width: 2560, height: 1440))])
        let b = ScreenLayout([(2, CGRect(x: 1512, y: 0, width: 2560, height: 1440)), (1, CGRect(x: 0, y: 0, width: 1512, height: 982))])
        let unplugged = ScreenLayout([(1, CGRect(x: 0, y: 0, width: 1512, height: 982))])
        let resized = ScreenLayout([(1, CGRect(x: 0, y: 0, width: 1800, height: 1169)), (2, CGRect(x: 1800, y: 0, width: 2560, height: 1440))])
        XCTAssertEqual(a, b)
        XCTAssertNotEqual(a, unplugged)
        XCTAssertNotEqual(a, resized)
    }

    func testTheWakeTestSaysWhatItHeardTheScoreAndWhy() {
        let lines = WakeTestCard.lines(WakeTestCard.sampleMiss)
        XCTAssertEqual(lines.first, "Heard \u{201C}Maxie.\u{201D}.")
        XCTAssertTrue(lines.contains("Your voice: 0.31, the bar: 0.38."))
        XCTAssertTrue(lines.last?.contains("Re-train") == true)
        let nothing = WakeTestCard.lines(.init(heard: "", form: nil, woke: false, score: nil, threshold: nil,
                                               why: "I heard nothing.", ms: nil))
        XCTAssertEqual(nothing, ["Heard nothing.", "I heard nothing."])
    }
}
