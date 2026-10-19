import AppKit
import SwiftUI

/// Settings › About me: your name, where you are and your work hours, used on every turn. City and clock follow
/// Location unless you keep a fixed city; work hours stay yours to set.
struct AboutMeSection: View {
    let hub: HubClient
    @State private var name = ""
    @State private var city = ""
    @State private var cityQuery = ""
    @State private var timezone = ""
    @State private var workStart = "09:00"
    @State private var workEnd = "18:00"
    @State private var note = ""
    @State private var following = true
    @State private var grant: Grant = LocationFollower.shared.grant

    struct Settings: Decodable {
        let user_name: String; let weather_place: String; let timezone: String; let work_start: String; let work_end: String
        let follow_location: Bool?
    }
    struct Place: Decodable { let place: String; let timezone: String }

    private let zones = TimeZone.knownTimeZoneIdentifiers.filter { $0.hasPrefix("America/") || $0.hasPrefix("Asia/Kolkata") }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            SunsetCard {
                SettingRow(title: "Name") { TextField("Alex", text: $name).textFieldStyle(.line).frame(width: 200) }
                Color.clear.frame(height: 2)
                SettingRow(title: "Follow my location", subtitle: Self.followLine(following: following, grant: grant)) {
                    HStack(spacing: 8) {
                        if following { locationButton }
                        Toggle("", isOn: $following.onSet { on in setFollowing(on) }).labelsHidden()
                    }
                }
                Color.clear.frame(height: 2)
                if Self.followsLocation(following: following, grant: grant) {
                    SettingRow(title: "City", subtitle: city.isEmpty ? "Waiting for the first fix…" : city) { EmptyView() }
                    Color.clear.frame(height: 2)
                    SettingRow(title: "Timezone", subtitle: timezone) { EmptyView() }
                } else {
                    SettingRow(title: "City", subtitle: city.isEmpty ? "Not set" : city) {
                        HStack {
                            TextField("Look up a city…", text: $cityQuery).textFieldStyle(.line).frame(width: 170)
                            Button("Set") { lookup() }.buttonStyle(.sunset).fixedSize().disabled(cityQuery.isEmpty)
                        }
                    }
                    Color.clear.frame(height: 2)
                    SettingRow(title: "Timezone") {
                        Picker("", selection: $timezone) { ForEach(zones, id: \.self) { Text($0).tag($0) } }.labelsHidden().boxed().frame(width: 220)
                    }
                }
                Color.clear.frame(height: 2)
                SettingRow(title: "Work hours") {
                    HStack(spacing: 6) {
                        TextField("09:00", text: $workStart).textFieldStyle(.line).frame(width: 64)
                        Text("to").foregroundStyle(.secondary)
                        TextField("18:00", text: $workEnd).textFieldStyle(.line).frame(width: 64)
                    }
                }
            }
            HStack {
                Button("Save") { save() }.buttonStyle(.sunsetProminent)
                NoteLine(text: note)
            }
            Label("Used on every turn so Maxie never guesses the time, the place or your hours.", systemImage: "clock")
                .font(.caption).foregroundStyle(.secondary)
        }
        .task { await load() }
        .onReceive(NotificationCenter.default.publisher(for: NSApplication.didBecomeActiveNotification)) { _ in
            grant = LocationFollower.shared.grant  // the user may have flipped it in System Settings
        }
    }

    /// City and clock come from Location only when the user wants that and macOS allows it.
    static func followsLocation(following: Bool, grant: Grant) -> Bool { following && grant == .allowed }

    static func followLine(following: Bool, grant: Grant) -> String {
        guard following else { return "Off: the city you set below stays." }
        switch grant {
        case .allowed: return "City and clock follow you. Low power: only big moves."
        case .denied: return "Location is off for Maxie in System Settings."
        case .notAsked, .notRunning: return "Turn on Location so your city and clock follow you."
        }
    }

    /// The same one-tap "Turn on" as Settings › Permissions.
    @ViewBuilder
    private var locationButton: some View {
        switch grant {
        case .allowed:
            EmptyView()
        case .denied:
            Button("Open Settings") { MacHelpers.openSystemSettings(Permission.location.pane) }.buttonStyle(.sunset)
        case .notAsked, .notRunning:
            Button("Turn on") { askLocation() }.buttonStyle(.sunsetProminent).clickTarget("aboutMe.location.turnOn")
        }
    }

    private func askLocation() {
        LocationFollower.shared.askPermission()
        Task {  // the macOS prompt answers asynchronously; look for up to half a minute
            for _ in 0..<30 where grant == .notAsked {
                try? await Task.sleep(for: .seconds(1))
                grant = LocationFollower.shared.grant
            }
        }
    }

    private func setFollowing(_ on: Bool) {
        NotificationCenter.default.post(name: .followLocationChanged, object: on)
        Task {
            let (code, body) = await hub.putSettings(["follow_location": on])
            note = code == 200 ? (on ? "Following your location." : "Keeping the city you set.") : "Not saved: \(body)"
        }
    }

    private func load() async {
        if let s: Settings = await hub.fetch("/api/settings") {
            name = s.user_name; city = s.weather_place; timezone = s.timezone; workStart = s.work_start; workEnd = s.work_end
            following = s.follow_location ?? true
        }
    }

    private func lookup() {
        Task {
            let (code, body) = await hub.postJSON("/api/place", dict: ["query": cityQuery])
            if code == 200, let p = try? JSONDecoder().decode(Place.self, from: Data(body.utf8)) {
                city = p.place; timezone = p.timezone; cityQuery = ""; note = "City set to \(p.place)."
            } else {
                note = "Couldn't find that place: \(body)"
            }
        }
    }

    private func save() {
        Task {
            let (code, body) = await hub.putSettings(["user_name": name, "timezone": timezone, "work_start": workStart, "work_end": workEnd])
            note = code == 200 ? "Saved." : "Not saved: \(body)"
        }
    }
}
