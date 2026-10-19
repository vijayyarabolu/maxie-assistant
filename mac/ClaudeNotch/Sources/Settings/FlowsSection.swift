import SwiftUI

/// Flows the user showed Maxie once (watch me once). Run replays it now (that click is the OK); Forget removes it.
struct FlowsSection: View {
    let hub: HubClient
    @State private var flows: [Flow] = []
    @State private var loaded = false
    @State private var note = ""

    struct Flow: Decodable, Identifiable { let id: Int; let name: String; let line: String; let runs: Int; let last_result: String }
    struct Flows: Decodable { let flows: [Flow] }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            SunsetCard {
                if loaded && flows.isEmpty {
                    Text("None yet. Press \(HotKeys.optionShiftW.label) or say \"watch me\", do it once, press \(HotKeys.optionShiftW.label) again, then name it.")
                        .font(Light.text).foregroundStyle(Light.quiet)
                }
                ForEach(flows) { f in
                    SettingRow(title: f.name, subtitle: f.line + (f.last_result.isEmpty ? "" : ". Last run: \(f.last_result)")) {
                        HStack(spacing: 16) {
                            Button("Run") { run(f) }.buttonStyle(.wordPrimary)
                            Button("Forget") { forget(f) }.buttonStyle(.wordDanger)
                        }
                    }
                }
            }
            Text("Saying \"replay the expense report flow\" asks for your OK first (Settings › Rules). Passwords are never recorded. Replaying needs Accessibility for Maxie.")
                .font(Light.small).foregroundStyle(Light.quiet)
            NoteLine(text: note)
        }
        .task { await load() }
    }

    private func run(_ f: Flow) {
        Task {
            let (code, _) = await hub.postJSON("/api/flows/\(f.id)/run", dict: [:])
            note = code == 200 ? "Replaying \(f.name)." : "Couldn't start it (\(code))."
        }
    }

    private func forget(_ f: Flow) {
        Task {
            let (code, _) = await hub.delete("/api/flows/\(f.id)")
            note = code == 200 ? "Forgot \(f.name)." : "Couldn't forget it (\(code))."
            await load()
        }
    }

    private func load() async {
        if let r: Flows = await hub.fetch("/api/flows") { flows = r.flows }
        loaded = true
    }
}
