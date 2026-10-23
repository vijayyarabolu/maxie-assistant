import XCTest
@testable import ClaudeNotch

/// reading the window list waits on WindowServer and stalled Maxie's main thread for up to seconds
/// (maxie-stalls.log). It is read off the main thread now; stepping aside works from what was read.
@MainActor
final class WindowListTests: XCTestCase {
    override func tearDown() {
        WindowList.reader = WindowList.read
        super.tearDown()
    }

    func testTheSystemListIsParsedIntoWindows() {
        let raw: [[String: Any]] = [
            [kCGWindowOwnerPID as String: Int32(42), kCGWindowLayer as String: 0,
             kCGWindowBounds as String: ["X": CGFloat(10), "Y": CGFloat(20), "Width": CGFloat(300), "Height": CGFloat(200)]],
            [kCGWindowOwnerPID as String: Int32(7), kCGWindowLayer as String: 25],  // no bounds: skipped
        ]
        XCTAssertEqual(WindowList.windows(from: raw), [.init(pid: 42, layer: 0, bounds: CGRect(x: 10, y: 20, width: 300, height: 200))])
    }

    func testTheListIsReadOffTheMainThread() async {
        // Arrange
        let seen = OnMain()
        WindowList.reader = { seen.value = Thread.isMainThread; return [.init(pid: 9, layer: 0, bounds: .zero)] }

        // Act
        let list = await WindowList.fresh()

        // Assert
        XCTAssertEqual(seen.value, false)
        XCTAssertEqual(list.map(\.pid), [9])
        XCTAssertEqual(WindowList.last.map(\.pid), [9], "kept for code that can't wait")
    }

    func testFrontWindowsAreTheFrontAppsNormalOnesInAppKitCoordinates() {
        let list: [WindowList.Window] = [
            .init(pid: 42, layer: 0, bounds: CGRect(x: 0, y: 0, width: 100, height: 50)),
            .init(pid: 42, layer: 3, bounds: CGRect(x: 0, y: 0, width: 10, height: 10)),  // a floating palette
            .init(pid: 7, layer: 0, bounds: CGRect(x: 0, y: 0, width: 400, height: 400)),  // another app
        ]
        let rects = StepAside.frontWindows(list, front: 42, primaryHeight: 1000)
        XCTAssertEqual(rects, [CGRect(x: 0, y: 950, width: 100, height: 50)])
    }

    func testAFullScreenWindowIsOneTheSizeOfAScreen() {
        let screens = [CGSize(width: 1512, height: 982)]
        let full: [WindowList.Window] = [.init(pid: 7, layer: 0, bounds: CGRect(x: 0, y: 0, width: 1512, height: 982))]
        let normal: [WindowList.Window] = [.init(pid: 7, layer: 0, bounds: CGRect(x: 0, y: 0, width: 1200, height: 800))]
        XCTAssertTrue(WindowList.fullscreen(full, screens: screens, own: 1))
        XCTAssertFalse(WindowList.fullscreen(normal, screens: screens, own: 1))
        XCTAssertFalse(WindowList.fullscreen(full, screens: screens, own: 7), "Maxie's own window never counts")
    }
}

/// Set from the reader's thread, read after the await.
private final class OnMain: @unchecked Sendable { var value: Bool? }
