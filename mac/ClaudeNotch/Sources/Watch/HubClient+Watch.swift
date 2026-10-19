import AppKit
import Foundation

/// "…want me to do it?" from the hub (maxie.watch), until the user answers.
struct WatchSuggestion: Equatable {
    let id: Int
    let text: String
}

/// Watch and learn on the hub: eyes open or closed, the apps the user excludes, the open question.
extension HubClient {
    struct WatchInfo: Decodable {
        struct Suggestion: Decodable { let id: Int; let text: String }
        let watching: Bool
        let excluded: [String]
        let kept_days: Int
        let today: Int
        let habits: [String]
        let routines: [String]
        let suggestion: Suggestion?
    }

    @discardableResult
    func syncWatch() async -> WatchInfo? {
        guard let w: WatchInfo = await fetch("/api/watch") else { return nil }
        eyesOpen = w.watching
        watchExcluded = w.excluded
        suggestion = w.suggestion.map { WatchSuggestion(id: $0.id, text: $0.text) }
        return w
    }

    /// The eye toggle (menu bar, panel, Settings): at once here, then on the hub, which tells every screen.
    func setWatching(_ on: Bool) {
        eyesOpen = on
        Task { await put("/api/watch", dict: ["watching": on]) }
    }

    func setExcluded(_ apps: [String]) async {
        watchExcluded = apps
        await put("/api/watch", dict: ["excluded": apps])
    }

    func answerSuggestion(yes: Bool) {
        guard let s = suggestion else { return }
        suggestion = nil
        Task { await postJSON("/api/watch/suggestions/\(s.id)", dict: ["answer": yes ? "yes" : "no"]) }
    }

    /// The hub's base state says whether Maxie is watching: "closed" means not; "idle" means yes; "sleep" says
    /// nothing about it.
    func noteEyes(base: String?) {
        if base == "closed" { eyesOpen = false } else if base == "idle" { eyesOpen = true }
    }
}

/// A routine the user said yes to: open its apps in the background. Never takes focus from what they are doing.
enum RoutineOpener {
    @MainActor
    static func open(_ bundles: [String]) {
        let config = NSWorkspace.OpenConfiguration()
        config.activates = false
        config.addsToRecentItems = false
        for id in bundles {
            guard let url = NSWorkspace.shared.urlForApplication(withBundleIdentifier: id) else { continue }
            NSWorkspace.shared.openApplication(at: url, configuration: config) { _, error in
                if let error { Log.d("routine: couldn't open \(id): \(error.localizedDescription)") }
            }
        }
    }
}
