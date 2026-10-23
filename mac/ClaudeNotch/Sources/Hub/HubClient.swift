import AppKit
import Foundation
import Observation

/// Talks to the Python hub on 127.0.0.1 with the HUB_TOKEN from the Keychain.
/// Follows /api/events (SSE) for Maxie's state, the live transcript, steps and confirmations.
@MainActor
@Observable
final class HubClient {
    private(set) var connected = false
    private(set) var state: BotState = .idle
    private(set) var transcript: [TranscriptLine] = []
    private(set) var steps: [Step] = []
    var confirmations: [Confirmation] = []  // cards waiting for the user (and screen tasks set to run when they are away)
    private(set) var brief: BriefInfo?
    private(set) var budgetLine = ""
    private(set) var modelLines: [String] = []
    private(set) var backupLine = ""          // "Last backup: Oct 15, 3:00 AM ✓"
    private(set) var backupAppLines: [String] = []  // "jobhunt ✓ Oct 15, 3:00 AM"
    private(set) var backupStale = false      // red badge: older than 2 days or failed
    private(set) var voiceMuted = false
    var paused = false         // Pause Maxie (HubClient+Pause): not listening, watching or working
    var notice: VoiceNotice?   // a line under Maxie for a few seconds ("Didn't catch that")
    var voiceAlive = true      // the voice helper answers the hub (its 0.5 s poll is the heartbeat)
    @ObservationIgnored var noticeTask: Task<Void, Never>?
    @ObservationIgnored var onVoiceDown: (@MainActor () -> Void)?  // the app restarts the helper
    @ObservationIgnored var onVoiceUp: (@MainActor () -> Void)?    // it answers again: the restarts' backoff starts over
    var maxie: MaxieInfo?                 // Settings › Maxie (HubClient+Maxie: syncMaxie / putMaxie)
    @ObservationIgnored var maxieSink: (([String: Any]) -> Void)?  // tests only: where putMaxie's changes go
    private(set) var sending = false      // one message in flight: never dropped, never doubled
    private(set) var queued = 0           // waiting behind it
    private(set) var lastError = ""
    private(set) var tasks: [TaskInfo] = []        // Activity: newest first
    private(set) var currentTask: TaskInfo? = nil  // the one running (or paused)
    var watching = false                 // watch me once: recording the user's clicks and typing
    var pendingFlow: [FlowStep]? = nil   // what was watched, waiting for a name
    var eyesOpen = true                  // watch and learn (HubClient+Watch): Maxie is watching
    var watchExcluded: [String] = []     // apps the user never wants watched
    var suggestion: WatchSuggestion?     // "…want me to do it?", waiting for a yes or no
    var backgroundJobs: [BackgroundJob] = []  // HubClient+Background: tasks Maxie runs on his own
    var scheduled: [ScheduledItem] = []        // what runs on its own: Activity › Scheduled
    @ObservationIgnored var onFlowWatch: ((Bool) -> Void)?
    @ObservationIgnored var onFlowRun: ((String, String, [FlowStep]) -> Void)?
    @ObservationIgnored var onToggleWatch: (() -> Void)?
    @ObservationIgnored var onConnect: (@MainActor () -> Void)?  // PresenceReporter: a restarted hub hears at once

    struct Outgoing: Sendable {
        let text: String
        let images: [String]   // data URLs
        let files: [String]    // paths
    }
    private var outbox: [Outgoing] = []

    let base: URL
    private let token: String?
    private var stream: Task<Void, Never>?
    private var holdTask: Task<Void, Never>?

    init(base: URL = URL(string: "http://127.0.0.1:8780")!, token: String? = Keychain.read("HUB_TOKEN")) {
        // Snapshots and UI tests point the app at the fake hub (make fake-hub), never at the live one.
        let env = ProcessInfo.processInfo.environment
        let snapshot = env["CLAUDE_NOTCH_SNAPSHOT"] != nil
        self.base = snapshot ? (env["CLAUDE_NOTCH_HUB"].flatMap(URL.init(string:)) ?? base) : base
        self.token = snapshot ? (env["CLAUDE_NOTCH_TOKEN"] ?? token) : token
    }

    func start() {
        guard stream == nil else { return }
        stream = Task { [weak self] in await self?.follow() }
    }

