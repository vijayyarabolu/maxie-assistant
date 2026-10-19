import AppKit
import ApplicationServices

/// Watch and learn, on the Mac side. When the user switches apps, and every 30 s while they stay in
/// one, Maxie notes the app and, through Accessibility, the front window's title (WatchRules decides what may
/// leave). Once a minute the notes go to the hub on this Mac, which checks them again and keeps them 30 days.
/// Nothing while his eyes are closed, while the screen is locked or asleep, or while a password field has focus.
/// No keystrokes, no screenshots, no page contents.
@MainActor
final class Observer {
    static let shared = Observer()
    static let titleEvery: TimeInterval = 30
    static let flushEvery: TimeInterval = 60
    static let maxBuffer = 400

    private weak var hub: HubClient?
    private(set) var buffer: [[String: Any]] = []
    private var lastTitle = ""
    private var paused = false  // locked or displays asleep
    private var timers: [Timer] = []
    private var observers: [NSObjectProtocol] = []
    private var categories: [String: String] = [:]

    var watching: Bool { hub?.eyesOpen == true && !paused }

    func attach(_ hub: HubClient) {
        self.hub = hub
        guard timers.isEmpty else { return }
        let ws = NSWorkspace.shared.notificationCenter, dist = DistributedNotificationCenter.default()
        let pause: (Bool) -> (Notification) -> Void = { [weak self] on in
            { _ in MainActor.assumeIsolated { self?.paused = on } }
        }
        observers = [
            ws.addObserver(forName: NSWorkspace.didActivateApplicationNotification, object: nil, queue: .main) { [weak self] n in
                let app = n.userInfo?[NSWorkspace.applicationUserInfoKey] as? NSRunningApplication
                MainActor.assumeIsolated { if let app { self?.note(app, switched: true) } }
            },
            ws.addObserver(forName: NSWorkspace.screensDidSleepNotification, object: nil, queue: .main, using: pause(true)),
            ws.addObserver(forName: NSWorkspace.screensDidWakeNotification, object: nil, queue: .main, using: pause(false)),
            dist.addObserver(forName: .init("com.apple.screenIsLocked"), object: nil, queue: .main, using: pause(true)),
            dist.addObserver(forName: .init("com.apple.screenIsUnlocked"), object: nil, queue: .main, using: pause(false)),
        ]
        timers = [repeating(Self.titleEvery) { $0.titleTick() }, repeating(Self.flushEvery) { $0.flush() }]
    }

    private func repeating(_ every: TimeInterval, _ body: @escaping @MainActor (Observer) -> Void) -> Timer {
        let t = Timer(timeInterval: every, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { if let self { body(self) } }
        }
        t.tolerance = every / 3  // low power: macOS may batch it with other wake-ups
        RunLoop.main.add(t, forMode: .common)
        return t
    }

    func note(_ app: NSRunningApplication, switched: Bool) {
        guard watching, let bundle = app.bundleIdentifier, bundle != Bundle.main.bundleIdentifier else { return }
        let (title, secure) = Self.frontWindow(app.processIdentifier)
        if !switched, title == lastTitle { return }
        lastTitle = title
        let items = WatchRules.items(bundle: bundle, name: app.localizedName ?? bundle, category: category(of: app),
                                     title: title, secure: secure, switched: switched, excluded: hub?.watchExcluded ?? [])
        buffer = Array((buffer + items).suffix(Self.maxBuffer))
    }

    private func titleTick() {
        if let app = NSWorkspace.shared.frontmostApplication { note(app, switched: false) }
    }

    func flush() {
        guard let hub, !buffer.isEmpty else { return }
        let batch = buffer
        buffer = []
        guard hub.eyesOpen else { return }  // closed meanwhile: what was noted is dropped, not sent
        Task { await hub.postJSON("/api/watch/observe", dict: ["items": batch]) }
    }

    /// The app's own category (Info.plist LSApplicationCategoryType): finance and health apps are never watched.
    private func category(of app: NSRunningApplication) -> String? {
        guard let id = app.bundleIdentifier else { return nil }
        if let known = categories[id] { return known.isEmpty ? nil : known }
        let found = app.bundleURL.flatMap { Bundle(url: $0)?.infoDictionary?["LSApplicationCategoryType"] as? String } ?? ""
        categories[id] = found
        return found.isEmpty ? nil : found
    }

    /// The front window's title, and whether a password field has focus. Without Accessibility: no title.
    static func frontWindow(_ pid: pid_t) -> (title: String, secure: Bool) {
        guard AXIsProcessTrusted() else { return ("", false) }
        let app = AXUIElementCreateApplication(pid)
        var focused: CFTypeRef?
        var secure = false
        if AXUIElementCopyAttributeValue(app, kAXFocusedUIElementAttribute as CFString, &focused) == .success,
           let element = focused, CFGetTypeID(element) == AXUIElementGetTypeID() {
            var subrole: CFTypeRef?
            AXUIElementCopyAttributeValue(element as! AXUIElement, kAXSubroleAttribute as CFString, &subrole)  // swiftlint:disable:this force_cast
            secure = (subrole as? String) == kAXSecureTextFieldSubrole
        }
        var window: CFTypeRef?
        guard AXUIElementCopyAttributeValue(app, kAXFocusedWindowAttribute as CFString, &window) == .success,
              let win = window, CFGetTypeID(win) == AXUIElementGetTypeID() else { return ("", secure) }
        var title: CFTypeRef?
        AXUIElementCopyAttributeValue(win as! AXUIElement, kAXTitleAttribute as CFString, &title)  // swiftlint:disable:this force_cast
        return ((title as? String) ?? "", secure)
    }
}
