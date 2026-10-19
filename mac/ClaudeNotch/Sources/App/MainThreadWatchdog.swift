import Darwin
import Foundation

/// Debug builds: whenever the main thread is stuck for more than 250 ms, one entry with how long and what the main
/// thread was doing (its stack), in ~/Library/Logs/maxie/maxie-stalls.log. "Maxie is not responding"
/// was the main thread waiting on macOS; next time this log says where.
///
/// A background thread pings the main queue. No answer within the threshold: it pauses the main thread just long
/// enough to read its frame pointers (no allocation while it's paused: the main thread may hold the malloc lock),
/// lets it go, and logs once the main thread answers again.
final class MainThreadWatchdog: @unchecked Sendable {
    struct Stall: Sendable {
        let ms: Int
        let frames: [String]

        var text: String { "main thread stalled \(ms) ms\n" + frames.map { "  " + $0 }.joined(separator: "\n") }
    }

    static let threshold: TimeInterval = 0.25
    static let logFile = FileManager.default.homeDirectoryForCurrentUser.appending(path: "Library/Logs/maxie/maxie-stalls.log")
    private static let maxFrames = 48
    /// Return addresses can carry pointer-authentication bits above the 47-bit address.
    private static let addressMask: UInt = 0x0000_7FFF_FFFF_FFFF
    private static let poll: useconds_t = 10_000

    private let threshold: TimeInterval
    private let report: @Sendable (Stall) -> Void
    private let main: thread_act_t
    private let stack: ClosedRange<UInt>
    private let lock = NSLock()
    private var running = false

    /// Made on the main thread: that's the thread it watches.
    init(threshold: TimeInterval = MainThreadWatchdog.threshold,
         report: @escaping @Sendable (Stall) -> Void = { MainThreadWatchdog.append($0) }) {
        precondition(Thread.isMainThread, "watches the thread it is made on")
        self.threshold = threshold
        self.report = report
        main = mach_thread_self()
        let top = UInt(bitPattern: pthread_get_stackaddr_np(pthread_self()))
        stack = (top - UInt(pthread_get_stacksize_np(pthread_self())))...top
    }

    func start() {
        lock.withLock { running = true }
        let t = Thread { [self] in watch() }
        t.name = "maxie.watchdog"
        t.qualityOfService = .utility
        t.start()
    }

    func stop() { lock.withLock { running = false } }

    private var isRunning: Bool { lock.withLock { running } }

    private func watch() {
        while isRunning {
            let answered = Flag()
            let sent = Date()
            DispatchQueue.main.async { answered.set() }
            while !answered.isSet, isRunning, Date().timeIntervalSince(sent) < threshold { usleep(Self.poll) }
            guard isRunning else { return }
            if answered.isSet {
                usleep(useconds_t(threshold * 500_000))  // half the threshold between pings
                continue
            }
            let frames = mainStack()
            while !answered.isSet, isRunning { usleep(Self.poll) }
            guard isRunning else { return }
            report(Stall(ms: Int(Date().timeIntervalSince(sent) * 1000), frames: frames))
        }
    }

    /// The main thread's return addresses, symbolicated after it runs again.
    private func mainStack() -> [String] {
        #if arch(arm64)
        let buffer = UnsafeMutablePointer<UInt>.allocate(capacity: Self.maxFrames)
        defer { buffer.deallocate() }
        var n = 0
        var state = arm_thread_state64_t()
        var count = mach_msg_type_number_t(MemoryLayout<arm_thread_state64_t>.size / MemoryLayout<natural_t>.size)
        guard thread_suspend(main) == KERN_SUCCESS else { return [] }
        let got = withUnsafeMutablePointer(to: &state) {
            $0.withMemoryRebound(to: natural_t.self, capacity: Int(count)) { thread_get_state(main, ARM_THREAD_STATE64, $0, &count) }
        }
        if got == KERN_SUCCESS {
            buffer[0] = UInt(state.__pc)
            buffer[1] = UInt(state.__lr)
            n = 2
            var fp = UInt(state.__fp)
            while n < Self.maxFrames, fp % 8 == 0, stack.contains(fp), stack.contains(fp + 16) {
                guard let frame = UnsafePointer<UInt>(bitPattern: fp) else { break }
                buffer[n] = frame[1]  // the saved return address; frame[0] is the caller's frame pointer
                n += 1
                guard frame[0] > fp else { break }
                fp = frame[0]
            }
        }
        thread_resume(main)
        return (0..<n).map { Self.symbol(buffer[$0] & Self.addressMask) }
        #else
        return []
        #endif
    }

    private static func symbol(_ address: UInt) -> String {
        var info = Dl_info()
        guard dladdr(UnsafeRawPointer(bitPattern: address), &info) != 0 else { return String(format: "0x%lx", address) }
        let image = info.dli_fname.map { URL(fileURLWithPath: String(cString: $0)).lastPathComponent } ?? "?"
        return "\(image)  \(info.dli_sname.map { String(cString: $0) } ?? String(format: "0x%lx", address))"
    }

    static func append(_ stall: Stall) {
        append(line: stall.text)
        Log.d(stall.text)
    }

    /// One dated entry in the log (stalls, and the launch time: LaunchRules).
    static func append(line: String) {
        let entry = Data("[\(Date().formatted(.iso8601))] \(line)\n".utf8)
        try? FileManager.default.createDirectory(at: logFile.deletingLastPathComponent(), withIntermediateDirectories: true)
        if let h = try? FileHandle(forWritingTo: logFile) {
            _ = try? h.seekToEnd()
            try? h.write(contentsOf: entry)
            try? h.close()
        } else {
            try? entry.write(to: logFile)
        }
    }
}

/// Set once, from any thread.
private final class Flag: @unchecked Sendable {
    private let lock = NSLock()
    private var value = false

    func set() { lock.withLock { value = true } }
    var isSet: Bool { lock.withLock { value } }
}
