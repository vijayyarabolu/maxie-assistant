import AppIntents
import Foundation

/// Siri, Spotlight, Shortcuts and the Action Button reach Maxie. Each intent sends one command to the
/// hub, the same as typing it in the panel, and speaks back the final answer. Apple doesn't let an app drive the
/// Siri assistant itself; this is the legit reach.
enum IntentHub {
    static let base = URL(string: "http://127.0.0.1:8780")!
    static let timeout: TimeInterval = 60

    enum Failure: Error, CustomLocalizedStringResourceConvertible {
        case noToken, hubDown, badStatus(Int)

        var localizedStringResource: LocalizedStringResource {
            switch self {
            case .noToken: "Maxie isn't set up on this Mac yet. Open Maxie once."
            case .hubDown: "Maxie's hub isn't running. Open Maxie and try again."
            case .badStatus(let code): "Maxie's hub answered \(code)."
            }
        }
    }

    /// One command in, Maxie's final words out.
    static func ask(_ text: String, token: String? = Keychain.read("HUB_TOKEN")) async throws -> String {
        guard let token, !token.isEmpty else { throw Failure.noToken }
        var r = URLRequest(url: base.appending(path: "api/chat"))
        r.httpMethod = "POST"
        r.timeoutInterval = timeout
        r.setValue("application/json", forHTTPHeaderField: "Content-Type")
        r.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        r.httpBody = try JSONSerialization.data(withJSONObject: ["text": text, "conversation": "siri", "device": "mac"])
        let data: Data, response: URLResponse
        do { (data, response) = try await URLSession.shared.data(for: r) } catch { throw Failure.hubDown }
        let code = (response as? HTTPURLResponse)?.statusCode ?? 0
        guard code == 200 else { throw Failure.badStatus(code) }
        return reply(fromNDJSON: String(decoding: data, as: UTF8.self)) ?? "Done."
    }

    /// The hub streams one JSON event per line; the answer is the `done` event's text (or the streamed words).
    static func reply(fromNDJSON body: String) -> String? {
        var streamed = ""
        for line in body.split(separator: "\n") {
            guard let ev = try? JSONSerialization.jsonObject(with: Data(line.utf8)) as? [String: Any] else { continue }
            switch ev["type"] as? String {
            case "done":
                if let t = (ev["text"] as? String)?.trimmingCharacters(in: .whitespacesAndNewlines), !t.isEmpty { return t }
            case "text":
                streamed += ev["text"] as? String ?? ""
            default:
                continue
            }
        }
        let t = streamed.trimmingCharacters(in: .whitespacesAndNewlines)
        return t.isEmpty ? nil : t
    }
}

struct AskMaxieIntent: AppIntent {
    static let title: LocalizedStringResource = "Ask Maxie"
    static let description = IntentDescription("Ask Maxie anything. Maxie answers out loud and in the notch.")
    @Parameter(title: "Question", requestValueDialog: "What should I ask Maxie?") var question: String

    func perform() async throws -> some IntentResult & ReturnsValue<String> & ProvidesDialog {
        let reply = try await IntentHub.ask(question)
        return .result(value: reply, dialog: "\(reply)")
    }
}

struct WhatsNextIntent: AppIntent {
    static let title: LocalizedStringResource = "What's Next"
    static let description = IntentDescription("Your next event and anything due today.")

    func perform() async throws -> some IntentResult & ReturnsValue<String> & ProvidesDialog {
        let reply = try await IntentHub.ask("what's next on my calendar today")
        return .result(value: reply, dialog: "\(reply)")
    }
}

struct CheckMessagesIntent: AppIntent {
    static let title: LocalizedStringResource = "Check Messages"
    static let description = IntentDescription("What someone texted you lately, read on your Mac.")
    @Parameter(title: "From", requestValueDialog: "Whose messages?") var person: String

    func perform() async throws -> some IntentResult & ReturnsValue<String> & ProvidesDialog {
        let reply = try await IntentHub.ask("what did \(person) text me lately")
        return .result(value: reply, dialog: "\(reply)")
    }
}

struct AddReminderIntent: AppIntent {
    static let title: LocalizedStringResource = "Add a Reminder"
    static let description = IntentDescription("Maxie adds a reminder for you.")
    @Parameter(title: "Reminder", requestValueDialog: "What should I remind you about?") var text: String

    func perform() async throws -> some IntentResult & ReturnsValue<String> & ProvidesDialog {
        let reply = try await IntentHub.ask("remind me to \(text)")
        return .result(value: reply, dialog: "\(reply)")
    }
}

struct MaxieShortcuts: AppShortcutsProvider {
    static var appShortcuts: [AppShortcut] {
        AppShortcut(intent: AskMaxieIntent(), phrases: ["Ask \(.applicationName)", "Ask \(.applicationName) something"],
                    shortTitle: "Ask Maxie", systemImageName: "bubble.left.and.text.bubble.right")
        AppShortcut(intent: WhatsNextIntent(), phrases: ["\(.applicationName), what's next", "What's next in \(.applicationName)"],
                    shortTitle: "What's Next", systemImageName: "calendar")
        AppShortcut(intent: CheckMessagesIntent(), phrases: ["\(.applicationName), check messages", "Check messages with \(.applicationName)"],
                    shortTitle: "Check Messages", systemImageName: "message")
        AppShortcut(intent: AddReminderIntent(), phrases: ["\(.applicationName), add a reminder", "Add a reminder in \(.applicationName)"],
                    shortTitle: "Add a Reminder", systemImageName: "checklist")
    }
}
