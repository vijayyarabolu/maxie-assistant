import Contacts
import EventKit
import Foundation
import FoundationModels

/// What the bridge reads on this Mac, each behind its own macOS permission. Every call reports a missing
/// permission as a reason the hub turns into one sentence ("turn on Calendars for Maxie…").
enum AppleError: Error { case denied(String), failed(String) }

enum CalendarStore {
    nonisolated(unsafe) private static let store = EKEventStore()

    static func events(days: Int, now: Date = Date()) throws(AppleError) -> [[String: String]] {
        guard EKEventStore.authorizationStatus(for: .event) == .fullAccess else { throw .denied("calendars") }
        let start = Calendar.current.startOfDay(for: now)
        let end = Calendar.current.date(byAdding: .day, value: max(1, min(days, 31)), to: start) ?? now
        let pred = store.predicateForEvents(withStart: start, end: end, calendars: nil)
        return store.events(matching: pred).prefix(60).map { e in
            ["title": e.title ?? "", "start": e.isAllDay ? "all day" : e.startDate.ISO8601Format(),
             "end": e.endDate.ISO8601Format(), "location": e.location ?? "", "calendar": e.calendar.title]
        }
    }

    static func reminders() async throws(AppleError) -> [[String: String]] {
        guard EKEventStore.authorizationStatus(for: .reminder) == .fullAccess else { throw .denied("reminders") }
        let pred = store.predicateForIncompleteReminders(withDueDateStarting: nil, ending: nil, calendars: nil)
        let items: [[String: String]] = await withCheckedContinuation { done in
            store.fetchReminders(matching: pred) { list in
                done.resume(returning: (list ?? []).prefix(60).map { r in
                    ["title": r.title ?? "", "due": r.dueDateComponents?.date.map { $0.ISO8601Format() } ?? ""]
                })
            }
        }
        return items
    }
}

enum ContactsStore {
    struct Person: Sendable { let name: String; let phones: [String]; let emails: [String] }

    static func find(_ query: String) throws(AppleError) -> [Person] {
        guard CNContactStore.authorizationStatus(for: .contacts) == .authorized else { throw .denied("contacts") }
        let keys = [CNContactGivenNameKey, CNContactFamilyNameKey, CNContactNicknameKey,
                    CNContactPhoneNumbersKey, CNContactEmailAddressesKey] as [CNKeyDescriptor]
        let found = (try? CNContactStore().unifiedContacts(matching: CNContact.predicateForContacts(matchingName: query), keysToFetch: keys)) ?? []
        return found.prefix(10).map { c in
            Person(name: [c.givenName, c.familyName].filter { !$0.isEmpty }.joined(separator: " ").ifEmpty(c.nickname),
                   phones: c.phoneNumbers.map(\.value.stringValue), emails: c.emailAddresses.map { $0.value as String })
        }
    }
}

/// Notes and Mail through AppleScript run by `osascript` from Maxie (so the Automation grant is Maxie's).
/// The scripts never `activate`: a quit app is opened hidden first, and the app stays behind the user's work.
enum ScriptStore {
    /// The app each script works in, as `tell application "…"` names it.
    static let apps = ["Notes": "com.apple.Notes", "Mail": "com.apple.mail"]

    /// Runs `script` in `app`, opening it hidden first; the outcome is remembered as its Automation answer.
    static func run(_ script: String, in app: String, timeout: TimeInterval = 20) throws(AppleError) -> String {
        guard let id = apps[app] else { return try run(script, timeout: timeout) }
        HiddenLaunch.openAndWait(id)
        do throws(AppleError) {
            let out = try run(script, timeout: timeout)
            AutomationMemory().record(.allowed, for: id)
            return out
        } catch {
            if let grant = grant(after: error) { AutomationMemory().record(grant, for: id) }
            throw error
        }
    }

    /// What a script's outcome says about the grant: ran = allowed, -1743 = denied, anything else = nothing.
    static func grant(after error: AppleError?) -> Grant? {
        switch error {
        case nil: .allowed
        case .denied: .denied
        case .failed: nil
        }
    }

