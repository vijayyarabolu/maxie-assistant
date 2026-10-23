import AVFoundation
import SwiftUI

struct VoiceSection: View {
    let hub: HubClient
    @State private var listening = true
    @State private var speak = true
    @State private var whisper = true
    @State private var mic = ""
    @State private var voice = "af_heart"
    @State private var speed = 1.05
    @State private var player: VoicePreviewPlayer
    @State private var voices: [Voice] = []
    @State private var mics: [String] = []
    @State private var note = ""
    @State private var testing = false
    @State private var wakeWords: Set<String> = Set(Self.forms.map(\.id))
    @State private var speaker: Speaker?
    private let tick = Timer.publish(every: 2, on: .main, in: .common).autoconnect()

    init(hub: HubClient) {
        self.hub = hub
        _player = State(initialValue: VoicePreviewPlayer(hub: hub))
    }

    struct Form: Identifiable { let id: String; let label: String; let note: String }
    static let forms = [
        Form(id: "hey max", label: "\"Hey Max\"", note: "When you start talking. Works for anyone."),
        Form(id: "hey maxie", label: "\"Hey Maxie\"", note: "Same, the long name."),
        Form(id: "max", label: "\"Max\"", note: "Only first, after a pause, and only your voice."),
        Form(id: "maxie", label: "\"Maxie\"", note: "Only first, after a pause, and only your voice."),
    ]
    struct Speaker: Decodable { let enrolled: Bool; let message: String; let enrolling: Bool }

