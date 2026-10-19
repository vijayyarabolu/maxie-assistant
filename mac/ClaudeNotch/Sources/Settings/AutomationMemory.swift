import AppKit
import Foundation

/// What an Automation row in Settings › Permissions shows: macOS's answer now, or, while the app is quit, the
/// last answer and when it was given. `grant` is never `.notRunning`.
struct AutomationStatus: Equatable, Sendable {
    let grant: Grant
    /// Set when this is a remembered answer (the app is quit), nil when macOS answered just now.
    let checked: Date?

    func label(now: Date = Date()) -> String {
        guard grant == .allowed else { return grant == .denied ? "Off" : "" }
        guard let checked else { return "Allowed" }
        let ago = RelativeDateTimeFormatter()
        ago.unitsStyle = .short
        return "Allowed · checked " + ago.localizedString(for: checked, relativeTo: now)
    }
}

/// macOS can only answer the Automation question for a running app: a quit Music came back "not running" and its
/// row said "Turn on", although the grant was still stored. Maxie remembers the last answer per app (on this Mac
/// only, in UserDefaults) and shows that while the app is quit.
struct AutomationMemory {
    static let key = "automationGrants"
    /// Refreshes run every 2 s while Settings is open: the stored date moves at most once a minute.
    private static let rewriteAfter: TimeInterval = 60

    struct Entry: Codable, Equatable {
        let allowed: Bool
        let at: Date
    }

    let defaults: UserDefaults

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
    }

    /// What to show for macOS's answer `live`, remembering it when it is a real answer.
    func resolve(_ live: Grant, for bundleID: String, now: Date = Date()) -> AutomationStatus {
        guard live == .notRunning else {
            record(live, for: bundleID, now: now)
            return AutomationStatus(grant: live, checked: nil)
        }
        guard let e = entries[bundleID] else { return AutomationStatus(grant: .notAsked, checked: nil) }
        return AutomationStatus(grant: e.allowed ? .allowed : .denied, checked: e.at)
    }

    /// Stores a real answer (from Settings or from a task that just ran in the app). `.notAsked` (reset, or never
    /// asked) forgets the old one; `.notRunning` says nothing.
    func record(_ grant: Grant, for bundleID: String, now: Date = Date()) {
        guard grant != .notRunning else { return }
        let old = entries[bundleID]
        let new = grant == .notAsked ? nil : Entry(allowed: grant == .allowed, at: now)
        if old == nil, new == nil { return }
        if let old, let new, old.allowed == new.allowed, now.timeIntervalSince(old.at) < Self.rewriteAfter { return }
        var all = entries
        all[bundleID] = new
        defaults.set(try? JSONEncoder().encode(all), forKey: Self.key)
    }

    private var entries: [String: Entry] {
        defaults.data(forKey: Self.key).flatMap { try? JSONDecoder().decode([String: Entry].self, from: $0) } ?? [:]
    }
}

/// Opens an app behind the user's work: never activated, hidden at once. Maxie works in it without bringing it forward.
enum HiddenLaunch {
    /// How long to wait for a quit app to be ready for Apple Events.
    static let wait: TimeInterval = 5

    static func configuration() -> NSWorkspace.OpenConfiguration {
        let config = NSWorkspace.OpenConfiguration()
        config.activates = false
        config.hides = true
        return config
    }

    static func isRunning(_ bundleID: String) -> Bool {
        NSRunningApplication.runningApplications(withBundleIdentifier: bundleID).contains { $0.isFinishedLaunching }
    }

    /// From a background queue (the bridge): opens the app hidden if it is quit and waits until it is ready.
    static func openAndWait(_ bundleID: String) {
        guard !isRunning(bundleID), let url = NSWorkspace.shared.urlForApplication(withBundleIdentifier: bundleID) else { return }
        NSWorkspace.shared.openApplication(at: url, configuration: configuration()) { _, error in
            if let error { Log.d("hidden launch \(bundleID): \(error.localizedDescription)") }
        }
        let deadline = Date().addingTimeInterval(wait)
        while !isRunning(bundleID), Date() < deadline { Thread.sleep(forTimeInterval: 0.1) }
    }

    /// From Settings' "Turn on": the same, without blocking the main thread.
    static func open(_ bundleID: String) async {
        guard !isRunning(bundleID), let url = NSWorkspace.shared.urlForApplication(withBundleIdentifier: bundleID) else { return }
        _ = try? await NSWorkspace.shared.openApplication(at: url, configuration: configuration())
        let deadline = Date().addingTimeInterval(wait)
        while !isRunning(bundleID), Date() < deadline { try? await Task.sleep(for: .milliseconds(100)) }
    }
}
