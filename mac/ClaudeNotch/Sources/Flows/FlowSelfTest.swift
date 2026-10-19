import AppKit

/// `CLAUDE_NOTCH_FLOW_TEST=1`: replays a harmless flow for real (Calculator: clear, 7 × 6 =) through the same
/// player a hub replay uses, prints the outcome and Calculator's display, quits Calculator if it wasn't open,
/// and exits. Only presses buttons through Accessibility: no typing, no cursor moves, no clicks anywhere else.
@MainActor
enum FlowSelfTest {
    static func runIfAsked() {
        guard ProcessInfo.processInfo.environment["CLAUDE_NOTCH_FLOW_TEST"] != nil else { return }
        guard ScreenGate.realScreenOK() else {  // it clicks in Calculator on the real screen: only when the user is away
            Log.d("flow self-test skipped: \(ScreenGate.why())")
            exit(3)
        }
        let bundle = "com.apple.calculator"
        let wasOpen = !NSRunningApplication.runningApplications(withBundleIdentifier: bundle).isEmpty
        func press(_ label: String) -> FlowStep {
            var s = FlowStep(kind: .click)
            s.app = bundle; s.role = "AXButton"; s.label = label
            return s
        }
        let steps: [FlowStep] = [.openApp(bundle: bundle, name: "Calculator"), press("Clear"), press("7"),
                                 press("Multiply"), press("6"), press("Equals")]
        Task { @MainActor in
            let out = await FlowPlayer().play(steps)
            let pid = NSRunningApplication.runningApplications(withBundleIdentifier: bundle).first?.processIdentifier
            let shown = pid.map { display(AXNode.app($0)) } ?? ""
            print("flow-test ok=\(out.ok) done=\(out.done)/\(steps.count) error=\(out.error) display=\(shown)")
            if !wasOpen { NSRunningApplication.runningApplications(withBundleIdentifier: bundle).first?.terminate() }
            exit(out.ok ? 0 : 1)
        }
    }

    /// Calculator's result: the static texts it shows, joined.
    private static func display(_ root: AXNode) -> String {
        var queue = [root], found: [String] = [], seen = 0
        while !queue.isEmpty, seen < 2000 {
            let n = queue.removeFirst()
            seen += 1
            if n.role == "AXStaticText", !n.value.isEmpty { found.append(n.value) }
            queue.append(contentsOf: n.children)
        }
        return found.joined(separator: " | ")
    }
}
