import AppKit
import SwiftUI

struct BackupsSection: View {
    let hub: HubClient
    @State private var info: Info?
    @State private var names: [String] = []
    @State private var chosen = ""
    @State private var note = ""
    @State private var busy = false

    struct Info: Decodable { let line: String; let app_lines: [String]; let warnings: [String]; let stale: Bool; let last_ok: Bool }
    struct Names: Decodable { let backups: [String] }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            SunsetCard {
                if let info {
                    let bad = info.stale || !info.last_ok
                    Label(info.line, systemImage: bad ? "exclamationmark.triangle.fill" : "checkmark.shield.fill")
                        .font(.headline).foregroundStyle(bad ? Sunset.bad : Sunset.good)
                    ForEach(info.app_lines, id: \.self) { line in
                        Label(line, systemImage: "shippingbox").font(.callout).foregroundStyle(.secondary)
                    }
                    ForEach(info.warnings, id: \.self) { Label($0, systemImage: "exclamationmark.triangle").font(.caption).foregroundStyle(Sunset.warn) }
                } else {
                    Label("Loading…", systemImage: "ellipsis.circle").foregroundStyle(.secondary)
                }
                Color.clear.frame(height: 2)
                HStack {
                    Button { backupNow() } label: { Label(busy ? "Backing up…" : "Back up now", systemImage: "arrow.down.circle.fill") }
                        .buttonStyle(.sunsetProminent).disabled(busy)
                    Button { Task { await load() } } label: { Label("Refresh", systemImage: "arrow.clockwise") }.buttonStyle(.sunset)
                }
            }
            SunsetCard {
                Text("Restore").font(.headline)
                SettingRow(title: "Backup", subtitle: "Restores into a folder you pick; nothing is overwritten in place. RESTORE.md inside lists the secrets to re-add.") {
                    HStack {
                        Picker("", selection: $chosen) { ForEach(names, id: \.self) { Text($0).tag($0) } }.labelsHidden().boxed().frame(maxWidth: 240)
                        Button("Restore…") { restore() }.buttonStyle(.sunset).disabled(chosen.isEmpty || busy)
                    }
                }
            }
            SunsetCard {
                SettingRow(title: "Clear test data",
                           subtitle: "Removes what tests and evals left behind: test conversations, fixture names (AcmeAI, Nimbus…), demo facts. Your own conversations, spend and the audit log stay.") {
                    Button("Clear test data") { clearTestData() }.buttonStyle(.sunset).disabled(busy)
                }
            }
            NoteLine(text: note)
        }
        .task { await load() }
    }

    private func load() async {
        info = await hub.fetch("/api/backup")
        if let n: Names = await hub.fetch("/api/backup/list") {
            names = n.backups
            if chosen.isEmpty { chosen = names.first ?? "" }
        }
    }

    private func backupNow() {
        busy = true
        Task {
            let (code, body) = await hub.postJSON("/api/backup?verify=true", dict: [:])
            note = code == 200 ? "Backup done and verified." : "Backup failed: \(body)"
            busy = false
            await load()
        }
    }

    private func clearTestData() {
        struct Cleared: Decodable { let total: Int }
        busy = true
        Task {
            let (code, body) = await hub.postJSON("/api/testdata/clear", dict: [:])
            let total = (try? JSONDecoder().decode(Cleared.self, from: Data(body.utf8)))?.total
            note = code == 200 ? (total == 0 ? "No test data found." : "Cleared \(total ?? 0) test rows.") : "Couldn't clear: \(body)"
            busy = false
        }
    }

    private func restore() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.canCreateDirectories = true
        panel.prompt = "Restore here"
        guard panel.runModal() == .OK, let dir = panel.url else { return }
        busy = true
        Task {
            let (code, body) = await hub.postJSON("/api/backup/restore", dict: ["name": chosen, "folder": dir.path])
            note = code == 200 ? "Restored into \(dir.path)." : "Restore failed: \(body)"
            busy = false
            if code == 200 { NSWorkspace.shared.open(dir) }
        }
    }
}
