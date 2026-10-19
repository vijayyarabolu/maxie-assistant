import SwiftUI

/// Settings › Maxie: how Maxie looks, where he lives, how he sounds and talks. The preview is
/// the live web character, so a change shows at once; the notch frames are re-drawn on this Mac in the
/// background (low priority) and swap in when they are complete.
struct MaxieSection: View {
    let hub: HubClient
    let notch: NotchController
    @State private var look: [String: String] = [:]
    @State private var character = "maxie"
    @State private var size = "medium"
    @State private var botMode = "notch"
    @State private var personality = "friend"
    @State private var chattiness = "normal"
    @State private var voice = "af_heart"
    @State private var speed = 1.05
    @State private var quietStart = "23:00"
    @State private var quietEnd = "07:00"
    @State private var sleepMode = "presence"
    @State private var briefSleep = false
    @State private var note = ""
    @State private var player: VoicePreviewPlayer
    @State private var shown: BotState = .wave  // what the live preview is doing (the action gallery picks it)
    @State private var autoPlay = false
    @Environment(\.settingsScrolled) private var scrolled
    @Environment(\.colorScheme) private var scheme
    private static let renderPoll: Double = 3

    init(hub: HubClient, notch: NotchController) {
        self.hub = hub
        self.notch = notch
        _player = State(initialValue: VoicePreviewPlayer(hub: hub))
    }

    static let personalities = [("Chill friend", "friend"), ("Coach", "coach"), ("Calm butler", "butler"), ("Playful", "playful")]
    static let chattinessWords = [("Quiet", "quiet"), ("Normal", "normal"), ("Chatty", "chatty")]
    static let sleepWords = [("Sleeps when you sleep", "presence"), ("Fixed quiet hours", "fixed")]

    static func sleepLine(_ mode: String) -> String {
        mode == "fixed" ? "He sleeps, whispers and holds alerts between these times."
            : "At night, once your screen locks or you stop typing for 30 minutes and nothing is playing. \"Goodnight Max\" works too; any key wakes him."
    }

    /// Like a game's character creator: the live preview is pinned at the top while the options
    /// scroll under it, shrinking from big to compact; every change shows on him at once.
    var body: some View {
        LazyVStack(alignment: .leading, spacing: 18, pinnedViews: [.sectionHeaders]) {
            if let m = hub.maxie {
                Section {
                    ActionGallery(classic: m.isClassic, shown: $shown, autoPlay: $autoPlay)
                    if !m.isClassic { lookCard(m.options) }
                    placeCard
                    mannerCard(m.options)
                } header: {
                    pinned(m)
                }
            } else {
                Text("Waiting for the hub…").font(Light.small).foregroundStyle(Light.quiet)
            }
            NoteLine(text: note)
        }
        .task { await hub.syncMaxie(); load(); player.warm(voice, speed: speed) }
        .onDisappear { player.stop() }
        .task(id: hub.maxie?.render) { await pollWhileRendering() }  // no timer at all unless the notch is being re-drawn
        .onChange(of: hub.maxie?.isClassic) { _, classic in
            if !ActionGallery.states(classic: classic ?? false).contains(shown) { shown = .idle }
        }
    }

    // MARK: - Preview and character

    /// The preview on a backing of the field's own light, so the options scroll under it, not through it.
    private func pinned(_ m: MaxieInfo) -> some View {
        preview(m)
            .padding(.vertical, 6)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background {
                let base = (scheme == .dark ? Light.night : Light.mist).opacity(MaxiePreview.backdrop(scrolled: scrolled))
                LinearGradient(stops: [.init(color: base, location: 0.85), .init(color: base.opacity(0), location: 1)],
                               startPoint: .top, endPoint: .bottom)
                    .padding(.horizontal, -8).padding(.bottom, -10)
            }
            .clickTarget("maxie.preview.pinned")
    }

    private func preview(_ m: MaxieInfo) -> some View {
        let side = MaxiePreview.side(scrolled: scrolled)
        return HStack(alignment: .center, spacing: 20) {
            LiveBotView(base: hub.base, state: shown, character: character, look: look, framing: "portrait", fps: 24)
                .frame(width: MaxiePreview.big, height: MaxiePreview.big)  // the web view keeps its size, scaled
                .scaleEffect(side / MaxiePreview.big, anchor: .topLeading)
                .frame(width: side, height: side, alignment: .topLeading)
                .accessibilityLabel(m.isClassic ? "Preview: the Classic robot" : "Preview: Maxie with your look")
                .clickValue("maxie.preview", shown.rawValue)
            VStack(alignment: .leading, spacing: 8) {
                WordChoice(options: [("Maxie", "maxie"), ("Classic robot", "classic")], selection: $character.onSet { c in
                    save(["character": c])
                }, id: "character")
                Text(m.renderLine).font(Light.small).foregroundStyle(Light.quiet).fixedSize(horizontal: false, vertical: true)
                if !m.isClassic && !isDefaultLook {
                    Button("Back to the default look") { look = [:]; save(["look": [String: String]()]) }.buttonStyle(.sunset)
                }
            }
        }
    }

