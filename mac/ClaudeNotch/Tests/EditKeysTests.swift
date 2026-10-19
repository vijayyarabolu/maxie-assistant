import AppKit
import XCTest
@testable import ClaudeNotch

/// ⌘C (and ⌘X ⌘V ⌘A ⌘Z ⇧⌘Z) in the floating panel reach the selected text even while Maxie is not
/// the active app (the panel opens over the user's work without activating Maxie), without going through the menu bar.
@MainActor
final class EditKeysTests: XCTestCase {
    /// Stands in for the selected text: records the edit actions it gets, never touches the pasteboard.
    private final class Recorder: NSView {
        var got: [String] = []
        override var acceptsFirstResponder: Bool { true }
        @objc func copy(_ sender: Any?) { got.append("copy") }
        @objc func cut(_ sender: Any?) { got.append("cut") }
        @objc func paste(_ sender: Any?) { got.append("paste") }
        @objc override func selectAll(_ sender: Any?) { got.append("selectAll") }
        @objc func undo(_ sender: Any?) { got.append("undo") }
        @objc func redo(_ sender: Any?) { got.append("redo") }
    }

    private func key(_ chars: String, _ flags: NSEvent.ModifierFlags = .command) -> NSEvent {
        NSEvent.keyEvent(with: .keyDown, location: .zero, modifierFlags: flags, timestamp: 0, windowNumber: 0, context: nil,
                         characters: chars, charactersIgnoringModifiers: chars, isARepeat: false, keyCode: 0)!
    }

    func testEditKeysReachTheSelectedTextInThePanel() {
        let panel = KeyPanel(contentRect: NSRect(x: -40_000, y: -40_000, width: 100, height: 100),
                             styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
        let recorder = Recorder(frame: NSRect(x: 0, y: 0, width: 100, height: 100))
        panel.contentView = recorder
        XCTAssertTrue(panel.makeFirstResponder(recorder))
        for chars in ["c", "x", "v", "a", "z"] { XCTAssertTrue(panel.performKeyEquivalent(with: key(chars)), chars) }
        XCTAssertTrue(panel.performKeyEquivalent(with: key("Z", [.command, .shift])))
        XCTAssertEqual(recorder.got, ["copy", "cut", "paste", "selectAll", "undo", "redo"])
        panel.close()
    }

    func testOtherKeysAreLeftAlone() {
        XCTAssertNil(EditKeys.action(for: key("c", [])))          // typing a c
        XCTAssertNil(EditKeys.action(for: key("w")))              // ⌘W closes, elsewhere
        XCTAssertNil(EditKeys.action(for: key("c", [.command, .option])))
        XCTAssertEqual(EditKeys.action(for: key("c")), #selector(NSText.copy(_:)))
    }
}
