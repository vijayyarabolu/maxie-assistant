import AVFoundation
import Observation
import SwiftUI

/// Seven thin bars left of the input: flat and quiet when idle, alive in ember while listening, following
/// the mic level when the app may read it (otherwise a gentle stand-in motion).
struct MicWave: View {
    let active: Bool
    var level: Double = 0
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        TimelineView(.animation(minimumInterval: 1.0 / 60, paused: !active || reduceMotion)) { ctx in
            let t = ctx.date.timeIntervalSinceReferenceDate
            HStack(alignment: .center, spacing: 2) {
                ForEach(0..<7, id: \.self) { i in
                    Capsule()
                        .fill(active ? Light.thread : Light.quiet)
                        .frame(width: 2, height: height(i, t))
                }
            }
            .frame(maxHeight: .infinity)
        }
        .accessibilityHidden(true)
    }

    private static let shape: [Double] = [0.45, 0.7, 0.9, 1.0, 0.9, 0.7, 0.45]

    private func height(_ i: Int, _ t: Double) -> CGFloat {
        guard active else { return 2 }
        let shape: Double = Self.shape[i]
        let live: Double = level > 0.02 ? level : 0.35
        let phase: Double = t * 7 + Double(i) * 0.9
        let wobble: Double = reduceMotion ? 0.8 : abs(sin(phase))
        let amount: Double = shape * (0.3 + 0.7 * live) * (0.4 + 0.6 * wobble)
        return 3 + 15 * CGFloat(amount)
    }
}

/// The mic level (0...1) while Maxie listens. Only when macOS already allows the app the microphone;
/// it never asks. Nothing is recorded: the samples go to /dev/null, only the meter is read.
@MainActor @Observable
final class MicMeter {
    private(set) var level: Double = 0
    @ObservationIgnored private var recorder: AVAudioRecorder?
    @ObservationIgnored private var timer: Timer?

    func run(_ on: Bool) {
        on ? start() : stop()
    }

    private func start() {
        guard recorder == nil, AVCaptureDevice.authorizationStatus(for: .audio) == .authorized else { return }
        let settings: [String: Any] = [AVFormatIDKey: kAudioFormatAppleLossless, AVSampleRateKey: 16000, AVNumberOfChannelsKey: 1]
        guard let r = try? AVAudioRecorder(url: URL(fileURLWithPath: "/dev/null"), settings: settings) else { return }
        r.isMeteringEnabled = true
        guard r.record() else { return }
        recorder = r
        let t = Timer(timeInterval: 1.0 / 30, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.sample() }
        }
        RunLoop.main.add(t, forMode: .common)
        timer = t
    }

    private func sample() {
        guard let r = recorder else { return }
        r.updateMeters()
        let db = Double(r.averagePower(forChannel: 0))  // -160 (silence) ... 0
        level = max(0, min(1, (db + 50) / 50))
    }

    private func stop() {
        timer?.invalidate()
        timer = nil
        recorder?.stop()
        recorder = nil
        level = 0
    }
}