    private var isDefaultLook: Bool { look.isEmpty || look == Self.defaultLook }
    static let defaultLook = ["fur": "mochi", "furLength": "long", "ears": "round", "eyes": "glossy", "accessory": "none"]

    // MARK: - Look

    private func lookCard(_ o: MaxieInfo.Options) -> some View {
        SunsetCard {
            Text("Look").font(.headline)
            FurSwatches(furs: o.fur, selection: part("fur"))
            SettingRow(title: "Fur") { WordChoice(options: o.furLength.map { ($0.capitalized, $0) }, selection: part("furLength"), id: "furLength") }
            SettingRow(title: "Ears") { WordChoice(options: o.ears.map { ($0.capitalized, $0) }, selection: part("ears"), id: "ears") }
            SettingRow(title: "Eyes") { WordChoice(options: o.eyes.map { ($0.capitalized, $0) }, selection: part("eyes"), id: "eyes") }
            SettingRow(title: "Wearing") {
                Picker("", selection: part("accessory")) {
                    ForEach(o.accessory, id: \.self) { Text($0 == "none" ? "Nothing" : $0.capitalized).tag($0) }
                }
                .labelsHidden().boxed().frame(maxWidth: 160)
            }
        }
    }

    /// One part of the look: the preview changes at once, the hub saves it and re-draws the notch frames.
    private func part(_ key: String) -> Binding<String> {
        Binding(get: { look[key] ?? Self.defaultLook[key] ?? "" }, set: { value in
            let next = Self.defaultLook.merging(look) { $1 }.merging([key: value]) { $1 }
            look = next
            save(["look": next])
        })
    }

    // MARK: - Where and how big

    private var placeCard: some View {
        SunsetCard {
            Text("Where he lives").font(.headline)
            SettingRow(title: "Home", subtitle: botMode == "free" ? "Drag him anywhere; edges tuck him away, double-click sends him home." : "Next to the notch.") {
                WordChoice(options: [("Notch", "notch"), ("Free", "free")], selection: $botMode.onSet { m in notch.mode = m }, id: "home")
            }
            SettingRow(title: "Size", subtitle: "Away from the notch. At the notch he always fits the menu bar.") {
                WordChoice(options: [("Small", "small"), ("Medium", "medium"), ("Large", "large")], selection: $size.onSet { s in save(["maxie_size": s]) }, id: "size")
            }
        }
    }

    // MARK: - Voice and manner

    private func mannerCard(_ o: MaxieInfo.Options) -> some View {
        SunsetCard {
            Text("Voice and manner").font(.headline)
            SettingRow(title: "Voice", subtitle: "Kokoro, on this Mac. Every English voice.") {
                VoiceChooser(voices: o.voice, voice: $voice.onSet { v in save(["voice": v]) }, speed: speed, player: player)
            }
            SettingRow(title: "Speed", subtitle: String(format: "%.2f×. At night he slows down a little more.", speed)) {
                Slider(value: $speed, in: (o.voice_speed.first ?? 0.7)...(o.voice_speed.last ?? 1.4), step: 0.05) { editing in
                    if !editing { save(["voice_speed": speed]); player.play(voice, speed: speed) }  // hear the new speed
                }
                .frame(maxWidth: 180)
            }
            SettingRow(title: "Personality") {
                WordChoice(options: Self.personalities, selection: $personality.onSet { p in save(["personality": p]) }, id: "personality")
            }
            SettingRow(title: "How chatty", subtitle: "Quiet: short replies and no idle moments.") {
                WordChoice(options: Self.chattinessWords, selection: $chattiness.onSet { c in save(["chattiness": c]) }, id: "chattiness")
            }
            SettingRow(title: "When he sleeps", subtitle: Self.sleepLine(sleepMode)) {
                WordChoice(options: Self.sleepWords, selection: $sleepMode.onSet { m in save(["sleep_mode": m]) }, id: "sleepMode")
            }
            SettingRow(title: "Quiet hours", subtitle: sleepMode == "fixed" ? "From, to." : "He whispers then. Also used when this Mac can't tell.") {
                HStack(spacing: 6) {
                    ClockField(text: $quietStart.onSet { t in save(["quiet_start": t]) })
                    Text("to").font(Light.small).foregroundStyle(Light.quiet)
                    ClockField(text: $quietEnd.onSet { t in save(["quiet_end": t]) })
                }
            }
            SettingRow(title: "Sleep in the morning brief", subtitle: "\"Slept 2:05 to 9:40\". Kept on this Mac only.") {
                Toggle("", isOn: $briefSleep.onSet { on in save(["brief_sleep": on]) }).labelsHidden().clickTarget("maxie.briefSleep")
            }
        }
    }

