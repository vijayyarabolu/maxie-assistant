import AppKit
import SwiftUI
import UniformTypeIdentifiers

/// Settings › Watching: eyes open or closed, what is never seen, the apps you leave out, what
/// Maxie has learned and the routines you said yes to. "Forget today" drops today's notes.
struct WatchingSection: View {
    let hub: HubClient
    @State private var info: HubClient.WatchInfo?
    @State private var note = ""

    static let never = "Never seen: banking, health and password apps, password fields, web pages in any browser "
        + "(private windows look the same), and anything while the screen is locked. No keystrokes, no screenshots."

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            SunsetCard {
                SettingRow(title: "Watch and learn", subtitle: eyesLine) {
                    Toggle("", isOn: Binding(get: { hub.eyesOpen }, set: { on in hub.setWatching(on); Task { await load() } }))
                        .labelsHidden().clickTarget("watch.eyes")
                }
                Color.clear.frame(height: 2)
                SettingRow(title: "Forget today", subtitle: "Drops everything noted since midnight.") {
                    Button("Forget today") { forgetToday() }.buttonStyle(.sunset).clickTarget("watch.forgetToday")
                }
            }
            SunsetCard {
                Label("Never watched", systemImage: "eye.slash").font(.headline)
                Text(Self.never).font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                ForEach(hub.watchExcluded, id: \.self) { app in
                    HStack {
                        Text(app).font(.callout)
                        Spacer()
                        Button("Remove") { Task { await hub.setExcluded(hub.watchExcluded.filter { $0 != app }) } }
                            .buttonStyle(.sunset)
                    }
                }
                Button { addApp() } label: { Label("Leave out an app…", systemImage: "plus") }
                    .buttonStyle(.sunset).clickTarget("watch.addApp")
            }
            SunsetCard {
                Label("What I've learned", systemImage: "sparkles").font(.headline)
                let learned = (info?.habits ?? []) + (info?.routines ?? [])
                if learned.isEmpty {
                    Text("Nothing yet. A habit needs about three days.").font(.callout).foregroundStyle(.secondary)
                }
                ForEach(info?.habits ?? [], id: \.self) { Text($0).font(.callout) }
                ForEach(info?.routines ?? [], id: \.self) { r in Label(r, systemImage: "clock").font(.callout) }
                if let s = hub.suggestion { SuggestionCard(suggestion: s, hub: hub) }
            }
            NoteLine(text: note)
        }
        .task { await load() }
    }

    private var eyesLine: String {
        guard hub.eyesOpen else { return "Eyes closed: nothing is noted. Say \"watch again\" to open them." }
        let today = info.map { " Today: \($0.today) note\($0.today == 1 ? "" : "s")." } ?? ""
        return "Apps you switch to and window titles, on this Mac only, kept \(info?.kept_days ?? 30) days.\(today)"
    }

    private func load() async { info = await hub.syncWatch() }

    private func forgetToday() {
        Task {
            let (code, _) = await hub.postJSON("/api/watch/forget-today", dict: [:])
            note = code == 200 ? "Forgot today." : "Couldn't reach the hub."
            await load()
        }
    }

    /// His pick from /Applications: its bundle id goes on the never list.
    private func addApp() {
        let panel = NSOpenPanel()
        panel.directoryURL = URL(fileURLWithPath: "/Applications")
        panel.allowedContentTypes = [.applicationBundle]
        panel.prompt = "Leave out"
        guard panel.runModal() == .OK, let url = panel.url, let id = Bundle(url: url)?.bundleIdentifier else { return }
        Task {
            await hub.setExcluded(Array(Set(hub.watchExcluded + [id])).sorted())
            note = "Never watched: \(url.deletingPathExtension().lastPathComponent)."
        }
    }
}

/// "…want me to do it?" with Yes and No (the panel and Settings).
struct SuggestionCard: View {
    let suggestion: WatchSuggestion
    let hub: HubClient

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(suggestion.text).font(Light.text).fixedSize(horizontal: false, vertical: true)
            HStack(spacing: 8) {
                Button("Yes, do it") { hub.answerSuggestion(yes: true) }.buttonStyle(.sunsetProminent).clickTarget("suggestion.yes")
                Button("No thanks") { hub.answerSuggestion(yes: false) }.buttonStyle(.sunset).clickTarget("suggestion.no")
            }
        }
        .padding(12)
        .overlay(RoundedRectangle(cornerRadius: Sunset.radius, style: .continuous).strokeBorder(Light.edge, lineWidth: 1))
    }
}

/// The panel's eye: always visible next to Maxie's name, with words (never colour alone).
struct EyeToggle: View {
    let hub: HubClient

    var body: some View {
        Button { hub.setWatching(!hub.eyesOpen) } label: {
            Label(hub.eyesOpen ? "Watching" : "Not watching", systemImage: hub.eyesOpen ? "eye" : "eye.slash")
                .font(Light.small)
                .foregroundStyle(hub.eyesOpen ? Light.words : Light.quiet)
        }
        .buttonStyle(.plain)
        .help(hub.eyesOpen ? "Maxie learns from what you do. Click to close his eyes." : "Eyes closed. Click to let Maxie watch and learn.")
        .accessibilityLabel(hub.eyesOpen ? "Watching: on" : "Watching: off")
        .clickTarget("panel.eyes")
    }
}
