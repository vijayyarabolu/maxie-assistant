import SwiftUI

/// Rules: for each action, Allow / Ask me / Block. Defaults are safe (anything leaving the Mac asks).
struct RulesSection: View {
    let hub: HubClient
    @State private var rules: [Rule] = []
    @State private var note = ""
    @State private var screenWhenAway = false  // a screen task runs once the user steps away

    struct Rule: Decodable, Identifiable { let name: String; let kind: String; let group: String; let description: String; var rule: String; let `default`: String; var id: String { name } }
    struct Rules: Decodable { let rules: [Rule] }
    private let groups = ["Leaves the Mac", "Changes things", "Reads"]
    private let groupIcon = ["Leaves the Mac": "paperplane.fill", "Changes things": "wand.and.stars", "Reads": "eye.fill"]

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            SunsetCard {
                Label("The screen", systemImage: "macwindow").font(.headline)
                SettingRow(title: "Use the screen when I'm away",
                           subtitle: "Maxie works in the background and never takes your screen. A task that truly needs the keyboard and mouse (a replayed flow) asks \"Can I use the screen for ~20 s?\" first. On: it waits until you've left the Mac 10 minutes (not locked, nothing playing), then does it. Sending, buying and deleting still wait for your tap.") {
                    Toggle("", isOn: $screenWhenAway.onSet { on in
                        Task {
                            let (code, _) = await hub.putSettings(["screen_when_away": on])
                            note = code == 200 ? (on ? "Screen tasks run when you're away." : "Screen tasks ask first.") : "Not saved."
                        }
                    })
                    .labelsHidden()
                    .clickTarget("rules.screenAway")
                }
            }
            ForEach(groups, id: \.self) { g in
                let rows = rules.filter { $0.group == g }
                if !rows.isEmpty {
                    SunsetCard {
                        Label(g, systemImage: groupIcon[g] ?? "circle").font(.headline)
                        ForEach(rows) { r in
                            Color.clear.frame(height: 2)
                            SettingRow(title: r.name.replacingOccurrences(of: "_", with: " "), subtitle: r.description) {
                                WordChoice(options: [("Allow", "allow"), ("Ask me", "ask"), ("Block", "block")],
                                           selection: binding(for: r))
                            }
                        }
                    }
                }
            }
            if rules.isEmpty { SunsetCard { Label("Loading…", systemImage: "ellipsis.circle").foregroundStyle(.secondary) } }
            Label("Ask me: the action waits for your OK in the panel or on the phone. Block: Maxie tells you it can't. Every change is in the audit log.", systemImage: "hand.raised.fill")
                .font(.caption).foregroundStyle(.secondary)
            NoteLine(text: note)
        }
        .task { await load() }
    }

    private func binding(for r: Rule) -> Binding<String> {
        Binding(get: { rules.first { $0.id == r.id }?.rule ?? r.rule },
                set: { new in
                    if let i = rules.firstIndex(where: { $0.id == r.id }) { rules[i].rule = new }
                    Task {
                        let (code, body) = await hub.put("/api/rules", dict: ["name": r.name, "rule": new])
                        note = code == 200 ? "\(r.name.replacingOccurrences(of: "_", with: " ")): \(new == "ask" ? "ask me" : new)." : "Not saved: \(body)"
                    }
                })
    }

    private struct ScreenSetting: Decodable { let screen_when_away: Bool }

    private func load() async {
        if let r: Rules = await hub.fetch("/api/rules") { rules = r.rules }
        if let st: ScreenSetting = await hub.fetch("/api/settings") { screenWhenAway = st.screen_when_away }
    }
}
