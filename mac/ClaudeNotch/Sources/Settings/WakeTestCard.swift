import SwiftUI

/// Settings › Voice › "Test wake word": he says a wake word once and sees what Maxie heard, his voice's
/// score against the bar, and why it would or wouldn't wake. Nothing runs and the panel stays shut.
struct WakeTestCard: View {
    let hub: HubClient
    @State private var phase: Phase
    @State private var result: Result?

    /// `sample`: off-screen renders show a finished test without a hub.
    init(hub: HubClient, sample: Result? = nil) {
        self.hub = hub
        _phase = State(initialValue: sample == nil ? .idle : .done)
        _result = State(initialValue: sample)
    }

    enum Phase { case idle, listening, done, failed }

    struct Result: Decodable, Equatable {
        let heard: String?
        let form: String?
        let woke: Bool?
        let score: Double?
        let threshold: Double?
        let why: String?
        let ms: Int?
    }
    struct Status: Decodable { let state: String; let result: Result? }

    static let sampleMiss = Result(heard: "Maxie.", form: "maxie", woke: false, score: 0.31, threshold: 0.38,
                                   why: "Your voice scored 0.31, under the bar of 0.38. Re-train your voice, or say \u{201C}Hey Maxie\u{201D}.", ms: 240)
    static let pollEvery: Double = 0.4
    static let giveUpAfter: Double = 12  // the helper listens 8 s, plus Whisper

    /// What the card shows under the button: heard, the score line (bare names) and why, in words.
    static func lines(_ r: Result) -> [String] {
        let heard = (r.heard ?? "").isEmpty ? "Heard nothing." : "Heard \u{201C}\(r.heard ?? "")\u{201D}."
        var out = [heard]
        if let score = r.score, let bar = r.threshold {
            out.append(String(format: "Your voice: %.2f, the bar: %.2f.", score, bar))
        }
        out.append(r.why ?? "")
        return out.filter { !$0.isEmpty }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            SettingRow(title: "Test wake word", subtitle: subtitle) {
                Button(phase == .listening ? "Listening…" : "Test wake word") { start() }
                    .buttonStyle(.sunset)
                    .disabled(phase == .listening)
            }
            if phase == .done, let result {
                VStack(alignment: .leading, spacing: 2) {
                    ForEach(Self.lines(result), id: \.self) { Text($0) }
                }
                .font(Light.small)
                .foregroundStyle(result.woke == true ? Light.ok : Light.notice)
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private var subtitle: String {
        switch phase {
        case .idle, .done: "Say \u{201C}Hey Maxie\u{201D} or \u{201C}Maxie\u{201D} once. Nothing runs."
        case .listening: "Say it now…"
        case .failed: "The voice helper didn't answer. Is it running? (Changing the mic above restarts it.)"
        }
    }

    private func start() {
        phase = .listening
        result = nil
        Task {
            _ = await hub.postJSON("/api/voice/test", dict: [:])
            let deadline = Date().addingTimeInterval(Self.giveUpAfter)
            while Date() < deadline {
                try? await Task.sleep(for: .seconds(Self.pollEvery))
                if let st: Status = await hub.fetch("/api/voice/test"), st.state == "done", let r = st.result {
                    result = r
                    phase = .done
                    return
                }
            }
            phase = .failed
        }
    }
}
