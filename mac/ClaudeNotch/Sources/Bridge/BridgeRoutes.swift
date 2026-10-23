import Foundation

/// The bridge's routes (the hub's client is maxie/hands/apple.py). Reads only, except a Mail draft; and
/// one screenshot when the user says "Maxie, look".
enum BridgeRoutes {
    static let maxDays = 3650

    @Sendable static func handle(_ r: BridgeRequest) async -> BridgeResponse {
        do {
            switch (r.method, r.path) {
            case ("GET", "/health"):
                return .json(["ok": true])
            case ("GET", "/messages"):
                return .json(["messages": try messages(person: r.query["person"] ?? "", query: r.query["q"] ?? "",
                                                         days: min(Int(r.query["days"] ?? "") ?? 365, maxDays))])
            case ("GET", "/contacts"):
                let people = try ContactsStore.find(r.query["q"] ?? "")
                return .json(["contacts": people.map { ["name": $0.name, "phones": $0.phones.joined(separator: ", "),
                                                        "emails": $0.emails.joined(separator: ", ")] }])
            case ("GET", "/calendar"):
                return .json(["events": try CalendarStore.events(days: Int(r.query["days"] ?? "") ?? 1),
                              "calendars": CalendarStore.calendars()])
            case ("GET", "/reminders"):
                return .json(["reminders": try await CalendarStore.reminders()])
            case ("GET", "/notes"):
                return .json(["notes": try ScriptStore.notes(r.query["q"] ?? "")])
            case ("GET", "/mail"):
                return .json(["mail": try ScriptStore.mail(r.query["q"] ?? "")])
            case ("POST", "/mail/draft"):
                let b = r.json()
                guard let to = b["to"] as? String, to.contains("@") else { return .error("bad_request", status: 400) }
                return .json(["result": try ScriptStore.draft(to: to, subject: b["subject"] as? String ?? "",
                                                              body: b["body"] as? String ?? "")])
            case ("GET", "/screen"):
                return await screen()
            case ("GET", "/files"):
                return .json(["files": SpotlightStore.files(r.query["q"] ?? "")])
            case ("POST", "/fm"):
                let b = r.json()
                return .json(["text": try await OnDeviceModel.respond(task: b["task"] as? String ?? "", text: b["text"] as? String ?? "",
                                                                         how: b["how"] as? String ?? "")])
            default:
                return .error("not_found", status: 404)
            }
        } catch let AppleError.denied(reason) {
            return .error(reason, status: reason == "fm_unavailable" ? 503 : 403)
        } catch let AppleError.failed(why) {
            Log.d("bridge \(r.path): \(why)")
            return .error("failed", status: 500)
        } catch MessagesStore.Failure.noAccess {
            return .error("full_disk_access")
        } catch {
            return .error("failed", status: 500)
        }
    }

    /// "Maxie, look" / "take a screenshot": one screenshot as a JPEG data URL. Maxie.app takes it,
    /// since it holds Screen Recording; on the main actor, as it asks AppKit where the pointer is.
    static func screen(grab: () async throws -> String = { try await Task { @MainActor in try await ScreenCapture.grab() }.value }) async -> BridgeResponse {
        do {
            return .json(["image": try await grab()])
        } catch ScreenCapture.Failure.noPermission {
            return .error("screen")
        } catch {
            Log.d("bridge /screen: \(error)")
            return .error("failed", status: 500)
        }
    }

    /// One person's recent messages: their handles come from Contacts (or the phone/email the user said).
    static func messages(person: String, query: String, days: Int,
                         store: MessagesStore = MessagesStore(),
                         lookup: (String) throws(AppleError) -> [ContactsStore.Person] = { q throws(AppleError) in try ContactsStore.find(q) }) throws -> [[String: Any]] {
        let trimmed = person.trimmingCharacters(in: .whitespaces)
        guard !trimmed.isEmpty else { return [] }
        var names: [String: String] = [:]  // handle key -> display name
        if trimmed.contains("@") || trimmed.filter(\.isNumber).count >= 7 {
            names[MessagesStore.key(trimmed)] = trimmed
        } else {
            for p in try lookup(trimmed) {
                for h in p.phones + p.emails { names[MessagesStore.key(h)] = p.name }
            }
        }
        guard !names.isEmpty else { return [] }
        let found = try store.messages(from: Array(names.keys), days: days)
        let q = query.lowercased()
        return found.filter { q.isEmpty || $0.text.lowercased().contains(q) }.map { m in
            ["text": m.text, "date": m.date, "from_me": m.fromMe, "sender": names[MessagesStore.key(m.handle)] ?? m.handle]
        }
    }
}
