import AppKit
import IOKit.pwr_mgt

/// Maxie sleeps when the user sleeps. Once a minute, and at once when the screen locks or unlocks or the
/// displays sleep or wake, the notch app tells the hub on this Mac four things: screen locked, displays asleep,
/// seconds since the last keyboard or mouse input, and whether something is playing (an app holding
/// PreventUserIdleDisplaySleep, as video players do). Nothing else is read and nothing leaves this Mac.
@MainActor
final class PresenceReporter {
    static let shared = PresenceReporter()
    static let every: TimeInterval = 60

    struct Signals: Equatable {
        let locked: Bool
        let displaysAsleep: Bool
        let idleS: Double
        let media: Bool

        var body: [String: Any] { ["locked": locked, "displays_asleep": displaysAsleep, "idle_s": idleS.rounded(), "media": media] }
    }

    private var send: @MainActor (String, [String: Any]) async -> Void = { _, _ in }
    private var timer: Timer?
    private var displaysAsleep = false
    private var observers: [NSObjectProtocol] = []

    /// At launch: report to this hub from now on.
    func attach(_ hub: HubClient) {
        send = { path, body in _ = await hub.postJSON(path, dict: body) }
        // A restarted hub has no report yet and falls back to the clock: asleep at night while the user works.
        hub.onConnect = { [weak self] in self?.report() }
        guard timer == nil else { return }
        watch()
        let t = Timer(timeInterval: Self.every, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.report() }
        }
        t.tolerance = 10  // low power: let macOS batch it with other wake-ups
        RunLoop.main.add(t, forMode: .common)
        timer = t
        report()
    }

    /// The wake word or a click on Maxie: the user is up, whatever the last report said.
    func active(_ source: String) {
        Task { await send("/api/presence/active", ["source": source]) }
    }

    func report() {
        let s = Self.read(displaysAsleep: displaysAsleep)
        Task { await send("/api/presence", s.body) }
    }

    static func read(displaysAsleep: Bool) -> Signals {
        Signals(locked: screenLocked(), displaysAsleep: displaysAsleep || CGDisplayIsAsleep(CGMainDisplayID()) != 0,
                idleS: idleSeconds(), media: mediaPlaying())
    }

    static func screenLocked() -> Bool {
        let session = CGSessionCopyCurrentDictionary() as? [String: Any]
        return session?["CGSSessionScreenIsLocked"] as? Bool ?? false
    }

    /// Seconds since the last keyboard, mouse or trackpad event in this login session.
    static func idleSeconds() -> Double {
        guard let any = CGEventType(rawValue: ~0) else { return 0 }
        return CGEventSource.secondsSinceLastEventType(.combinedSessionState, eventType: any)
    }

    /// A video or call keeps the display awake: that's a movie, not sleep.
    static func mediaPlaying() -> Bool {
        var status: Unmanaged<CFDictionary>?
        guard IOPMCopyAssertionsStatus(&status) == kIOReturnSuccess,
              let dict = status?.takeRetainedValue() as? [String: Any] else { return false }
        return (dict[kIOPMAssertPreventUserIdleDisplaySleep] as? Int ?? 0) > 0
    }

    private func watch() {
        let ws = NSWorkspace.shared.notificationCenter
        let dist = DistributedNotificationCenter.default()
        let now: (Bool?) -> (Notification) -> Void = { [weak self] asleep in
            { _ in
                MainActor.assumeIsolated {
                    if let asleep { self?.displaysAsleep = asleep }
                    self?.report()
                }
            }
        }
        observers = [
            ws.addObserver(forName: NSWorkspace.screensDidSleepNotification, object: nil, queue: .main, using: now(true)),
            ws.addObserver(forName: NSWorkspace.screensDidWakeNotification, object: nil, queue: .main, using: now(false)),
            ws.addObserver(forName: NSWorkspace.didWakeNotification, object: nil, queue: .main, using: now(false)),
            dist.addObserver(forName: .init("com.apple.screenIsLocked"), object: nil, queue: .main, using: now(nil)),
            dist.addObserver(forName: .init("com.apple.screenIsUnlocked"), object: nil, queue: .main, using: now(nil)),
        ]
    }
}
