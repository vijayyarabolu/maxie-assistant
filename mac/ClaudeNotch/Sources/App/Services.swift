import Foundation
import Observation

/// Maxie's launchd services. Quit stops the hub, the voice helper and Laya until Maxie is opened again
/// (they are KeepAlive services: a plain app quit left them running and listening). Opening Maxie starts them.
enum Services {
    struct Service: Equatable, Sendable {
        let label: String
        let name: String
    }

    static let laya = "dev.maxie.laya"
    static let all = [Service(label: MacHelpers.voiceLabel, name: "voice helper"), Service(label: laya, name: "Laya"),
                      Service(label: MacHelpers.hubLabel, name: "hub")]
    static var domain: String { "gui/\(getuid())" }

    /// launchctl with these arguments → (exit status, output). Tests swap it: they never touch his services.
    nonisolated(unsafe) static var launchctl: @Sendable ([String]) -> (Int32, String) = run

    static func plist(_ label: String) -> URL {
        FileManager.default.homeDirectoryForCurrentUser.appending(path: "Library/LaunchAgents/\(label).plist")
    }

    /// Quit: stopped, and disabled so launchd doesn't bring them back at login before Maxie does.
    static func stopAll() {
        for s in all { stop(s.label) }
    }

    static func stop(_ label: String) {
        _ = launchctl(["disable", "\(domain)/\(label)"])
        _ = launchctl(["bootout", "\(domain)/\(label)"])
    }

    /// Launch: any service that isn't loaded (Quit stopped it, or a crash loop did) is enabled and started.
    static func startMissing(exists: (URL) -> Bool = { FileManager.default.fileExists(atPath: $0.path) }) {
        for s in all where status(s.label) == nil && exists(plist(s.label)) { start(s.label) }
    }

    static func start(_ label: String) {
        _ = launchctl(["enable", "\(domain)/\(label)"])
        _ = launchctl(["bootstrap", domain, plist(label).path])
    }

    struct Status: Equatable {
        let runs: Int
        /// The last exit was a crash: an exit code other than 0, or a signal other than launchd's own SIGTERM.
        let crashed: Bool
        let exit: String  // "exit 3", "signal 11", "" when it never exited or stopped cleanly
    }

    /// nil when launchd doesn't have the service loaded.
    static func status(_ label: String) -> Status? {
        parse(launchctl(["print", "\(domain)/\(label)"]).1)
    }

    /// The top-level fields of `launchctl print` (one tab in; nested sections have more).
    static func parse(_ text: String) -> Status? {
        var fields: [String: String] = [:]
        for line in text.split(separator: "\n") where line.hasPrefix("\t") && !line.hasPrefix("\t\t") {
            let parts = line.dropFirst().components(separatedBy: " = ")
            if parts.count == 2 { fields[parts[0]] = parts[1] }
        }
        guard let runs = fields["runs"].flatMap(Int.init) else { return nil }
        if let code = fields["last exit code"].flatMap(Int.init), code != 0 {
            return Status(runs: runs, crashed: true, exit: "exit \(code)")
        }
        if let signal = fields["last terminating signal"]?.split(separator: ":").last.flatMap({ Int($0.trimmingCharacters(in: .whitespaces)) }),
           signal != sigterm {
            return Status(runs: runs, crashed: true, exit: "signal \(signal)")
        }
        return Status(runs: runs, crashed: false, exit: "")
    }

    static let sigterm = 15

    private static func run(_ args: [String]) -> (Int32, String) {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/launchctl")
        p.arguments = args
        let out = Pipe()
        p.standardOutput = out
        p.standardError = FileHandle.nullDevice
        do { try p.run() } catch { return (-1, "") }
        let data = out.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        return (p.terminationStatus, String(data: data, encoding: .utf8) ?? "")
    }
}

/// Waits that double after each try, up to a cap, and start over after a quiet spell.
struct Backoff: Equatable {
    let first: TimeInterval
    let cap: TimeInterval
    private(set) var tries = 0

    init(first: TimeInterval, cap: TimeInterval) {
        self.first = first
        self.cap = cap
    }

    var wait: TimeInterval { min(cap, first * pow(2, Double(max(0, tries - 1)))) }

