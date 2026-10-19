import AppKit
import SwiftUI
import XCTest
@testable import ClaudeNotch

/// Settings as a normal Mac window. Done on the title row in line with the cards; move, resize,
/// minimize and close like any window, the traffic lights showing only when the pointer comes near the top; on
/// the Maxie page the live preview stays pinned while the options scroll.
@MainActor
final class SettingsWindowTests: XCTestCase {
    private func hub() -> HubClient { HubClient(base: URL(string: "http://127.0.0.1:9")!, token: nil) }

    /// Another window holds key, as in ClickTests: without one, every harness here took ~40 s to settle.
    private var other: NSPanel?

    override func setUp() {
        let p = KeyPanel(contentRect: NSRect(x: -30_000, y: -30_000, width: 200, height: 200), styleMask: [.borderless], backing: .buffered, defer: false)
        p.alphaValue = 0
        p.orderBack(nil)
        p.makeKey()
        other = p
    }

    override func tearDown() { other?.close() }

    private func open(_ section: SettingsSection) -> ClickHarness {
        ClickHarness(SettingsView(hub: hub(), notch: NotchController(hub: hub()), initial: section), size: SettingsWindow.size,
                     window: { SettingsWindow.makeWindow(content: $0) })
    }

    func testDoneSitsOnTheTitleRowInLineWithTheCards() throws {
        for section in [SettingsSection.account, .rules, .models] {  // General alone takes ~30 s to settle here
            let h = open(section)
            defer { h.close() }
            let done = try XCTUnwrap(ClickTargets.frames["settings.done"], "\(section)")
            let title = try XCTUnwrap(ClickTargets.frames["settings.title"], "\(section)")
            XCTAssertEqual(done.maxX, title.maxX, accuracy: 1, "\(section): Done's right edge lines up with the cards")
            XCTAssertTrue(title.minY <= done.midY && done.midY <= title.maxY, "\(section): Done is on the title's row")
            let content = try XCTUnwrap(ClickTargets.frames["settings.content"])
            XCTAssertLessThanOrEqual(done.maxY, content.minY, "\(section): the cards scroll below Done, never under it")
        }
    }

    // MARK: a normal Mac window

    func testSettingsIsANormalMacWindow() {
        let w = SettingsWindow.makeWindow(content: NSView())
        defer { w.close() }
        XCTAssertTrue(w.styleMask.isSuperset(of: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView]))
        XCTAssertTrue(w.titlebarAppearsTransparent)
        XCTAssertEqual(w.titleVisibility, .hidden)
        XCTAssertFalse(w.isMovableByWindowBackground, "drag-anywhere caused the double-click bug")
        XCTAssertEqual(w.contentMinSize, SettingsWindow.minSize)
    }

    func testTheTrafficLightsWaitForThePointer() {
        let w = SettingsWindow.makeWindow(content: NSView())
        defer { w.close() }
        let buttons = [NSWindow.ButtonType.closeButton, .miniaturizeButton, .zoomButton].compactMap { w.standardWindowButton($0) }
        XCTAssertEqual(buttons.count, 3)
        XCTAssertEqual(buttons.map(\.alphaValue), [0, 0, 0], "hidden until the pointer comes near the top")
    }

    func testTrafficLightsShowOnlyNearTheTop() {
        let f = CGRect(x: 100, y: 100, width: 720, height: 560)  // top edge at y = 660
        XCTAssertTrue(TrafficLights.visible(mouse: CGPoint(x: 130, y: 650), in: f))
        XCTAssertTrue(TrafficLights.visible(mouse: CGPoint(x: 700, y: 630), in: f), "anywhere along the top band")
        XCTAssertFalse(TrafficLights.visible(mouse: CGPoint(x: 400, y: 400), in: f), "over the content")
        XCTAssertFalse(TrafficLights.visible(mouse: CGPoint(x: 50, y: 650), in: f), "outside the window")
    }

    /// (The test host can't press it: with activation policy .prohibited AppKit disables the buttons.)
    func testTheRedButtonOnlyClosesSettings() {
        let w = SettingsWindow.makeWindow(content: NSView())
        let red = w.standardWindowButton(.closeButton)
        XCTAssertTrue(red?.target === w, "it closes this window, never quits Maxie")
        w.orderBack(nil)
        w.close()
        XCTAssertFalse(w.isVisible)
        XCTAssertFalse(w.isReleasedWhenClosed, "kept, so Settings reopens where it was")
    }

    /// Settings is now a titled window: closing it (the last window on screen) must never quit Maxie.
    func testClosingTheLastWindowNeverQuitsMaxie() {
        XCTAssertEqual(NSApp.delegate?.applicationShouldTerminateAfterLastWindowClosed?(NSApp), false)
    }

    // MARK: on the Maxie page he never scrolls away

    func testThePreviewShrinksWithTheScrollButNeverLeaves() {
        XCTAssertEqual(MaxiePreview.side(scrolled: -30), MaxiePreview.big, "rubber-banding at the top")
        XCTAssertEqual(MaxiePreview.side(scrolled: 0), MaxiePreview.big)
        XCTAssertEqual(MaxiePreview.side(scrolled: MaxiePreview.shrinkOver), MaxiePreview.compact)
        XCTAssertEqual(MaxiePreview.side(scrolled: 5000), MaxiePreview.compact)
        let mid = MaxiePreview.side(scrolled: MaxiePreview.shrinkOver / 2)
        XCTAssertTrue(MaxiePreview.compact < mid && mid < MaxiePreview.big)
    }

    /// The pinned preview shared the window's see-through backdrop, so the cards scrolling under it showed through.
    func testThePinnedPreviewHidesTheCardsScrollingUnderIt() {
        XCTAssertEqual(MaxiePreview.backdrop(scrolled: 0), LightField.baseOpacity, "at rest it looks like the page")
        XCTAssertEqual(MaxiePreview.backdrop(scrolled: 1), 1)
        XCTAssertEqual(MaxiePreview.backdrop(scrolled: 600), 1)
    }

    func testOnTheMaxiePageThePreviewStaysPinnedWhileTheOptionsScroll() throws {
        let hub = hub()
        hub.maxie = try JSONDecoder().decode(MaxieInfo.self, from: Data(ClickTests.maxieJSON.utf8))
        let h = ClickHarness(SettingsView(hub: hub, notch: NotchController(hub: hub), initial: .maxie), size: SettingsWindow.size,
                             window: { SettingsWindow.makeWindow(content: $0) })
        defer { h.close() }
        h.settle(0.5)
        let content = try XCTUnwrap(ClickTargets.frames["settings.content"])
        let before = try XCTUnwrap(ClickTargets.frames["maxie.preview.pinned"])
        let scroll = try XCTUnwrap(Self.first(NSScrollView.self, in: h.host))
        scroll.contentView.scroll(to: NSPoint(x: 0, y: 600))
        scroll.reflectScrolledClipView(scroll.contentView)
        h.settle(0.5)
        let after = try XCTUnwrap(ClickTargets.frames["maxie.preview.pinned"])
        XCTAssertGreaterThan(scroll.contentView.bounds.origin.y, 300, "the options scrolled")
        XCTAssertEqual(after.minY, content.minY, accuracy: 2, "he stays at the top of the page")
        XCTAssertLessThan(after.height, before.height, "compact while the options scroll")
    }

    private static func first<V: NSView>(_ type: V.Type, in view: NSView) -> V? {
        if let v = view as? V { return v }
        for sub in view.subviews { if let v = first(type, in: sub) { return v } }
        return nil
    }
}
