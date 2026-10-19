import SwiftUI

enum SettingsSection: String, CaseIterable, Identifiable {
    case maxie = "Maxie", account = "Account", models = "Models", rules = "Rules", watching = "Watching", flows = "Flows", speed = "Speed", voice = "Voice", permissions = "Permissions",
         connections = "Connections", aboutMe = "About me", backups = "Backups", general = "General"
    var id: String { rawValue }
    var icon: String {
        switch self {
        case .maxie: "pawprint.fill"
        case .account: "key.fill"
        case .models: "cpu.fill"
        case .rules: "hand.raised.fill"
        case .watching: "eye.fill"
        case .flows: "figure.walk.motion"
        case .speed: "gauge.with.needle.fill"
        case .voice: "waveform"
        case .permissions: "lock.shield.fill"
        case .connections: "link"
        case .aboutMe: "person.crop.circle.fill"
        case .backups: "externaldrive.fill.badge.timemachine"
        case .general: "gearshape.fill"
        }
    }
    var subtitle: String {
        switch self {
        case .maxie: "His look, where he lives, his voice and manner."
        case .account: "Your Anthropic key and this month's spend."
        case .models: "Which Claude answers at each tier."
        case .rules: "Per action: allow, ask me, or block."
        case .watching: "What Maxie learns from what you do, and never sees."
        case .flows: "Things you showed it once, to replay."
        case .speed: "First word, measured on every turn."
        case .voice: "\"Hey Max\" on this Mac: wake word, mic, voice."
        case .permissions: "What macOS lets Maxie do."
        case .connections: "Google, jobhunt, resumegen and your iPhone."
        case .aboutMe: "So it never guesses the time, the place or your hours."
        case .backups: "Nightly, encrypted, on this Mac and in iCloud Drive if you use it."
        case .general: "Launch, light mode, Maxie on screen, hotkeys, resources."
        }
    }
    /// Tile colours, one pair per section (all blue/orange family: safe for red-green colour blindness).
    var tint: [Color] {
        switch self {
        case .maxie: [Color(hex: "#9AA8E0"), Color(hex: "#6A7BC0")]
        case .account: [Color(hex: "#FFB07A"), Sunset.coral]
        case .models: [Sunset.periwinkle, Sunset.indigo]
        case .rules: [Color(hex: "#E8744F"), Color(hex: "#8E3B6B")]
        case .watching: [Color(hex: "#8FA2D8"), Color(hex: "#3E4C7A")]
        case .flows: [Sunset.gold, Color(hex: "#C85A2E")]
        case .speed: [Color(hex: "#FFB07A"), Color(hex: "#C85A2E")]
        case .voice: [Sunset.coral, Color(hex: "#8E3B6B")]
        case .permissions: [Color(hex: "#6B7FC2"), Sunset.indigo]
        case .connections: [Color(hex: "#F0A06A"), Color(hex: "#C85A2E")]
        case .aboutMe: [Sunset.gold, Sunset.coral]
        case .backups: [Color(hex: "#7E93CF"), Color(hex: "#2E3A5E")]
        case .general: [Color(hex: "#8A94A8"), Color(hex: "#4A5468")]
        }
    }

    @MainActor @ViewBuilder
    func view(hub: HubClient, notch: NotchController, open: @escaping (SettingsSection) -> Void = { _ in }) -> some View {
        switch self {
        case .maxie: MaxieSection(hub: hub, notch: notch)
        case .account: AccountSection(hub: hub)
        case .models: ModelsSection(hub: hub)
        case .rules: RulesSection(hub: hub)
        case .watching: WatchingSection(hub: hub)
        case .flows: FlowsSection(hub: hub)
        case .speed: SpeedSection(hub: hub)
        case .voice: VoiceSection(hub: hub)
        case .permissions: PermissionsSection(hub: hub)
        case .connections: ConnectionsSection(hub: hub)
        case .aboutMe: AboutMeSection(hub: hub)
        case .backups: BackupsSection(hub: hub)
        case .general: GeneralSection(notch: notch, hub: hub, openMaxie: { open(.maxie) })
        }
    }
}

/// Settings as the same field of light as the panel, wider: a quiet list of words on the
/// left (the one you are on in mist with a knot, the rest at 45%), the section on the right. No sidebar
/// chrome, no icons, no cards. Esc, ⌘W, Done or the red button close it; drag its top to move it.
struct SettingsView: View {
    /// 560 pt; the screenshot tool (DebugSnapshot) can ask for a taller one to show long sections whole.
    static let height = CGFloat(Double(ProcessInfo.processInfo.environment["CLAUDE_NOTCH_SHOT_HEIGHT"] ?? "") ?? 560)
    let hub: HubClient
    let notch: NotchController
    var onDone: () -> Void = {}
    let model: SettingsModel
    @State private var scrolled: CGFloat = 0
    private var section: SettingsSection { model.section }

