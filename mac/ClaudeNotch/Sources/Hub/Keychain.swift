import Foundation

/// Secrets live in the login Keychain under service "maxie" (same as the Python hub).
///
/// Read through `/usr/bin/security`, exactly like the hub does: the items were created by that tool,
/// so it is already on their access list and no "ClaudeNotch wants to use your keychain" dialog
/// appears, even though this app is only ad-hoc signed (its code identity changes every build).
enum Keychain {
    static func read(_ account: String, service: String = "maxie") -> String? {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/security")
        p.arguments = ["find-generic-password", "-s", service, "-a", account, "-w"]
        let out = Pipe()
        p.standardOutput = out
        p.standardError = FileHandle.nullDevice
        do { try p.run() } catch { return nil }
        let data = out.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        guard p.terminationStatus == 0,
              let s = String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines),
              !s.isEmpty else { return nil }
        return s
    }
}
