import AppKit
import ServiceManagement
import SwiftUI

struct GeneralSection: View {
    let notch: NotchController
    @AppStorage("lightMode") private var lightMode = false
    @AppStorage("showNotchBot") private var showNotchBot = true
    @State private var launchAtLogin = false
    @State private var hideFullscreen = true
    @State private var resources: Resources?
    @State private var note = ""
    let hub: HubClient
    var openMaxie: () -> Void = {}

    struct Service: Decodable, Identifiable { let name: String; let running: Bool; let rss_mb: Double; let cpu_pct: Double; var id: String { name } }
    struct Resources: Decodable { let line: String; let total_rss_mb: Double; let services: [Service] }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            SunsetCard {
                SettingRow(title: "Launch at login") {
                    Toggle("", isOn: $launchAtLogin.onSet { on in setLaunchAtLogin(on) }).labelsHidden()  // the user's tap only
                }
                Color.clear.frame(height: 2)
                SettingRow(title: "Light mode", subtitle: "Off: follows the system.") {
                    Toggle("", isOn: $lightMode).labelsHidden()
                        .onChange(of: lightMode) { _, on in NSApp.appearance = on ? NSAppearance(named: .aqua) : nil }
                }
            }
            SunsetCard {
                HStack(spacing: 14) {
                    MascotLayer(state: hub.state).frame(width: MascotLibrary.shared.slotSize.width, height: MascotLibrary.shared.slotSize.height)
                        .padding(8).background(Sunset.sky, in: RoundedRectangle(cornerRadius: Sunset.radius, style: .continuous))
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Maxie").font(.headline)
                        Text("Right now: \(hub.state.label.lowercased()).").font(.caption).foregroundStyle(.secondary)
                    }
                    Spacer()
                    Toggle("Show", isOn: $showNotchBot)
                        .onChange(of: showNotchBot) { _, on in notch.setMiniVisible(on) }
                }
                Color.clear.frame(height: 2)
                SettingRow(title: "Home and size", subtitle: "His look, home and size live in Settings › Maxie.") {  // one place, no duplicate
                    Button("Open Maxie") { openMaxie() }.buttonStyle(.sunset).clickTarget("general.openMaxie")
                }
                Color.clear.frame(height: 2)
                SettingRow(title: "Hide when an app is fullscreen") {
                    Toggle("", isOn: $hideFullscreen).labelsHidden()
                        .onChange(of: hideFullscreen) { _, on in notch.hideWhenFullscreen = on }
                }
                Color.clear.frame(height: 2)
                SettingRow(title: "Reset position") { Button("Reset") { notch.resetPosition() }.buttonStyle(.sunset) }
            }
            SunsetCard {
                HStack {
                    Text("Resources").font(.headline)
                    Spacer()
                    if let r = resources { Text(String(format: "%.0f MB total", r.total_rss_mb)).font(.caption).foregroundStyle(.secondary) }
                    Button { Task { await loadResources() } } label: { Image(systemName: "arrow.clockwise") }.buttonStyle(.sunset)
                }
                if let r = resources {
                    ForEach(r.services) { s in
                        HStack {
                            Label(s.name.capitalized, systemImage: s.running ? "circle.fill" : "circle")
                                .foregroundStyle(s.running ? Sunset.good : Color.secondary)
                            Spacer()
                            Text(s.running ? String(format: "%.0f MB  ·  %.0f%% CPU", s.rss_mb, s.cpu_pct) : "off")
                                .font(.system(.callout, design: .monospaced)).foregroundStyle(.secondary)
                        }
                    }
                } else {
                    Text("…").foregroundStyle(.secondary)
                }
            }
            SunsetCard {
                Text("Hotkeys").font(.headline)
                hotkey(HotKeys.optionSpace.label, "Open the panel with the cursor in the box (again: close)")
                hotkey(HotKeys.optionShiftS.label, "Look at my screen")
                hotkey("⌘,", "Settings")
                hotkey("⌘.", "Stop the current task")
                hotkey("Esc", "Close the panel")
            }
            HStack {
                Button { MacHelpers.openLogs() } label: { Label("Logs", systemImage: "doc.text.magnifyingglass") }.buttonStyle(.sunset)
                Button { MacHelpers.kickstart(MacHelpers.hubLabel); note = "Hub restarting." } label: { Label("Restart hub", systemImage: "arrow.clockwise") }.buttonStyle(.sunset)
                Button { MacHelpers.kickstart(MacHelpers.voiceLabel); note = "Voice helper restarting." } label: { Label("Restart voice", systemImage: "waveform") }.buttonStyle(.sunset)
                Spacer()
                Button("Quit Maxie") { NSApp.terminate(nil) }.buttonStyle(.sunset)
            }
            NoteLine(text: note)
        }
        .onAppear {
            hideFullscreen = notch.hideWhenFullscreen
        }
        .task {
            launchAtLogin = await Task.detached { SMAppService.mainApp.status == .enabled }.value  // an XPC call: off the click path
            await loadResources()
        }
    }

    private func hotkey(_ key: String, _ what: String) -> some View {
        HStack {
            Text(key).font(.system(.callout, design: .rounded, weight: .semibold))
                .padding(.horizontal, 8).padding(.vertical, 3)
                .background(Sunset.accent.opacity(0.12), in: RoundedRectangle(cornerRadius: 6, style: .continuous))
                .frame(width: 90, alignment: .leading)
            Text(what).font(.callout).foregroundStyle(.secondary)
        }
    }

    private func setLaunchAtLogin(_ on: Bool) {
        do {
            if on { try SMAppService.mainApp.register() } else { try SMAppService.mainApp.unregister() }
        } catch {
            note = "Couldn't change launch at login: \(error.localizedDescription)"
            launchAtLogin = SMAppService.mainApp.status == .enabled
        }
    }

    private func loadResources() async {
        resources = await hub.fetch("/api/resources")
    }
}