    // MARK: - SSE

    private func follow() async {
        var wait: UInt64 = 1
        while !Task.isCancelled {
            do {
                let (bytes, response) = try await URLSession.shared.bytes(for: request("/api/events"))
                guard (response as? HTTPURLResponse)?.statusCode == 200 else { throw URLError(.userAuthenticationRequired) }
                connected = true
                wait = 1
                onConnect?()
                Task { await refreshPanel(); await refreshVoice() }
                for try await line in bytes.lines where line.hasPrefix("data: ") {
                    let data = Data(line.dropFirst(6).utf8)
                    if line.hasPrefix("data: {\"type\": \"task\""), let t = try? JSONDecoder().decode(TaskInfo.self, from: data) {
                        applyTask(t)
                    } else if let ev = try? JSONDecoder().decode(HubEvent.self, from: data) {
                        apply(ev)
                    }
                }
            } catch {
                // Hub down, Mac waking up, or a bad token: show "offline" and retry with backoff.
            }
            connected = false
            if wait == Self.hubDownAfter { show(VoiceNotice(kind: "hub_down", text: VoiceNotice.hubDown)) }  // ~7 s down, once
            try? await Task.sleep(for: .seconds(Double(wait)))
            wait = min(wait * 2, 30)
        }
    }

    /// States that are a feeling (good news, proud, a hard day): the trackpad gives one soft tap.
    /// No sound: nothing should play after a reply ends.
    static let feelings: Set<BotState> = [.happy, .proud, .gentle]
    static let hubDownAfter: UInt64 = 4  // the third failed reconnect in a row (1 + 2 + 4 s)
    static let hopSeconds = 2.0
    static let glanceSeconds = 1.0

    static func tap() {
        NSHapticFeedbackManager.defaultPerformer.perform(.levelChange, performanceTime: .now)
    }

    func apply(_ ev: HubEvent) {  // internal: tests feed it events
        switch ev.type {
        case "state":
            guard let s = ev.state.flatMap(BotState.init(rawValue:)) else { return }
            noteEyes(base: ev.base)
            holdTask?.cancel()
            if Self.feelings.contains(s), s != state { Self.tap() }
            state = s
            if s == .think { steps = [] }
            if let hold = ev.hold, hold > 0, let back = ev.base.flatMap(BotState.init(rawValue:)) {
                holdTask = Task { [weak self] in
                    try? await Task.sleep(for: .seconds(hold))
                    if !Task.isCancelled { self?.state = back }
                }
            }
        case "transcript":
            let role = ev.role ?? "assistant"
            if let last = transcript.last, last.role == role, !last.done {
                transcript[transcript.count - 1].text += ev.text ?? ""
                transcript[transcript.count - 1].done = ev.isFinal ?? false
            } else if !(ev.text ?? "").isEmpty {
                transcript.append(TranscriptLine(role: role, text: ev.text ?? "", done: ev.isFinal ?? false))
            }
            transcript = Array(transcript.suffix(30))
        case "moment":
            // A rare idle moment (maxie.mood): a short hop of joy, or a quick glance.
            guard state == .idle else { return }
            holdTask?.cancel()
            state = .happy
            let seconds = ev.kind == "hop" ? Self.hopSeconds : Self.glanceSeconds
            holdTask = Task { [weak self] in
                try? await Task.sleep(for: .seconds(seconds))
                if !Task.isCancelled, self?.state == .happy { self?.state = .idle }
            }
        case "suggestion":
            if let id = ev.sid, let text = ev.text { suggestion = WatchSuggestion(id: id, text: text) }
        case "open_apps":
            RoutineOpener.open(ev.apps ?? [])  // a routine the user said yes to: in the background, no focus taken
        case "flow_watch":
            onFlowWatch?(ev.on ?? true)
        case "flow_run":
            if let run = ev.run, let steps = ev.steps { onFlowRun?(run, ev.name ?? "the flow", steps) }
        case "step":
            steps.append(Step(name: ev.name ?? "step", ok: ev.ok ?? true))
        case "confirm":
            if let id = ev.id, !confirmations.contains(where: { $0.id == id }) {
                confirmations.append(Confirmation(id: id, summary: ev.summary ?? "", afterUntrusted: ev.afterUntrusted ?? false))
                Task { await loadConfirmations() }  // the full card: is it a screen ask?
            }
        case "background":
            Task { await loadBackground() }
        case "voice_notice":
            if let kind = ev.kind, let text = ev.text { show(VoiceNotice(kind: kind, text: text)) }
        case "voice":
            setVoiceAlive(ev.alive ?? true)
        case "paused":
            paused = ev.on ?? false
            Task { await syncMuted() }  // pausing mutes him; resuming puts back what he had
        default:
            break
        }
    }

