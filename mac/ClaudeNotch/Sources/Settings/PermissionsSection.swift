import AppKit
import SwiftUI

/// Settings › Permissions: one "Turn on" per row that shows the real macOS prompt from Maxie.app, live status
/// (every 2 s while open, and when Maxie comes to the front), no Re-check button.
struct PermissionsSection: View {
    let hub: HubClient
    @StateObject private var center = PermissionCenter()
    private let tick = Timer.publish(every: 2, on: .main, in: .common).autoconnect()

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            if !center.stableSigning { signingBanner }
            SunsetCard {
                ForEach(Array(Permission.allCases.enumerated()), id: \.element) { i, p in
                    if i > 0 { Color.clear.frame(height: 2) }
                    row(p)
                    if p == .accessibility, center.accessibilityLooksStale { staleHint }
                    if p == .screen { ScreenHint(advice: center.screen) }
                }
            }
            SunsetCard {
                Label("Automation", systemImage: "applescript").font(.headline)
                Text("Maxie works in these apps in the background, without bringing them to the front.")
                    .font(Light.small).foregroundStyle(Light.quiet)
                ForEach(AutomationTarget.all) { t in
                    AutomationRow(target: t, status: center.automation[t] ?? AutomationStatus(grant: .notAsked, checked: nil),
                                  turnOn: { await center.turnOn(t) })
                }
            }
            NoteLine(text: center.note)
        }
        .task {
            center.onPlace = { place in Task { await savePlace(place) } }
            await center.refresh()
        }
        .onReceive(tick) { _ in Task { await center.refresh() } }
        .onReceive(NotificationCenter.default.publisher(for: NSApplication.didBecomeActiveNotification)) { _ in
            center.cameBack()
            Task { await center.refresh() }
        }
    }

    private func row(_ p: Permission) -> some View {
        let grant = center.grants[p] ?? .notAsked
        return SettingRow(title: p.title, subtitle: p.why) {
            HStack(spacing: 8) {
                if grant != .notAsked { StatusMark(ok: grant == .allowed, yes: "Allowed", no: "Off") }
                PermissionAction(grant: grant, ask: { await center.turnOn(p) }, pane: p.pane)
            }
        }
    }

    private var staleHint: some View {
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: "arrow.triangle.2.circlepath").foregroundStyle(Light.quiet)
            VStack(alignment: .leading, spacing: 6) {
                Text("Already on in System Settings? That switch belongs to an older build of Maxie. Select Maxie in the list, press −, then + and add Maxie again.")
                    .font(Light.small).foregroundStyle(Light.quiet).fixedSize(horizontal: false, vertical: true)
                Button("Open Accessibility") { MacHelpers.openSystemSettings(Permission.accessibility.pane) }.buttonStyle(.sunset)
            }
        }
        .padding(.leading, 2)
    }

    private var signingBanner: some View {
        SunsetCard {
            Label("This build of Maxie is ad-hoc signed", systemImage: "exclamationmark.shield").font(.headline)
            Text("macOS forgets its permissions after every rebuild. One-time fix: build with make notch, which signs with a stable local identity (scripts/make_signing_identity.sh makes it the first time).")
                .font(Light.small).foregroundStyle(Light.quiet).fixedSize(horizontal: false, vertical: true)
        }
    }

    private func savePlace(_ p: LocationAsker.Place) async {
        let zone = TimeZone(identifier: p.timezone).map(LocationFollower.zoneName) ?? ""
        let (code, _) = await hub.postJSON("/api/place/here", dict: ["name": p.name, "lat": p.lat, "lon": p.lon, "timezone": p.timezone,
                                                                    "zone_name": zone, "first": true])  // fills About me in, says nothing
        center.note = code == 200 ? "About me › City: \(p.name) (from Location)." : ""
    }
}

/// Screen Recording's next step once macOS won't ask again: where to flip the switch, an old
/// build's switch to remove, or the reopen macOS needs before the switch counts.
struct ScreenHint: View {
    let advice: ScreenAccess.Advice

    var body: some View {
        if let text {
            HStack(alignment: .top, spacing: 8) {
                Image(systemName: "rectangle.on.rectangle").foregroundStyle(Light.quiet)
                VStack(alignment: .leading, spacing: 6) {
                    Text(text).font(Light.small).foregroundStyle(Light.quiet).fixedSize(horizontal: false, vertical: true)
                    HStack(spacing: 8) {
                        if advice != .reopen {
                            Button("Open Screen Recording") { MacHelpers.openSystemSettings(Permission.screen.pane) }.buttonStyle(.sunset)
                        }
                        Button("Reopen Maxie") { MacHelpers.reopen() }.buttonStyle(advice == .reopen ? .sunsetProminent : .sunset)
                    }
                }
            }
            .padding(.leading, 2)
        }
    }

    private var text: String? {
        switch advice {
        case .allowed, .turnOn: nil
        case .turnOnInSettings: ScreenAccess.line
        case .reopen: "On for this Maxie. macOS needs a reopen before it counts."
        case .removeOld: "That switch belongs to an older build of Maxie. Select Maxie in the list, press −, then + and add this one. Then reopen Maxie."
        }
    }
}

/// One app Maxie drives. A quit app shows its last answer ("Allowed · checked 2 hr. ago"), not "Turn on".
struct AutomationRow: View {
    let target: AutomationTarget
    let status: AutomationStatus
    var turnOn: @MainActor () async -> Void = {}

    var body: some View {
        SettingRow(title: target.name) {
            HStack(spacing: 8) {
                if status.grant != .notAsked { StatusMark(ok: status.grant == .allowed, yes: status.label(), no: "Off") }
                PermissionAction(grant: status.grant, ask: turnOn, pane: "com.apple.preference.security?Privacy_Automation")
            }
        }
    }
}

/// "Turn on" while macOS can still ask; "Open Settings" once the user said no (macOS never asks twice).
struct PermissionAction: View {
    let grant: Grant
    let ask: @MainActor () async -> Void
    let pane: String

    var body: some View {
        switch grant {
        case .allowed:
            EmptyView()
        case .denied:
            Button("Open Settings") { MacHelpers.openSystemSettings(pane) }.buttonStyle(.sunset)
        case .notAsked, .notRunning:
            Button("Turn on") { Task { await ask() } }.buttonStyle(.sunsetProminent)
        }
    }
}