    init(hub: HubClient, notch: NotchController, model: SettingsModel, onDone: @escaping () -> Void = {}) {
        self.hub = hub
        self.notch = notch
        self.model = model
        self.onDone = onDone
    }

    /// Snapshots and tests: on one section, remembering nothing.
    init(hub: HubClient, notch: NotchController, initial: SettingsSection, onDone: @escaping () -> Void = {}) {
        self.init(hub: hub, notch: notch, model: SettingsModel(section: initial, defaults: nil), onDone: onDone)
    }

    var body: some View {
        HStack(alignment: .top, spacing: 0) {
            list
            VStack(alignment: .leading, spacing: 0) {
                titleRow  // stays put: the section scrolls below it, never under Done
                    .padding(.top, 54).padding(.bottom, 12)
                    .column()
                ScrollView {
                    VStack(alignment: .leading, spacing: 18) {
                        section.view(hub: hub, notch: notch) { model.section = $0 }
                            .textSelection(.enabled)  // every word in a section can be selected and copied
                            .environment(\.settingsScrolled, scrolled)
                            .id(section)
                            .transition(.opacity)
                    }
                    .animation(.easeOut(duration: 0.12), value: section)  // only the content cross-fades; the list never moves
                    .padding(.top, 6).padding(.bottom, 40)
                    .column()
                }
                .scrollIndicators(.never)
                .onScrollGeometryChange(for: CGFloat.self) {  // clamped: no updates once the user is past the shrink
                    min(max($0.contentOffset.y + $0.contentInsets.top, 0), MaxiePreview.shrinkOver)
                } action: { _, y in scrolled = y }
                .clickTarget("settings.content")
            }
        }
        .frame(minWidth: SettingsWindow.minSize.width, maxWidth: .infinity,  // the user can resize it
               minHeight: min(SettingsWindow.minSize.height, Self.height), maxHeight: .infinity)
        .background(LightField(source: UnitPoint(x: 0.12, y: 0), reach: 820))
        .foregroundStyle(Light.words)
        .tint(Light.thread)
        .labelStyle(.titleOnly)
        .toggleStyle(EdgedSwitch())
        .environment(\.edgedControls, true)
        .pickerStyle(.menu)
        .buttonStyle(.borderless)  // drop-down pickers lose their bezel; word buttons keep their own style
        .ignoresSafeArea()  // the see-through title bar is part of the light, not a strip above it
    }

    /// The section's name, and Done on the same row, its right edge in line with the cards.
    /// Outside the ScrollView: a button inside it hit-tests to an AppKit container that could start a window drag.
    private var titleRow: some View {
        HStack(alignment: .firstTextBaseline) {
            SectionHeader(section: section).clickValue("settings.section", section.rawValue)
            Spacer(minLength: 12)
            Button("Done", action: onDone).buttonStyle(.word).keyboardShortcut(.cancelAction).clickTarget("settings.done")
        }
        .clickTarget("settings.title")
    }

    private var list: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text("Maxie").font(Light.name).tracking(-0.5).padding(.bottom, 14).padding(.leading, 18)
            ForEach(SettingsSection.allCases) { s in
                Button { model.section = s } label: {
                    HStack(spacing: 8) {
                        Knot(kind: .claude).opacity(section == s ? 1 : 0)
                        Text(s.rawValue).font(.system(size: 14, weight: section == s ? .semibold : .regular))
                            .foregroundStyle(section == s ? Light.words : Light.faint)
                        Spacer()
                    }
                    .padding(.vertical, 5).padding(.horizontal, 6)
                    .background {  // the one you are on: a filled row and the knot, never colour alone
                        if section == s {
                            RoundedRectangle(cornerRadius: 7, style: .continuous).fill(Light.card)
                                .overlay(RoundedRectangle(cornerRadius: 7, style: .continuous).strokeBorder(Light.edge, lineWidth: 1))
                        }
                    }
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityAddTraits(section == s ? .isSelected : [])
                .clickTarget("settings.row.\(s.rawValue)")
            }
            Spacer()
            Text("v\(Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "")")
                .font(Light.small).foregroundStyle(Light.quiet).padding(.leading, 22)
        }
        .padding(.top, 34).padding(.bottom, 22).padding(.leading, 18).padding(.trailing, 16)  // the selected row stops short of the section
        .frame(width: 190)
    }
}

private extension View {
    /// The 500-pt column the title row and the cards share, so their right edges line up.
    func column() -> some View {
        padding(.trailing, 36).frame(maxWidth: 500, alignment: .leading).frame(maxWidth: .infinity, alignment: .leading)
    }
}

extension EnvironmentValues {
    /// How far the open Settings section has scrolled, up to MaxiePreview.shrinkOver.
    @Entry var settingsScrolled: CGFloat = 0
}