    struct Voice: Decodable, Identifiable { let id: String; let label: String }
    struct Voices: Decodable { let current: String; let voices: [Voice] }
    struct Settings: Decodable {
        let voice_muted: Bool; let speak_replies: Bool; let voice_input_device: String; let voice: String
        let whisper_at_night: Bool?; let wake_words: [String]?; let voice_speed: Double?
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            SunsetCard {
                SettingRow(title: "Listen for \"Hey Max\"", subtitle: "The wake word runs on the Mac; nothing leaves it before you say it.") {
                    Toggle("", isOn: $listening.onSet { on in Task { await hub.putSettings(["voice_muted": !on]) } })
                        .labelsHidden()
                }
                Color.clear.frame(height: 2)
                SettingRow(title: "Speak replies out loud", subtitle: "Off: replies show in the panel only.") {
                    Toggle("", isOn: $speak.onSet { on in Task { await hub.putSettings(["speak_replies": on]) } })
                        .labelsHidden()
                }
                Color.clear.frame(height: 2)
                SettingRow(title: "Whisper at night", subtitle: "In quiet hours Maxie speaks softer and a little slower, and the chime is quieter.") {
                    Button("Hear it") {
                        player.play(voice, speed: speed * VoicePreviewPlayer.whisperSpeed, volume: VoicePreviewPlayer.whisperVolume)
                    }
                    .buttonStyle(.sunset).help("How Maxie sounds at night")
                    Toggle("", isOn: $whisper.onSet { on in Task { await hub.putSettings(["whisper_at_night": on]) } })
                        .labelsHidden()
                }
            }
            wakeCard
            SunsetCard {
                SettingRow(title: "Microphone") {
                    Picker("", selection: $mic.onSet { m in Task { await hub.putSettings(["voice_input_device": m]); restartVoice() } }) {
                        Text("System default").tag("")
                        ForEach(mics, id: \.self) { Text($0).tag($0) }
                    }
                    .labelsHidden().boxed().frame(maxWidth: 260)
                }
                Color.clear.frame(height: 2)
                SettingRow(title: "Voice", subtitle: "Kokoro, on this Mac.") {
                    HStack {
                        Picker("", selection: $voice.onSet { v in Task { await hub.putSettings(["voice": v]); restartVoice() } }) {
                            ForEach(voices) { Text($0.label).tag($0.id) }
                        }
                        .labelsHidden().boxed().frame(maxWidth: 180)
                        PlayButton(voice: voice, speed: speed, player: player)
                        Button { test() } label: {
                            Label(testing ? "Playing…" : "Test on speakers", systemImage: testing ? "speaker.wave.2.fill" : "play.fill")
                        }
                        .buttonStyle(.sunset)
                        .fixedSize()
                    }
                }
            }
            Text("For a long talk, say \"let's talk\". Maxie keeps listening between replies until you say \"that's all\" or stay quiet for 30 seconds.")
                .font(Light.small).foregroundStyle(Light.quiet)
            Label("Everything stays on the Mac: silero VAD, Whisper (MLX) and Kokoro run locally. Changing the mic or the voice restarts the voice helper.",
                  systemImage: "lock.fill").font(.caption).foregroundStyle(.secondary)
            NoteLine(text: note)
        }
        .task { await load() }
        .onReceive(tick) { _ in if speaker?.enrolling == true { Task { speaker = await hub.fetch("/api/voice/speaker") } } }
    }

    private var wakeCard: some View {
        SunsetCard {
            Label("Wake words", systemImage: "waveform").font(.headline)
            ForEach(Self.forms) { f in
                SettingRow(title: f.label, subtitle: f.note) {
                    Toggle("", isOn: Binding(get: { wakeWords.contains(f.id) }, set: { setWake(f.id, $0) }))
                        .labelsHidden()
                }
            }
            Color.clear.frame(height: 2)
            SettingRow(title: "Your voice", subtitle: speakerLine) {
                HStack(spacing: 8) {
                    if speaker?.enrolled == true {
                        Button("Forget my voice") { Task { speaker = await post("/api/voice/speaker/forget") } }.buttonStyle(.sunset)
                    }
                    Button(speaker?.enrolled == true ? "Re-train my voice" : "Teach Maxie your voice") {
                        Task { _ = await hub.postJSON("/api/voice/enroll", dict: [:]); speaker = await hub.fetch("/api/voice/speaker") }
                    }
                    .buttonStyle(.sunsetProminent).disabled(speaker?.enrolling == true)
                }
            }
            Color.clear.frame(height: 2)
            WakeTestCard(hub: hub)
            Text("Like the new Siri: say the name and your question in one breath (\"Max what's on my calendar\"), follow up for 8 s without it, talk over Maxie to interrupt, or type with \(HotKeys.optionSpace.label).")
                .font(Light.small).foregroundStyle(Light.quiet).fixedSize(horizontal: false, vertical: true)
        }
    }

    private var speakerLine: String {
        guard let speaker else { return "Five short phrases. Only voice prints are kept, on this Mac." }
        if speaker.enrolling { return "Listening: repeat each phrase after Maxie says it." }
        if speaker.enrolled { return "Maxie knows your voice: a bare \u{201C}Max\u{201D} from TV or other people can't wake it. Missed you? Re-train, or try the test below." }
        return speaker.message.isEmpty ? "Five short phrases. Until then only \"Hey Max\" and \"Hey Maxie\" work." : speaker.message
    }

    private func setWake(_ id: String, _ on: Bool) {
        if on { wakeWords.insert(id) } else { wakeWords.remove(id) }
        let ordered = Self.forms.map(\.id).filter(wakeWords.contains)
        Task { await hub.putSettings(["wake_words": ordered]) }
    }

    private func post(_ path: String) async -> Speaker? {
        _ = await hub.postJSON(path, dict: [:])
        return await hub.fetch("/api/voice/speaker")
    }

    private func load() async {
        if let s: Settings = await hub.fetch("/api/settings") {
            listening = !s.voice_muted; speak = s.speak_replies; mic = s.voice_input_device; voice = s.voice
            whisper = s.whisper_at_night ?? true
            speed = s.voice_speed ?? 1.05
            if let w = s.wake_words { wakeWords = Set(w) }
        }
        speaker = await hub.fetch("/api/voice/speaker")
        if let v: Voices = await hub.fetch("/api/voice/voices") { voices = v.voices }
        mics = AVCaptureDevice.DiscoverySession(deviceTypes: [.microphone, .external], mediaType: .audio, position: .unspecified)
            .devices.map(\.localizedName)
    }

    /// Shows "Playing…" at once (the voice helper picks the line up within half a second, a bit longer
    /// when it was idle and reloads the voice), so one click is clearly enough.
    private func test() {
        testing = true
        note = speak ? "Playing on this Mac's speakers…" : "Speak replies is off, so the test stays silent."
        Task {
            let (code, _) = await hub.postJSON("/api/voice/say", dict: ["text": "Hi there. This is how I sound."])
            if code != 200 { note = "The hub didn't take it (\(code)). Is it running?" }
            try? await Task.sleep(for: .seconds(3))
            testing = false
        }
    }

    private func restartVoice() {
        MacHelpers.kickstart(MacHelpers.voiceLabel)
        note = "Voice helper restarted."
    }
}