    // MARK: - Load and save

    private func pollWhileRendering() async {
        while hub.maxie?.render == "rendering", !Task.isCancelled {
            try? await Task.sleep(for: .seconds(Self.renderPoll))
            guard !Task.isCancelled else { return }
            await hub.syncMaxie()
        }
    }

    private func load() {
        guard let m = hub.maxie else { return }
        look = m.look == Self.defaultLook ? [:] : m.look
        character = m.character
        size = m.maxie_size
        personality = m.personality
        chattiness = m.chattiness
        voice = m.voice
        speed = m.voice_speed
        quietStart = m.quiet_start
        quietEnd = m.quiet_end
        sleepMode = m.sleep_mode ?? "presence"
        briefSleep = m.brief_sleep ?? false
        botMode = notch.mode
    }

    private func save(_ changes: [String: Any]) {
        Task {
            let error = await hub.putMaxie(changes)
            note = error ?? "Saved."
            if error != nil { load() }  // show what is really stored
        }
    }
}

/// The fur colours as swatches; the chosen one ringed and named.
struct FurSwatches: View {
    let furs: [MaxieInfo.Fur]
    @Binding var selection: String

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            LazyVGrid(columns: Array(repeating: GridItem(.fixed(26), spacing: 8), count: 7), alignment: .leading, spacing: 8) {
                ForEach(furs) { fur in
                    Button { selection = fur.id } label: {
                        Circle().fill(Color(hex: fur.color)).frame(width: 22, height: 22)
                            .overlay(Circle().strokeBorder(Light.edge, lineWidth: 1))  // every swatch has an edge, even Midnight
                            .overlay {  // the chosen one: a 2 pt ring and a check, not colour alone
                                if fur.id == selection {
                                    Circle().strokeBorder(Light.thread, lineWidth: 2).padding(-4)
                                    Image(systemName: "checkmark").font(.system(size: 10, weight: .heavy))
                                        .foregroundStyle(Color(hex: fur.color).isLight ? Light.onThread : .white)
                                }
                            }
                            .probe("control")
                    }
                    .buttonStyle(.plain)
                    .help(fur.name)
                    .accessibilityLabel(fur.name)
                    .accessibilityAddTraits(fur.id == selection ? .isSelected : [])
                    .clickTarget("swatch.\(fur.id)")
                }
            }
            Text(furs.first { $0.id == selection }?.name ?? "").font(Light.small).foregroundStyle(Light.quiet)
        }
    }
}

/// "23:00" as a small field; only a valid HH:MM is passed on.
struct ClockField: View {
    @Binding var text: String
    @State private var draft = ""

    var body: some View {
        TextField("HH:MM", text: $draft)
            .textFieldStyle(.roundedBorder).frame(width: 64).multilineTextAlignment(.center)
            .onAppear { draft = text }
            .onChange(of: text) { _, t in draft = t }
            .onSubmit { if Self.valid(draft) { text = draft } else { draft = text } }
    }

    static func valid(_ s: String) -> Bool {
        s.range(of: #"^(?:[01]\d|2[0-3]):[0-5]\d$"#, options: .regularExpression) != nil
    }
}

/// The Settings › Maxie preview's size: big at the top, compact once the user has scrolled into the options.
enum MaxiePreview {
    static let big: CGFloat = 132
    static let compact: CGFloat = 72
    /// Points of scrolling over which he shrinks from big to compact.
    static let shrinkOver: CGFloat = 120

    static func side(scrolled: CGFloat) -> CGFloat {
        let t = min(max(scrolled / shrinkOver, 0), 1)
        return big - (big - compact) * t
    }

    /// The pinned preview's backdrop: the page's own see-through one at rest; solid once cards scroll under it.
    static func backdrop(scrolled: CGFloat) -> Double {
        scrolled > 0 ? 1 : LightField.baseOpacity
    }
}
