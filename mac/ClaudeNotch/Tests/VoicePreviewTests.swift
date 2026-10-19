import XCTest
@testable import ClaudeNotch

/// One sample at a time, ■ stops it, a slow answer for an old voice never plays over a new one.
/// The output is a silent fake: tests never make a sound.
@MainActor
final class VoicePreviewTests: XCTestCase {
    final class Silent: SampleOutput {
        var onFinish: (() -> Void)?
        var volume: Float = 1
        static var started: [Silent] = []
        var stopped = false
        func start() -> Bool { Silent.started.append(self); return true }
        func stop() { stopped = true }
    }

    private func player(delay: [String: Double] = [:], asked: @escaping (String, Double) -> Void = { _, _ in }) -> VoicePreviewPlayer {
        Silent.started = []
        return VoicePreviewPlayer(fetch: { voice, speed in
            asked(voice, speed)
            try? await Task.sleep(for: .milliseconds(Int((delay[voice] ?? 0.01) * 1000)))
            return Data("RIFF".utf8)
        }, output: { _ in Silent() })
    }

    private func wait(_ s: Double = 0.2) async { try? await Task.sleep(for: .milliseconds(Int(s * 1000))) }

    func testPlaysAndStops() async {
        var asked: [(String, Double)] = []
        let p = player { asked.append(($0, $1)) }
        p.play("bm_fable", speed: 1.2)
        XCTAssertEqual(p.loading, "bm_fable")
        await wait()
        XCTAssertEqual(p.playing, "bm_fable")
        XCTAssertEqual(asked.first?.0, "bm_fable")
        XCTAssertEqual(asked.first?.1, 1.2)
        p.toggle("bm_fable", speed: 1.2)
        XCTAssertNil(p.playing)
        XCTAssertTrue(Silent.started.first?.stopped ?? false)
    }

    func testOneSampleAtATime() async {
        let p = player()
        p.play("af_heart", speed: 1)
        await wait()
        p.play("am_onyx", speed: 1)
        await wait()
        XCTAssertEqual(p.playing, "am_onyx")
        XCTAssertEqual(Silent.started.count, 2)
        XCTAssertTrue(Silent.started[0].stopped)
    }

    func testASlowOldVoiceNeverPlaysOverTheNewOne() async {
        let p = player(delay: ["af_sky": 0.4])
        p.play("af_sky", speed: 1)
        p.play("bf_lily", speed: 1)
        await wait(0.6)
        XCTAssertEqual(p.playing, "bf_lily")
        XCTAssertEqual(Silent.started.count, 1)
    }

    func testFinishingClearsPlaying() async {
        let p = player()
        p.play("af_heart", speed: 1)
        await wait()
        Silent.started.last?.onFinish?()
        XCTAssertNil(p.playing)
    }

    func testWhisperModeIsSlowerAndSofter() async {
        var asked: [Double] = []
        let p = player { asked.append($1) }
        p.play("af_heart", speed: 1.05 * VoicePreviewPlayer.whisperSpeed, volume: VoicePreviewPlayer.whisperVolume)
        await wait()
        XCTAssertEqual(asked.first ?? 0, 0.95, accuracy: 0.001)
        XCTAssertEqual(Silent.started.first?.volume, 0.35)
    }
}
