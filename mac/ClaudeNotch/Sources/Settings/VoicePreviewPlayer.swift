import AVFoundation
import Observation

/// Something that plays one sample (AVAudioPlayer in the app; a silent fake in tests, which never make a sound).
@MainActor
protocol SampleOutput: AnyObject {
    var onFinish: (() -> Void)? { get set }
    var volume: Float { get set }
    func start() -> Bool
    func stop()
}

/// Plays "Hi there, I'm Maxie." in a voice before the user picks it. One sample at a time: a new one stops
/// the last; ■ stops it. The hub makes and caches the WAV (POST /api/voice/preview); this only plays it.
@MainActor
@Observable
final class VoicePreviewPlayer {
    private(set) var playing: String?   // the voice id being played
    private(set) var loading: String?
    private(set) var error = ""
    @ObservationIgnored private var output: SampleOutput?
    @ObservationIgnored private var request = 0
    @ObservationIgnored private let fetch: (String, Double) async -> Data?
    @ObservationIgnored private let makeOutput: @MainActor (Data) -> SampleOutput?

    init(fetch: @escaping (String, Double) async -> Data?, output: @escaping @MainActor (Data) -> SampleOutput? = { AudioSample($0) }) {
        self.fetch = fetch
        self.makeOutput = output
    }

    convenience init(hub: HubClient) {
        self.init(fetch: { voice, speed in
            let (code, data) = await hub.postData("/api/voice/preview", dict: ["voice": voice, "speed": speed])
            return code == 200 ? data : nil
        })
    }

    func toggle(_ voice: String, speed: Double) {
        if playing == voice || loading == voice { stop() } else { play(voice, speed: speed) }
    }

    /// Whisper mode at night: a little slower and this much softer (maxie.voice.loop).
    static let whisperSpeed = 0.95 / 1.05, whisperVolume: Float = 0.35

    func play(_ voice: String, speed: Double, volume: Float = 1) {
        stop()
        request += 1
        let mine = request
        loading = voice
        Task {
            let data = await fetch(voice, speed)
            guard mine == request else { return }  // the user moved on to another voice meanwhile
            loading = nil
            guard let data, let out = makeOutput(data) else {
                error = "Couldn't play that voice. Is the hub running?"
                return
            }
            out.volume = volume
            guard out.start() else {
                error = "Couldn't play that voice. Is the hub running?"
                return
            }
            error = ""
            out.onFinish = { [weak self, weak out] in
                guard let self, let out, out === self.output else { return }
                self.output = nil
                self.playing = nil
            }
            output = out
            playing = voice
        }
    }

    /// Makes the sample without playing it, so the first ▶︎ is instant (the first one loads Kokoro).
    func warm(_ voice: String, speed: Double) {
        Task { _ = await fetch(voice, speed) }
    }

    func stop() {
        request += 1
        output?.stop()
        output = nil
        playing = nil
        loading = nil
    }
}

/// The app's output: AVAudioPlayer on a WAV.
@MainActor
final class AudioSample: NSObject, SampleOutput, AVAudioPlayerDelegate {
    var onFinish: (() -> Void)?
    var volume: Float {
        get { player.volume }
        set { player.volume = newValue }
    }
    private let player: AVAudioPlayer

    init?(_ data: Data) {
        guard let p = try? AVAudioPlayer(data: data) else { return nil }
        player = p
        super.init()
        p.delegate = self
    }

    func start() -> Bool { player.play() }
    func stop() { player.stop() }

    nonisolated func audioPlayerDidFinishPlaying(_ p: AVAudioPlayer, successfully flag: Bool) {
        MainActor.assumeIsolated { onFinish?() }
    }
}
