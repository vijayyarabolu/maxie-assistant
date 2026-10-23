import Foundation

/// The mascot's states, exactly as the hub sends them on /api/events.
enum BotState: String, CaseIterable, Sendable {
    case idle, listen, think, talk, happy, proud, sleep, gentle, wave, closed, stretch

    var label: String {
        switch self {
        case .idle: "Idle"
        case .listen: "Listening"
        case .think: "Thinking"
        case .talk: "Talking"
        case .happy: "Happy"
        case .proud: "Proud of you"
        case .sleep: "Sleeping"
        case .gentle: "With you"
        case .wave: "Waving"
        case .closed: "Eyes closed, not watching"
        case .stretch: "Stretch break"
        }
    }
}

struct TranscriptLine: Identifiable, Equatable {
    let id = UUID()
    let role: String   // "user" | "assistant"
    var text: String
    var done: Bool
}

struct Step: Identifiable, Equatable {
    let id = UUID()
    let name: String
    let ok: Bool
}

struct Confirmation: Identifiable, Decodable, Equatable {
    let id: String
    let summary: String
    let afterUntrusted: Bool
    var status = "pending"  // "away": a screen task that runs once the user steps away
    var screen = false      // it drives the real screen: the card asks "Can I use the screen for ~20 s?"

    enum CodingKeys: String, CodingKey {
        case id, summary, status, screen
        case afterUntrusted = "after_untrusted"
    }

    init(id: String, summary: String, afterUntrusted: Bool, status: String = "pending", screen: Bool = false) {
        self.id = id
        self.summary = summary
        self.afterUntrusted = afterUntrusted
        self.status = status
        self.screen = screen
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        summary = try c.decode(String.self, forKey: .summary)
        afterUntrusted = try c.decodeIfPresent(Bool.self, forKey: .afterUntrusted) ?? false
        status = try c.decodeIfPresent(String.self, forKey: .status) ?? "pending"
        screen = try c.decodeIfPresent(Bool.self, forKey: .screen) ?? false
    }
}

struct TaskStep: Decodable, Equatable {
    let name: String
    let ok: Bool
    let ms: Int
}

/// One task in Activity, as /api/tasks and the "task" events send it.
struct TaskInfo: Decodable, Identifiable, Equatable {
    let id: Int
    let ts: Double
    let text: String
    let device: String
    let route: String
    let model: String?
    let status: String
    let steps: [TaskStep]
    let reply: String
    let cost: Double
    let first_ms: Double?
    let total_ms: Double?
}

/// One server-sent event from the hub. Only the fields a type uses are set.
struct HubEvent: Decodable {
    let type: String
    let state: String?
    let base: String?
    let hold: Double?
    let role: String?
    let text: String?
    let isFinal: Bool?
    let name: String?
    let ok: Bool?
    let id: String?
    let summary: String?
    let afterUntrusted: Bool?
    let kind: String?  // "moment": glance | hop
    let run: String?   // "flow_run": the id the result is posted back under
    let on: Bool?      // "flow_watch": start (true) or stop watching
    let steps: [FlowStep]?  // "flow_run"
    let apps: [String]?     // "open_apps": bundle ids (a routine)
    let sid: Int?           // "suggestion": its id
    let alive: Bool?        // "voice": the voice helper answers the hub

    enum CodingKeys: String, CodingKey {
        case type, state, base, hold, role, text, name, ok, id, summary, kind, run, on, steps, apps, sid, alive
        case isFinal = "final"
        case afterUntrusted = "after_untrusted"
    }
}

/// GET /api/brief: "Good morning" before noon, "Today so far" after; one section per source. A source that
/// is not connected says so in `missing`, with the fix in `action` (connect_google | link_jobhunt | open_connections).
struct BriefInfo: Decodable, Equatable {
    struct Section: Decodable, Equatable, Identifiable {
        let name: String
        let lines: [String]
        let missing: String
        let action: String
        var id: String { name }
    }
    let title: String
    let date: String
    let text: String
    let sections: [Section]

    var isEmpty: Bool { text.isEmpty && sections.isEmpty }

    /// The word on the fix button after "Gmail not connected · ".
    static func actionWord(_ action: String) -> String? {
        switch action {
        case "connect_google": "Connect"
        case "link_jobhunt": "Link"
        case "open_connections": "Check"
        default: nil
        }
    }
}
