import SwiftUI

struct ModelsSection: View {
    let hub: HubClient
    @State private var active: [String: String] = [:]
    @State private var auto = true
    @State private var lines: [String] = []
    @State private var guessed: [String: Bool] = [:]
    @State private var askMyClaude = true

    struct Models: Decodable { let active: [String: String]; let auto_upgrade: Bool; let lines: [String]; let price_guessed: [String: Bool] }
    struct Settings: Decodable { let ask_my_claude: Bool }
    private let tiers: [(String, String, String)] = [
        ("haiku", "Haiku", "Routing, quick answers, plain Mac actions"),
        ("sonnet", "Sonnet", "Real tasks: mail, calendar, research, multi-step"),
        ("opus", "Opus", "\"Think hard\""),
    ]

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            SunsetCard {
                ForEach(Array(tiers.enumerated()), id: \.offset) { i, t in
                    if i > 0 { Color.clear.frame(height: 2) }
                    SettingRow(title: t.1, subtitle: t.2) {
                        HStack(spacing: 8) {
                            if guessed[t.0] == true {
                                Label("price guessed", systemImage: "questionmark.circle").font(.caption).foregroundStyle(Sunset.warn)
                            }
                            Text(active[t.0] ?? "none").font(.system(.callout, design: .monospaced)).foregroundStyle(.secondary)
                        }
                    }
                }
            }
            SunsetCard {
                SettingRow(title: "Auto-upgrade", subtitle: "Daily check for a newer stable Haiku / Sonnet / Opus; a smoke test runs before any switch.") {
                    Toggle("", isOn: $auto.onSet { on in Task { await hub.putSettings(["auto_upgrade_models": on]); await load() } })
                        .labelsHidden()
                }
                Color.clear.frame(height: 2)
                SettingRow(title: "Pin current models", subtitle: "Keep today's ids until auto-upgrade is turned back on.") {
                    Button("Pin") { Task { await hub.postJSON("/api/models/pin", dict: [:]); await load() } }.buttonStyle(.sunset)
                }
                if !lines.isEmpty {
                    Color.clear.frame(height: 2)
                    ForEach(lines, id: \.self) { Label($0, systemImage: "arrow.up.circle").font(.caption).foregroundStyle(.secondary) }
                }
            }
            SunsetCard {
                SettingRow(title: "Ask my Claude", subtitle: "\"Ask my Claude\", \"use my plan\" or \"think deeply\" runs the official Claude Code CLI on this Mac with your login: nothing is billed to the API key. It never touches the Maxie app or the website.") {
                    Toggle("", isOn: $askMyClaude.onSet { on in Task { await hub.putSettings(["ask_my_claude": on]) } })
                        .labelsHidden()
                }
            }
        }
        .task { await load() }
    }

    private func load() async {
        if let m: Models = await hub.fetch("/api/models") {
            active = m.active; auto = m.auto_upgrade; lines = m.lines; guessed = m.price_guessed
        }
        if let st: Settings = await hub.fetch("/api/settings") { askMyClaude = st.ask_my_claude }
    }
}
