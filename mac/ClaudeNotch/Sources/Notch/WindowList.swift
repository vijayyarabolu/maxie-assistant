import AppKit

/// The windows on screen, from CGWindowList. Reading it waits on WindowServer, and while that is busy it blocked
/// Maxie's main thread for up to seconds (45 of 244 stalls in maxie-stalls.log on 2026-10-21, so a
/// "Hey Maxie" then could not open the panel). It is read on a background thread: `fresh()` waits for a new read
/// without blocking, and code that can't wait uses `recent()`, the last read (starting a new one when it is old).
@MainActor
enum WindowList {
    struct Window: Equatable, Sendable {
        let pid: Int32
        let layer: Int
        let bounds: CGRect  // CGWindowList coordinates: top-left origin
    }

    static let maxAge: TimeInterval = 1
    private(set) static var last: [Window] = []
    private static var readAt = Date.distantPast
    private static var reading: Task<[Window], Never>?
    /// Tests swap this to see which thread reads.
    static var reader: @Sendable () -> [Window] = read

    static func fresh() async -> [Window] {
        if let reading { return await reading.value }
        let read = reader
        let task = Task.detached(priority: .userInitiated) { read() }
        reading = task
        let list = await task.value
        reading = nil
        last = list
        readAt = Date()
        return list
    }

    static func recent() -> [Window] {
        if Date().timeIntervalSince(readAt) > maxAge, reading == nil { Task { _ = await fresh() } }
        return last
    }

    nonisolated static func read() -> [Window] {
        let raw = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]]
        return windows(from: raw ?? [])
    }

    nonisolated static func windows(from raw: [[String: Any]]) -> [Window] {
        raw.compactMap { w in
            guard let pid = w[kCGWindowOwnerPID as String] as? Int32, let layer = w[kCGWindowLayer as String] as? Int,
                  let b = w[kCGWindowBounds as String] as? [String: CGFloat],
                  let x = b["X"], let y = b["Y"], let width = b["Width"], let height = b["Height"] else { return nil }
            return Window(pid: pid, layer: layer, bounds: CGRect(x: x, y: y, width: width, height: height))
        }
    }

    /// A normal window of another app, the size of a screen.
    nonisolated static func fullscreen(_ list: [Window], screens: [CGSize], own: Int32) -> Bool {
        list.contains { w in
            w.layer == 0 && w.pid != own
                && screens.contains { abs($0.width - w.bounds.width) < 2 && abs($0.height - w.bounds.height) < 2 }
        }
    }
}
