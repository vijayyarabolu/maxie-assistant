import SwiftUI
import XCTest
@testable import ClaudeNotch

/// Buttons must fire on one normal click anywhere on their visible shape, including the padding and
/// right at the edge (Settings › Voice › Test needed a "hard press"). Clicks are real
/// NSEvents sent to a real window, in-process, so no Accessibility permission is needed.
@MainActor
final class HitTests: XCTestCase {
    final class Taps { var count = 0 }

    /// Hosts `content` at the window's top-left, measures the button, clicks at `spot(frame)`.
    /// `inactive`: another window is key when the click arrives (the notch panel usually is).
    private func clicks(_ content: (Taps) -> some View, inactive: Bool = false, host makeHost: ((AnyView) -> NSView)? = nil,
                        at spot: (CGRect) -> CGPoint) -> Int {
        let taps = Taps()
        let size = CGSize(width: 300, height: 160)
        let root = AnyView(content(taps).fixedSize().frame(width: size.width, height: size.height, alignment: .topLeading))
        let host = makeHost?(root) ?? NSHostingView(rootView: root)
        let w = NSWindow(contentRect: NSRect(origin: .zero, size: size), styleMask: [.titled], backing: .buffered, defer: false)
        w.contentView = host
        w.makeKeyAndOrderFront(nil)
        host.layoutSubtreeIfNeeded()
        let other = NSWindow(contentRect: NSRect(x: 400, y: 0, width: 100, height: 100), styleMask: [.titled], backing: .buffered, defer: false)
        if inactive {
            other.makeKeyAndOrderFront(nil)
        }
        RunLoop.main.run(until: Date().addingTimeInterval(0.15))
        defer { other.orderOut(nil) }
        let frame = CGRect(origin: .zero, size: ButtonProbe.measured)
        let p = spot(frame)
        let loc = NSPoint(x: p.x, y: size.height - p.y)  // window coordinates start bottom-left
        for type in [NSEvent.EventType.leftMouseDown, .leftMouseUp] {
            let e = NSEvent.mouseEvent(with: type, location: loc, modifierFlags: [], timestamp: ProcessInfo.processInfo.systemUptime,
                                       windowNumber: w.windowNumber, context: nil, eventNumber: 0, clickCount: 1, pressure: 1)!
            w.sendEvent(e)
            RunLoop.main.run(until: Date().addingTimeInterval(0.05))
        }
        RunLoop.main.run(until: Date().addingTimeInterval(0.1))
        w.orderOut(nil)
        return taps.count
    }

    private func sunsetButton(_ taps: Taps) -> some View {
        Button { taps.count += 1 } label: { Label("Test", systemImage: "play.fill") }
            .buttonStyle(.sunset)
            .background(GeometryReader { g in Color.clear.onAppear { ButtonProbe.measured = g.size } })
    }

    func testSunsetButtonFiresInTheMiddle() {
        XCTAssertEqual(clicks(sunsetButton) { CGPoint(x: $0.midX, y: $0.midY) }, 1)
    }

    func testSunsetButtonFiresInItsPadding() {
        XCTAssertEqual(clicks(sunsetButton) { CGPoint(x: 4, y: $0.midY) }, 1)  // left padding, no text there
        XCTAssertEqual(clicks(sunsetButton) { CGPoint(x: $0.midX, y: 2) }, 1)  // top padding
    }

    func testAClickOutsideTheButtonDoesNothing() {  // proves the clicks above really land where aimed
        XCTAssertEqual(clicks(sunsetButton) { CGPoint(x: $0.maxX + 8, y: $0.midY) }, 0)
    }

    func testFirstClickOnAnInactiveSettingsWindowFires() {
        XCTAssertEqual(clicks(sunsetButton, inactive: true, host: { FirstClickHostingView(rootView: $0) }) {
            CGPoint(x: $0.midX, y: $0.midY)
        }, 1)
    }

    func testIconButtonsFireAroundTheGlyphNotOnlyOnItsStrokes() {
        let icon = { (taps: Taps) in
            Button { taps.count += 1 } label: { Image(systemName: "pencil") }
                .buttonStyle(.sunsetIcon)
                .background(GeometryReader { g in Color.clear.onAppear { ButtonProbe.measured = g.size } })
        }
        XCTAssertGreaterThanOrEqual(ButtonProbe.measuredAfter(icon).width, 28)
        XCTAssertEqual(clicks(icon) { _ in CGPoint(x: 2, y: 2) }, 1)  // a corner, far from the pencil's strokes
    }

    func testAWordButtonFiresAnywhereOnItsWord() {
        let word = { (taps: Taps) in
            Button("Allow") { taps.count += 1 }.buttonStyle(.wordPrimary)
                .background(GeometryReader { g in Color.clear.onAppear { ButtonProbe.measured = g.size } })
        }
        XCTAssertEqual(clicks(word) { CGPoint(x: 2, y: $0.midY) }, 1)  // the margin left of the word
        XCTAssertEqual(clicks(word) { CGPoint(x: $0.maxX - 2, y: $0.maxY - 2) }, 1)
    }

    func testAWordChoiceSelectsWithOneClick() {
        final class Box { var value = "allow" }
        let box = Box()
        let choice = { (_: Taps) in
            WordChoice(options: [("Allow", "allow"), ("Ask me", "ask"), ("Block", "block")],
                       selection: Binding(get: { box.value }, set: { box.value = $0 }))
                .background(GeometryReader { g in Color.clear.onAppear { ButtonProbe.measured = g.size } })
        }
        _ = clicks(choice) { CGPoint(x: $0.maxX - 8, y: $0.midY) }  // the last word
        XCTAssertEqual(box.value, "block")
    }

    func testCopyConversationIsPlainTextWithWhoSaidWhat() {
        let lines = [TranscriptLine(role: "user", text: "what's on my calendar", done: true),
                     TranscriptLine(role: "assistant", text: "10 AM review.", done: true)]
        XCTAssertEqual(PanelView.conversationText(lines), "You: what's on my calendar\n\nMaxie: 10 AM review.")
    }

    func testSunsetButtonFiresAtTheVeryEdge() {
        XCTAssertEqual(clicks(sunsetButton) { CGPoint(x: $0.maxX - 1, y: $0.midY) }, 1)
        XCTAssertEqual(clicks(sunsetButton) { CGPoint(x: $0.midX, y: $0.maxY - 1) }, 1)
    }
}

@MainActor
enum ButtonProbe {
    static var measured: CGSize = .zero

    /// Lays `content` out once and returns the size its probe reported.
    static func measuredAfter(_ content: (HitTests.Taps) -> some View) -> CGSize {
        let host = NSHostingView(rootView: content(HitTests.Taps()).fixedSize())
        host.frame = NSRect(x: 0, y: 0, width: 200, height: 100)
        host.layoutSubtreeIfNeeded()
        RunLoop.main.run(until: Date().addingTimeInterval(0.1))
        return measured
    }
}
