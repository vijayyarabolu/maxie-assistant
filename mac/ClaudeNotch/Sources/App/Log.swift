import Foundation

/// Debug tracing to stderr, on only when CLAUDE_NOTCH_DEBUG is set (run the binary from a shell to see it).
enum Log {
    static let on = ProcessInfo.processInfo.environment["CLAUDE_NOTCH_DEBUG"] != nil

    static func d(_ msg: @autoclosure () -> String) {
        guard on else { return }
        FileHandle.standardError.write(Data("[\(Date().formatted(date: .omitted, time: .standard))] \(msg())\n".utf8))
    }
}