    static func run(_ script: String, timeout: TimeInterval = 20) throws(AppleError) -> String {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")
        p.arguments = ["-e", script]
        let out = Pipe(), err = Pipe()
        p.standardOutput = out
        p.standardError = err
        do { try p.run() } catch { throw .failed("osascript") }
        let deadline = Date().addingTimeInterval(timeout)
        while p.isRunning, Date() < deadline { Thread.sleep(forTimeInterval: 0.05) }
        if p.isRunning { p.terminate(); throw .failed("timeout") }
        let text = String(data: out.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
        let problem = String(data: err.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
        if p.terminationStatus != 0 {
            throw problem.contains("-1743") || problem.contains("Not authorized") ? .denied("automation") : .failed(problem.prefix(200).description)
        }
        return text.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    /// An AppleScript string literal.
    static func quote(_ s: String) -> String {
        "\"" + s.replacingOccurrences(of: "\\", with: "\\\\").replacingOccurrences(of: "\"", with: "\\\"") + "\""
    }

    static let fieldSep = "\u{1F}", rowSep = "\u{1E}"

    static func rows(_ text: String, _ names: [String]) -> [[String: String]] {
        text.components(separatedBy: rowSep).filter { !$0.isEmpty }.map { row in
            Dictionary(uniqueKeysWithValues: zip(names, row.components(separatedBy: fieldSep)))
        }
    }

    static func notes(_ q: String) throws(AppleError) -> [[String: String]] {
        let script = """
            set out to ""
            tell application "Notes"
                repeat with n in (notes whose name contains \(quote(q)) or plaintext contains \(quote(q)))
                    set p to plaintext of n
                    if length of p > 240 then set p to text 1 thru 240 of p
                    set out to out & name of n & (ASCII character 31) & p & (ASCII character 30)
                end repeat
            end tell
            return out
            """
        return Array(rows(try run(script, in: "Notes"), ["title", "snippet"]).prefix(20))
    }

    static func mail(_ q: String) throws(AppleError) -> [[String: String]] {
        let script = """
            set out to ""
            tell application "Mail"
                set found to (messages of inbox whose subject contains \(quote(q)) or sender contains \(quote(q)))
                set n to count of found
                if n > 20 then set n to 20
                repeat with i from 1 to n
                    set m to item i of found
                    set out to out & (sender of m) & (ASCII character 31) & (subject of m) & (ASCII character 31) & ((date received of m) as string) & (ASCII character 30)
                end repeat
            end tell
            return out
            """
        return rows(try run(script, in: "Mail", timeout: 30), ["from", "subject", "date"])
    }

    /// A draft only: closed with saving, never sent (sending is a separate, confirmed action).
    static func draft(to: String, subject: String, body: String) throws(AppleError) -> String {
        let script = """
            tell application "Mail"
                set m to make new outgoing message with properties {subject:\(quote(subject)), content:\(quote(body)), visible:false}
                tell m to make new to recipient at end of to recipients with properties {address:\(quote(to))}
                close m saving yes
            end tell
            """
        _ = try run(script, in: "Mail")
        return "Draft saved in Mail to \(to)."
    }
}

enum SpotlightStore {
    static func files(_ q: String) -> [[String: String]] {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/mdfind")
        p.arguments = ["-onlyin", FileManager.default.homeDirectoryForCurrentUser.path, q]
        let out = Pipe()
        p.standardOutput = out
        p.standardError = FileHandle.nullDevice
        guard (try? p.run()) != nil else { return [] }
        let text = String(data: out.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
        p.waitUntilExit()
        return text.split(separator: "\n").prefix(20).map { path in
            ["name": (path as NSString).lastPathComponent, "path": String(path)]
        }
    }
}

/// Apple's on-device model: free and private. Used for summaries, rewriting and short replies, where it is
/// good; never for routing, where it was slow and often wrong.
enum OnDeviceModel {
    static let tasks = [
        "summarize": "Summarize the text in at most three short sentences. Plain words, no lists.",
        "rewrite": "Rewrite the text so it is clear, friendly and short. Keep the meaning. Return only the new text.",
        "reply": "Write a short, warm reply to this message, in the first person. Return only the reply.",
        "extract": "List only the dates, times, places, people and amounts that appear in the text, one per line, like \"Date: Thursday the 22nd\". Skip any kind that isn't there. Nothing else.",
    ]
    static let maxChars = 9_000   // ~3k tokens: the 4k context minus room for the answer
    static let maxHowChars = 60   // "so it sounds friendlier"

    static func respond(task: String, text: String, how: String = "") async throws(AppleError) -> String {
        guard let base = tasks[task] else { throw .failed("unknown task") }
        let extra = how.trimmingCharacters(in: .whitespacesAndNewlines).prefix(maxHowChars)
        let instructions = extra.isEmpty ? base : base + " He asked for it this way: \(extra)."
        guard #available(macOS 26.0, *), case .available = SystemLanguageModel.default.availability else {
            throw .denied("fm_unavailable")
        }
        let session = LanguageModelSession(instructions: instructions)
        do {
            return try await session.respond(to: String(text.prefix(maxChars))).content
                .trimmingCharacters(in: .whitespacesAndNewlines)
        } catch {
            throw .failed("on-device model: \(error.localizedDescription)")
        }
    }
}

private extension String {
    func ifEmpty(_ other: String) -> String { isEmpty ? other : self }
}