    func applyTask(_ t: TaskInfo) {
        if let i = tasks.firstIndex(where: { $0.id == t.id }) { tasks[i] = t } else { tasks.insert(t, at: 0) }
        tasks = Array(tasks.prefix(50))
        guard t.device != "background" else { return }  // the user's own task keeps the running slot (Pause / Cancel)
        currentTask = (t.status == "running" || t.status == "paused") ? t : (currentTask?.id == t.id ? nil : currentTask)
    }

    // MARK: - Actions

    struct TasksPage: Decodable { let current: TaskInfo?; let tasks: [TaskInfo]; let paused: Bool }

    func loadTasks() async {
        if let p: TasksPage = try? await get("/api/tasks") {
            tasks = p.tasks
            currentTask = p.current
        }
    }

    /// Activity: pause / resume / cancel the running task.
    func control(task id: Int, _ verb: String) {
        Task {
            _ = await postJSON("/api/tasks/\(id)/\(verb)", dict: [:])
            await loadTasks()
        }
    }

    /// Snapshots only (CLAUDE_NOTCH_DEMO=1): a sample conversation shown in this process, never sent anywhere.
    func showDemoTranscript() {
        transcript = [
            TranscriptLine(role: "user", text: "what's on my calendar today", done: true),
            TranscriptLine(role: "assistant", text: "10 AM design review, 6 PM gym. Want me to move the gym?", done: true),
            TranscriptLine(role: "user", text: "yes, push it to 7", done: true),
            TranscriptLine(role: "assistant", text: "Done. Gym is at 7 PM, and I left a note on the 6 PM slot in case you change your mind.", done: true),
        ]
    }

    func refreshPanel() async {
        struct Budget: Decodable { let line: String }
        struct Models: Decodable { let lines: [String]; let price_guessed: [String: Bool]; let active: [String: String] }
        if let b: BriefInfo = try? await get("/api/brief") { brief = b }
        if let b: Budget = try? await get("/api/budget") { budgetLine = b.line }
        await syncMuted()
        await syncPause()
        await syncWatch()
        struct BackupInfo: Decodable { let line: String; let app_lines: [String]; let stale: Bool; let last_ok: Bool; let warnings: [String] }
        if let b: BackupInfo = try? await get("/api/backup") {
            backupLine = b.line
            backupAppLines = b.app_lines + b.warnings
            backupStale = b.stale || !b.last_ok
        }
        if let m: Models = try? await get("/api/models") {
            let guessed = m.price_guessed.filter(\.value).keys.sorted().compactMap { m.active[$0] }
            modelLines = m.lines + (guessed.isEmpty ? [] : ["New model, price guessed: " + guessed.joined(separator: ", ")])
        }
        await loadConfirmations()
        await loadBackground()
        await syncMaxie()
    }

    func answer(_ c: Confirmation, approve: Bool) {
        confirmations.removeAll { $0.id == c.id }
        Task { _ = try? await postJSON("/api/confirm/\(c.id)", body: ["approve": approve]) }
    }

    /// More › Clear chat: the panel starts fresh (the hub keeps its history for search and memory).
    func clearChat() {
        transcript = []
        steps = []
    }

    /// "jobhunt not linked · Link" (after the user's OK): the hub copies the jobhunt and resumegen token files set in its Settings into the Keychain.
    func linkJobhunt() async -> String {
        struct Linked: Decodable { let linked: Bool?; let line: String?; let detail: String? }
        let (code, body) = await postJSON("/api/connections/jobhunt/link", dict: [:])
        let r = try? JSONDecoder().decode(Linked.self, from: Data(body.utf8))
        await refreshPanel()
        if code == 200 { return r?.line ?? "jobhunt linked." }
        return r?.detail ?? "Couldn't link jobhunt."
    }

