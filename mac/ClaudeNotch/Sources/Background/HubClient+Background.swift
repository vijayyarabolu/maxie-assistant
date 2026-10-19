import Foundation

/// A task Maxie runs on his own (maxie.background), as /api/background sends it.
struct BackgroundJob: Decodable, Identifiable, Equatable {
    let id: Int
    let ts: Double
    let text: String
    let source: String
    let status: String
    let reply: String

    static func label(_ status: String) -> String {
        switch status {
        case "queued": "Waiting its turn"
        case "running": "Working on it"
        case "done": "Done"
        case "waiting": "Needs your OK"
        case "error": "Couldn't finish"
        case "cancelled": "Cancelled"
        default: "Stopped"
        }
    }

    var statusLine: String { Self.label(status) }
    var isOpen: Bool { ["queued", "running", "waiting"].contains(status) }
}

/// One line of Activity › Scheduled: what runs on its own, and whether it only reads.
struct ScheduledItem: Decodable, Identifiable, Equatable {
    let name: String
    let when: String
    let what: String
    let reads_only: Bool
    var id: String { "\(name)|\(what)" }
}

/// The words on a "Can I use the screen?" card, from the hub's summary
/// ("Use the screen for ~20 s to replay Expense report").
enum ScreenAsk {
    /// (seconds, what) from "Use the screen for ~20 s to replay Expense report".
    private static func parts(_ summary: String) -> (String, String)? {
        guard let m = summary.wholeMatch(of: /Use the screen for ~(\d+) s to (.+)/) else { return nil }
        return (String(m.1), String(m.2))
    }

    static func title(_ c: Confirmation) -> String {
        if c.status == "away" { return "When you step away" }
        guard let (seconds, _) = parts(c.summary) else { return "Can I use the screen?" }
        return "Can I use the screen for ~\(seconds) s?"
    }

    static func detail(_ c: Confirmation) -> String {
        guard let (_, what) = parts(c.summary) else { return c.summary + "." }
        if c.status == "away" { return "I'll \(what) once you've been away 10 minutes." }
        return "To \(what)."
    }
}

extension HubClient {
    func loadConfirmations() async {
        if let c: [Confirmation] = await fetch("/api/confirmations") { confirmations = c }
    }

    func loadBackground() async {
        if let jobs: [BackgroundJob] = await fetch("/api/background?limit=20") { backgroundJobs = jobs }
        if let items: [ScheduledItem] = await fetch("/api/scheduled") { scheduled = items }
    }

    func cancelBackground(_ id: Int) {
        backgroundJobs = backgroundJobs.map { $0.id == id && $0.status == "queued"
            ? BackgroundJob(id: $0.id, ts: $0.ts, text: $0.text, source: $0.source, status: "cancelled", reply: $0.reply) : $0 }
        Task { _ = await delete("/api/background/\(id)"); await loadBackground() }
    }

    /// "When I'm away" on a screen card: the card stays, saying it waits for the user to step away.
    func answerLater(_ c: Confirmation) {
        confirmations = confirmations.map { $0.id == c.id
            ? Confirmation(id: $0.id, summary: $0.summary, afterUntrusted: $0.afterUntrusted, status: "away", screen: true) : $0 }
        Task { _ = await postJSON("/api/confirm/\(c.id)/away", dict: [:]) }
    }
}
