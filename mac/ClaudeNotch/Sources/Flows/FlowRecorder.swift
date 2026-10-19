import AppKit

/// Watches the user do something once: clicks on named controls, typing, and a few keys, in other apps.
/// Never records a password field (and macOS hides typing into one anyway), never records the Maxie app itself.
/// Events are handled one at a time, in order; a hit-test the other app is too busy to answer is retried.
@MainActor
final class FlowRecorder {
    private enum Event {
        case click(CGPoint)
        case key(code: Int, flags: NSEvent.ModifierFlags, chars: String, typed: String)
    }

    static let retries = 4
    static let retryPause: Duration = .milliseconds(150)

    private(set) var steps: [FlowStep] = []
    private(set) var skippedSecret = false
    private(set) var unnamedClicks = 0
    private var monitors: [Any] = []
    private var lastBundle = ""
    private var typing = ""
    private var inbox: [Event] = []
    private var draining: Task<Void, Never>?

    var isRecording: Bool { !monitors.isEmpty }

    /// False when Accessibility is off (macOS shows its prompt).
    func start() -> Bool {
        guard AXNode.trusted(prompt: true) else { return false }
        AXUIElementSetMessagingTimeout(AXUIElementCreateSystemWide(), 1.0)
        steps = []; typing = ""; lastBundle = ""; skippedSecret = false; unnamedClicks = 0; inbox = []
        if let front = NSWorkspace.shared.frontmostApplication, front.processIdentifier != getpid() {
            noteApp(front)
        }
        monitors = [
            NSEvent.addGlobalMonitorForEvents(matching: .leftMouseDown) { [weak self] _ in
                let p = NSEvent.mouseLocation
                MainActor.assumeIsolated { self?.receive(.click(p)) }
            } as Any,
            NSEvent.addGlobalMonitorForEvents(matching: .keyDown) { [weak self] e in
                let ev = Event.key(code: Int(e.keyCode), flags: e.modifierFlags, chars: e.charactersIgnoringModifiers ?? "",
                                   typed: e.characters ?? "")
                MainActor.assumeIsolated { self?.receive(ev) }
            } as Any,
        ]
        return true
    }

    /// Stops watching and returns the steps once every event seen so far is handled.
    func stop() async -> [FlowStep] {
        monitors.forEach(NSEvent.removeMonitor)
        monitors = []
        await draining?.value
        flushTyping()
        return steps
    }

    private func receive(_ ev: Event) {
        inbox.append(ev)
        guard draining == nil else { return }
        draining = Task { [weak self] in
            while let self, !self.inbox.isEmpty {
                let next = self.inbox.removeFirst()
                switch next {
                case .click(let p): await self.click(at: p)
                case .key(let code, let flags, let chars, let typed): self.key(code: code, flags: flags, chars: chars, typed: typed)
                }
            }
            self?.draining = nil
        }
    }

    private func noteApp(_ app: NSRunningApplication) {
        let bundle = app.bundleIdentifier ?? ""
        guard !bundle.isEmpty, bundle != lastBundle else { return }
        lastBundle = bundle
        steps.append(.openApp(bundle: bundle, name: app.localizedName ?? bundle))
    }

    private func hit(_ point: CGPoint) async -> AXNode? {
        for attempt in 0..<Self.retries {
            if let node = AXNode.at(appKitPoint: point)?.target, !node.role.isEmpty { return node }
            if attempt < Self.retries - 1 { try? await Task.sleep(for: Self.retryPause) }
        }
        return nil
    }

    private func click(at point: CGPoint) async {
        flushTyping()
        guard let hit = await hit(point) else {
            unnamedClicks += 1
            return
        }
        guard hit.pid != getpid(), let app = NSRunningApplication(processIdentifier: hit.pid) else { return }
        noteApp(app)
        var s = FlowStep(kind: .click)
        s.app = app.bundleIdentifier ?? ""
        s.window = hit.window?.title ?? ""
        s.role = hit.role; s.title = hit.title; s.label = hit.label; s.id = hit.identifier
        guard !(s.title.isEmpty && s.label.isEmpty && s.id.isEmpty) else {
            unnamedClicks += 1  // a click on nothing with a name (empty space, a canvas) can't be found again
            return
        }
        steps.append(s)
    }

    private func key(code: Int, flags: NSEvent.ModifierFlags, chars: String, typed: String) {
        guard NSWorkspace.shared.frontmostApplication?.processIdentifier != getpid() else { return }
        if let front = NSWorkspace.shared.frontmostApplication { noteApp(front) }
        if AXNode.focused()?.isSecure == true {
            skippedSecret = true
            return
        }
        var mods: [String] = []
        if flags.contains(.command) { mods.append("cmd") }
        if flags.contains(.shift) { mods.append("shift") }
        if flags.contains(.option) { mods.append("option") }
        if flags.contains(.control) { mods.append("control") }
        let name = FlowKeys.names[code] ?? chars.lowercased()
        let chord = mods.contains("cmd") || mods.contains("control") || mods.contains("option")
        if chord {
            guard FlowKeys.codes[name] != nil else { return }
            flushTyping()
            steps.append(.press(name, mods: mods))
        } else if FlowKeys.special.contains(name) {
            flushTyping()
            steps.append(.press(name, mods: mods.filter { $0 == "shift" }))
        } else if name == "delete" {
            if typing.isEmpty { steps.append(.press("delete")) } else { typing.removeLast() }
        } else {
            typing += typed
        }
    }

    private func flushTyping() {
        guard !typing.isEmpty else { return }
        steps.append(.typing(typing))
        typing = ""
    }
}
