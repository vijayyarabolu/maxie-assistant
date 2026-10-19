import AppKit
import XCTest
@testable import ClaudeNotch

/// The chat box.
final class ComposerTests: XCTestCase {
    /// The mic's wave sat before "Ask or just talk" all the time; at rest it was a row of grey dots.
    func testTheMicWaveShowsOnlyWhileHeListens() {
        XCTAssertTrue(ComposerView.showsWave(.listen))
        for state in BotState.allCases where state != .listen {
            XCTAssertFalse(ComposerView.showsWave(state), "\(state)")
        }
    }

    /// "Look at my screen" (⌥⇧S) was a dashed square; it's an eye, like the watching eye by his name.
    func testLookAtMyScreenIsAnEye() {
        XCTAssertEqual(ComposerView.lookSymbol, "eye")
    }

    /// "+" opens a picker for any number of files; folders aren't attachments.
    @MainActor
    func testThePlusButtonPicksFiles() {
        let picker = ComposerView.picker()
        XCTAssertTrue(picker.canChooseFiles)
        XCTAssertFalse(picker.canChooseDirectories)
        XCTAssertTrue(picker.allowsMultipleSelection)
        XCTAssertEqual(picker.prompt, "Attach")
    }

    /// The picker, a paste and a drop all attach the same way: an image goes as a picture, anything else by path.
    func testAnImageGoesAsAPictureAndAnythingElseByPath() throws {
        let dir = FileManager.default.temporaryDirectory.appending(path: "attach-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: dir) }
        let png = dir.appending(path: "shot.png")
        let image = NSImage(size: NSSize(width: 4, height: 4), flipped: false) { r in NSColor.red.setFill(); r.fill(); return true }
        let rep = try XCTUnwrap(NSBitmapImageRep(data: try XCTUnwrap(image.tiffRepresentation)))
        try XCTUnwrap(rep.representation(using: .png, properties: [:])).write(to: png)
        let pdf = dir.appending(path: "resume.pdf")
        try Data("%PDF-1.4".utf8).write(to: pdf)

        let picture = ComposerView.attachment(for: png)
        XCTAssertEqual(picture.name, "shot.png")
        XCTAssertTrue(picture.image?.hasPrefix("data:image/jpeg;base64,") ?? false)
        XCTAssertNil(picture.path)
        let file = ComposerView.attachment(for: pdf)
        XCTAssertEqual(file.name, "resume.pdf")
        XCTAssertEqual(file.path, pdf.path)
        XCTAssertNil(file.image)
    }
}