    func stop() { Task { _ = try? await postJSON("/api/stop", body: [String: String]()) } }

    // MARK: - Sending

    /// Queue a message. They go one at a time, in order; a network failure keeps it and retries.
    func send(text: String, images: [String] = [], files: [String] = []) {
        let t = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !t.isEmpty || !images.isEmpty || !files.isEmpty else { return }
        outbox.append(Outgoing(text: t.isEmpty ? "What's this?" : t, images: images, files: files))
        queued = outbox.count
        pump()
    }

    private func pump() {
        guard !sending, !outbox.isEmpty else { return }
        let next = outbox.removeFirst()
        queued = outbox.count
        sending = true
        Task {
            await deliver(next)
            sending = false
            pump()
        }
    }

    private func deliver(_ o: Outgoing) async {
        struct Body: Encodable { let text: String; let conversation: String; let device: String; let images: [String]; let files: [String] }
        var r = request("/api/chat", method: "POST")
        r.timeoutInterval = 300
        do {
            r.httpBody = try JSONEncoder().encode(Body(text: o.text, conversation: "mac", device: "mac", images: o.images, files: o.files))
            let (bytes, response) = try await URLSession.shared.bytes(for: r)
            let code = (response as? HTTPURLResponse)?.statusCode ?? 0
            guard code == 200 else {
                lastError = code == 401 ? "The hub token in the Keychain doesn't match. Get a new one in the hub's web Settings (Settings, Access, Connect the Mac app)." : "The hub answered \(code)."
                return
            }
            for try await _ in bytes.lines {}  // the words arrive on /api/events as they stream
            lastError = ""
        } catch {
            lastError = "Couldn't reach the hub; keeping \"\(o.text.prefix(30))…\" to retry."
            outbox.insert(o, at: 0)
            queued = outbox.count
            try? await Task.sleep(for: .seconds(3))
        }
    }

    /// The panel's mic button: the voice helper chimes and listens, as after "Hey Max".
    func wakeVoice() { Task { _ = try? await postJSON("/api/voice/wake", body: [String: String]()) } }

    /// Screenshot of the display under the mouse, sent with the question (Sonnet with vision).
    func lookAtScreen(question: String) {
        Task {
            do {
                let image = try await ScreenCapture.grab()
                send(text: question, images: [image])
            } catch {
                lastError = error.localizedDescription
            }
        }
    }

    // MARK: - Watch me once

    /// A line from the Mac app itself in the conversation (not sent to Maxie).
    func note(_ text: String) {
        transcript.append(TranscriptLine(role: "assistant", text: text, done: true))
        transcript = Array(transcript.suffix(30))
    }

    /// Saves what was watched under the name the user gave it. nil when saved, else why not (from the hub).
    func saveFlow(name: String) async -> String? {
        guard let steps = pendingFlow else { return nil }
        let (code, body) = await postJSON("/api/flows", dict: ["name": name, "steps": steps.map(\.json)])
        guard code == 200 else {
            struct Detail: Decodable { let detail: String }
            return (try? JSONDecoder().decode(Detail.self, from: Data(body.utf8)))?.detail ?? "The hub didn't take it (\(code))."
        }
        pendingFlow = nil
        note("Saved. Say \"replay the \(name) flow\" when you want it again.")
        return nil
    }

    // MARK: - For the Settings window

    func fetch<T: Decodable>(_ path: String) async -> T? { try? await get(path) }

    @discardableResult
    func postJSON(_ path: String, dict: [String: Any]) async -> (Int, String) {
        var r = request(path, method: "POST")
        r.httpBody = try? JSONSerialization.data(withJSONObject: dict)
        return await run(r)
    }

    @discardableResult
    func put(_ path: String, dict: [String: Any]) async -> (Int, String) {
        var r = request(path, method: "PUT")
        r.httpBody = try? JSONSerialization.data(withJSONObject: dict)
        return await run(r)
    }