    func next() -> Backoff {
        var b = self
        b.tries += 1
        return b
    }

    func reset() -> Backoff { Backoff(first: first, cap: cap) }
}

/// launchd restarts a KeepAlive service 10 s after it dies, forever: a service that crashes on start
/// burns CPU all night and nobody sees it. Every minute this reads each service's restarts; `limit` crashes within
/// `window` stops it, says so in words, and tries it again later, waiting longer each time it crashes again.
@MainActor
@Observable
final class CrashWatch {
    static let every: TimeInterval = 60
    static let window: TimeInterval = 600
    static let limit = 3
    static let longestWait: TimeInterval = 4 * 3600

    struct Stopped: Equatable {
        let name: String
        let exit: String
        let until: Date
    }

    private(set) var stopped: [String: Stopped] = [:]  // label → why, and when it is tried again
    @ObservationIgnored var onNotice: (String) -> Void = { _ in }
    /// launchctl runs off the main thread; tests record instead.
    @ObservationIgnored var stopService: (String) -> Void = { label in
        Task.detached { _ = Services.launchctl(["bootout", "\(Services.domain)/\(label)"]) }
    }
    @ObservationIgnored var startService: (String) -> Void = { label in Task.detached { Services.start(label) } }
    @ObservationIgnored private var crashes: [String: [Date]] = [:]
    @ObservationIgnored private var lastRuns: [String: Int] = [:]
    @ObservationIgnored private var backoff: [String: Backoff] = [:]
    @ObservationIgnored private var restartedAt: [String: Date] = [:]
    @ObservationIgnored private var timer: Timer?

    /// Words for the menu: nil while everything runs.
    var line: String? {
        stopped.values.min(by: { $0.until < $1.until }).map { Self.words($0) }
    }

    static func words(_ s: Stopped, now: Date = Date()) -> String {
        let minutes = max(1, Int((s.until.timeIntervalSince(now) / 60).rounded()))
        let why = s.exit.isEmpty ? "" : " (\(s.exit))"
        return "The \(s.name) kept crashing\(why), so I stopped it. Trying again in \(minutes) min. Logs: Settings › General."
    }

    func start() {
        timer = Timer.scheduledTimer(withTimeInterval: Self.every, repeats: true) { [weak self] _ in
            Task { @MainActor in await self?.check() }
        }
        timer?.tolerance = Self.every / 4
    }

    /// Is this service stopped on purpose? The voice heartbeat must not restart it behind the backoff's back.
    func isStopped(_ label: String) -> Bool { stopped[label] != nil }

    func check(now: Date = Date()) async {
        let statuses = await Task.detached { Services.all.map { ($0, Services.status($0.label)) } }.value
        for (service, status) in statuses { see(service, status, now: now) }
    }

    func see(_ service: Services.Service, _ status: Services.Status?, now: Date = Date()) {
        let label = service.label
        if let s = stopped[label] {
            guard now >= s.until else { return }
            stopped[label] = nil
            lastRuns[label] = nil
            crashes[label] = []
            restartedAt[label] = now
            startService(label)
            return
        }
        guard let status else { return }  // not loaded: Quit, or he stopped it himself
        let before = lastRuns[label]
        lastRuns[label] = status.runs
        var recent = (crashes[label] ?? []).filter { now.timeIntervalSince($0) < Self.window }
        if let before, status.runs > before, status.crashed {
            recent += Array(repeating: now, count: status.runs - before)
        } else if recent.isEmpty, let b = backoff[label], b.tries > 0,
                  now.timeIntervalSince(restartedAt[label] ?? .distantPast) >= Self.window {
            backoff[label] = b.reset()  // ran a whole window since: the next crash loop starts with a short wait again
        }
        crashes[label] = recent
        guard recent.count >= Self.limit else { return }
        let b = (backoff[label] ?? Backoff(first: Self.window, cap: Self.longestWait)).next()
        backoff[label] = b
        let s = Stopped(name: service.name, exit: status.exit, until: now.addingTimeInterval(b.wait))
        stopped[label] = s
        Log.d("\(label) crashed \(recent.count)× in \(Int(Self.window))s (\(status.exit)): stopped for \(Int(b.wait))s")
        stopService(label)
        onNotice(Self.words(s, now: now))
    }
}
