import AppKit
import SwiftUI
import XCTest
@testable import ClaudeNotch

/// Hosts a SwiftUI view in a real window parked off every screen (alpha 0, never key unless asked, never seen),
/// finds controls through the accessibility tree and clicks them with synthetic mouse-down/up pairs sent through
/// `window.sendEvent`, the way AppKit delivers a real click.
@MainActor
final class ClickHarness {
    let window: NSWindow
    let host: NSView
    private var eventNumber = 0

    /// `window` builds the window around the host (default: a plain LightWindow); tests of Settings pass
    /// SettingsWindow.makeWindow so they click exactly the window the user gets.
    init(_ view: some View, size: CGSize, origin: CGPoint = CGPoint(x: -40_000, y: -40_000), key: Bool = false,
         window make: ((NSView) -> NSWindow)? = nil) {
        ClickTargets.isRecording = true
        ClickTargets.reset()
        let h = FirstClickHostingView(rootView: AnyView(view))
        h.frame = NSRect(origin: .zero, size: size)
        let w = make?(h) ?? {
            let w = LightWindow(contentRect: NSRect(origin: .zero, size: size), styleMask: [.borderless], backing: .buffered, defer: false)
            w.contentView = h
            return w
        }()
        w.alphaValue = 0
        w.isReleasedWhenClosed = false
        w.setFrameOrigin(origin)
        if key { w.makeKey() }
        w.orderBack(nil)
        window = w
        host = h
        settle(0.4)
    }

    func close() { window.orderOut(nil); window.close() }

    func settle(_ seconds: Double = 0.15) {
        RunLoop.main.run(until: Date().addingTimeInterval(seconds))
        host.layoutSubtreeIfNeeded()
    }

    /// Every accessibility element under the host, depth first.
    func elements() -> [NSAccessibilityElementProtocol] {
        var out: [NSAccessibilityElementProtocol] = []
        func walk(_ e: Any) {
            guard let el = e as? NSObject & NSAccessibilityElementProtocol else { return }
            out.append(el)
            for c in (el.value(forKey: "accessibilityChildren") as? [Any]) ?? [] { walk(c) }
        }
        for c in host.accessibilityChildren() ?? [] { walk(c) }
        return out
    }

    func label(_ e: NSAccessibilityElementProtocol) -> String {
        let o = e as? NSObject
        return (o?.value(forKey: "accessibilityLabel") as? String) ?? (o?.value(forKey: "accessibilityTitle") as? String) ?? ""
    }

    /// The centre of the element labelled `name` (first match), in window coordinates.
    func center(of name: String, role: NSAccessibility.Role? = nil) -> CGPoint? {
        let match = elements().first { e in
            label(e) == name && (role == nil || (e as? NSObject)?.value(forKey: "accessibilityRole") as? String == role?.rawValue)
        }
        guard let frame = (match as? NSObject)?.value(forKey: "accessibilityFrame") as? NSRect else { return nil }
        let screen = CGPoint(x: frame.midX, y: frame.midY)
        return window.convertPoint(fromScreen: screen)
    }

    /// The centre of a control that reported itself with `.clickTarget(id)`, in window coordinates (y up).
    func center(target id: String) -> CGPoint? {
        guard let f = ClickTargets.frames[id] else { return nil }
        let y = host.isFlipped ? f.midY : host.bounds.height - f.midY  // SwiftUI .global is top-left based
        return host.convert(CGPoint(x: f.midX, y: y), to: nil)
    }

    @discardableResult
    func click(target id: String, file: StaticString = #filePath, line: UInt = #line) -> Bool {
        guard let p = center(target: id) else {
            XCTFail("no target \(id); have: \(ClickTargets.frames.keys.sorted())", file: file, line: line)
            return false
        }
        click(at: p)
        return true
    }

    /// One click (down + up) at a window point.
    func click(at p: CGPoint) {
        press(at: p)
        settle()
    }

    /// Down + up and nothing else, so a test can time what happens next.
    func press(at p: CGPoint) {
        for type in [NSEvent.EventType.leftMouseDown, .leftMouseUp] {
            eventNumber += 1
            let e = NSEvent.mouseEvent(with: type, location: p, modifierFlags: [], timestamp: ProcessInfo.processInfo.systemUptime,
                                       windowNumber: window.windowNumber, context: nil, eventNumber: eventNumber,
                                       clickCount: 1, pressure: type == .leftMouseDown ? 1 : 0)!
            window.sendEvent(e)
        }
    }

    @discardableResult
    func click(_ name: String, role: NSAccessibility.Role? = nil, file: StaticString = #filePath, line: UInt = #line) -> Bool {
        guard let p = center(of: name, role: role) else {
            XCTFail("no control labelled \(name); have: \(elements().map(label).filter { !$0.isEmpty }.prefix(40))", file: file, line: line)
            return false
        }
        click(at: p)
        return true
    }
}