    /// POST JSON, raw bytes back (voice samples).
    func postData(_ path: String, dict: [String: Any]) async -> (Int, Data) {
        var r = request(path, method: "POST")
        r.httpBody = try? JSONSerialization.data(withJSONObject: dict)
        r.timeoutInterval = 30  // the first sample loads Kokoro
        do {
            let (data, response) = try await URLSession.shared.data(for: r)
            return ((response as? HTTPURLResponse)?.statusCode ?? 0, data)
        } catch {
            return (0, Data())
        }
    }

    @discardableResult
    func delete(_ path: String) async -> (Int, String) {
        await run(request(path, method: "DELETE"))
    }

    @discardableResult
    func putSettings(_ changes: [String: Any]) async -> (Int, String) {
        var r = request("/api/settings", method: "PUT")
        r.httpBody = try? JSONSerialization.data(withJSONObject: changes)
        let out = await run(r)
        await refreshPanel()
        return out
    }

    private func run(_ r: URLRequest) async -> (Int, String) {
        do {
            let (data, response) = try await URLSession.shared.data(for: r)
            return ((response as? HTTPURLResponse)?.statusCode ?? 0, String(data: data, encoding: .utf8) ?? "")
        } catch {
            return (0, error.localizedDescription)
        }
    }

    func syncMuted() async {
        struct SettingsInfo: Decodable { let voice_muted: Bool }
        if let st: SettingsInfo = try? await get("/api/settings") { voiceMuted = st.voice_muted }
    }

    func setMuted(_ muted: Bool) {
        voiceMuted = muted
        Task { _ = try? await put("/api/settings", body: ["voice_muted": muted]) }
    }

    /// The same, waited for (Quit: the voice helper must be muted before the app goes).
    func setMutedNow(_ muted: Bool) async {
        voiceMuted = muted
        _ = try? await put("/api/settings", body: ["voice_muted": muted])
    }

    func post(state: BotState) {
        self.state = state
        Task { _ = try? await postJSON("/api/events/state", body: ["state": state.rawValue]) }
    }

    // MARK: - HTTP

    /// base + path, keeping a query ("/api/backup?verify=true") a query: appending(path:) alone would encode the
    /// "?" as %3F and the hub would answer 404.
    static func url(_ base: URL, _ path: String) -> URL {
        let parts = path.split(separator: "?", maxSplits: 1).map(String.init)
        let url = base.appending(path: parts.first ?? "")
        guard parts.count == 2, var c = URLComponents(url: url, resolvingAgainstBaseURL: false) else { return url }
        c.percentEncodedQuery = parts[1]
        return c.url ?? url
    }

    /// A page of the hub's web app, its #fragment kept a fragment: ("/settings.html", "google") is
    /// base/settings.html#google.
    static func webURL(_ base: URL, page: String, fragment: String? = nil) -> URL {
        let url = base.appending(path: page)
        guard let fragment, var c = URLComponents(url: url, resolvingAgainstBaseURL: false) else { return url }
        c.fragment = fragment
        return c.url ?? url
    }

    /// Opens the hub's web Settings in the default browser, on one part of it ("google", "access").
    func openWebSettings(_ fragment: String? = nil) {
        NSWorkspace.shared.open(Self.webURL(base, page: "/settings.html", fragment: fragment))
    }

    private func request(_ path: String, method: String = "GET") -> URLRequest {
        var r = URLRequest(url: Self.url(base, path))
        r.httpMethod = method
        r.timeoutInterval = path == "/api/events" ? 60 : 10  // the hub sends a keep-alive every 15 s
        if let token { r.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
        r.setValue("application/json", forHTTPHeaderField: "Content-Type")
        return r
    }

    private func get<T: Decodable>(_ path: String) async throws -> T {
        let (data, _) = try await URLSession.shared.data(for: request(path))
        return try JSONDecoder().decode(T.self, from: data)
    }

    private func postJSON(_ path: String, body: some Encodable) async throws -> Data {
        var r = request(path, method: "POST")
        r.httpBody = try JSONEncoder().encode(body)
        return try await URLSession.shared.data(for: r).0
    }

    private func put(_ path: String, body: some Encodable) async throws -> Data {
        var r = request(path, method: "PUT")
        r.httpBody = try JSONEncoder().encode(body)
        return try await URLSession.shared.data(for: r).0
    }
}
