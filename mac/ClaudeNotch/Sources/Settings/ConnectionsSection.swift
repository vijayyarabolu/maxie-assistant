import AppKit
import SwiftUI

struct ConnectionsSection: View {
    let hub: HubClient
    @State private var c: Connections?
    @State private var jobhuntToken = ""
    @State private var note = ""
    @State private var linkFailed = false
    private let tick = Timer.publish(every: 3, on: .main, in: .common).autoconnect()

    struct GoogleAccount: Decodable, Equatable { let connected: Bool; let email: String; let name: String; let picture: String }
    struct Google: Decodable {
        let client: Bool; let login_status: String
        let accounts: [String: GoogleAccount]?
        let refused: [Refusal]?
    }
    /// A Google service that answered 403: why, in words, and the one fix that works.
    struct Refusal: Decodable, Equatable {
        let service: String; let why: String; let line: String; let fix_url: String

        enum Fix: Equatable { case enable(URL), signInAgain }

        var fix: Fix {
            if why == "api_off", let url = URL(string: fix_url), !fix_url.isEmpty { return .enable(url) }
            return .signInAgain
        }
    }
    struct Jobhunt: Decodable { let token: Bool; let url: String; let error: String? }
    struct Resumegen: Decodable { let token: Bool; let url: String }
    struct Tailscale: Decodable { let served: Bool; let url: String? }
    struct Phone: Decodable { let last_ask: Double?; let pushes: Int }
    struct Connections: Decodable {
        let google: Google; let jobhunt: Jobhunt; let resumegen: Resumegen; let tailscale: Tailscale; let phone: Phone?
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            if let c {
                GoogleCard(hub: hub, google: c.google)
                SunsetCard {
                    SettingRow(title: "Who can use Maxie", subtitle: "Passcode, devices and app tokens live in the web Settings.") {
                        Button("Manage in browser") { hub.openWebSettings("access") }.buttonStyle(.sunset).fixedSize()
                            .clickTarget("access.manage")
                    }
                }
                SunsetCard {
                    Label("Job apps", systemImage: "briefcase.fill").font(.headline)
                    SettingRow(title: "jobhunt", subtitle: Self.jobhuntLine(c.jobhunt)) {
                        HStack(spacing: 8) {
                            if !Self.jobhuntUnlinked(c.jobhunt) {
                                StatusMark(ok: c.jobhunt.token && c.jobhunt.error == nil, yes: "Linked", no: "Not answering")
                            }
                            if Self.jobhuntUnlinked(c.jobhunt) {
                                Button("Link") { link() }.buttonStyle(.sunsetProminent)
                            }
                        }
                    }
                    if linkFailed {  // the one-click Link couldn't find the file: paste it instead
                        HStack(spacing: 8) {
                            SecureField("Paste the jobhunt LAN token", text: $jobhuntToken).textFieldStyle(.line)
                            Button("Save") { saveToken("JOBHUNT_TOKEN", jobhuntToken); jobhuntToken = "" }.buttonStyle(.sunset).disabled(jobhuntToken.isEmpty)
                        }
                    }
                    Color.clear.frame(height: 2)
                    SettingRow(title: "resumegen", subtitle: c.resumegen.token ? c.resumegen.url : "Linked together with jobhunt.") {
                        if c.resumegen.token { StatusMark(ok: true, yes: "Linked") }
                    }
                }
                SunsetCard {
                    Label("iPhone", systemImage: "iphone").font(.headline)
                    SettingRow(title: "Tailscale", subtitle: "HTTPS to Maxie on your tailnet only, for the phone.") {
                        HStack(spacing: 8) {
                            if c.tailscale.served { StatusMark(ok: true, yes: "Served") }
                            if !c.tailscale.served {
                                Button("Serve to my iPhone") { serveToPhone() }.buttonStyle(.sunsetProminent)
                            }
                        }
                    }
                    if let url = c.tailscale.url {
                        HStack {
                            Text(url).font(.system(.caption, design: .monospaced)).textSelection(.enabled).foregroundStyle(.secondary)
                            Button { NSPasteboard.general.clearContents(); NSPasteboard.general.setString(url, forType: .string) } label: {
                                Image(systemName: "doc.on.doc")
                            }.buttonStyle(.sunset).help("Copy")
                        }
                    }
                    Color.clear.frame(height: 2)
                    SettingRow(title: "Test push", subtitle: phoneLine(c.phone)) {
                        Button { Task { let (code, body) = await hub.postJSON("/api/push/test", dict: [:]); note = code == 200 ? "Test push sent." : "No push: \(body)"; await load() } } label: {
                            Label("Send", systemImage: "bell.badge")
                        }.buttonStyle(.sunset)
                    }
                    Text("On the iPhone: open the address above, enter the passcode, Add to Home Screen, turn on notifications. Action Button + \"Hey Max\": docs/iphone.md.")
                        .font(.caption).foregroundStyle(.secondary)
                }
            } else {
                SunsetCard { Label("Loading…", systemImage: "ellipsis.circle").foregroundStyle(.secondary) }
            }
            NoteLine(text: note)
        }
        .task { await load() }
        .onReceive(tick) { _ in Task { await load() } }  // live: a consent page finished, a token appeared
    }

    /// Never a raw error: "Not linked" (no token, or jobhunt turned it away) or "isn't answering".
    static func jobhuntUnlinked(_ j: Jobhunt) -> Bool {
        !j.token || (j.error ?? "").hasPrefix("401") || (j.error ?? "").hasPrefix("403")
    }

    static func jobhuntLine(_ j: Jobhunt) -> String {
        if jobhuntUnlinked(j) { return "Maxie reads the jobhunt and resumegen token files set in Settings and keeps them on this Mac." }
        if j.error != nil { return "jobhunt isn't answering at \(j.url). Is it running?" }
        return j.url
    }

    private func serveToPhone() {
        guard MacHelpers.confirm("Serve Maxie to your iPhone?",
                                 "Runs: tailscale serve --bg --https=8780 http://127.0.0.1:8780. Only devices on your tailnet can reach it, and every request still needs the passcode or an app token.",
                                 ok: "Serve") else { return }
        note = "Asking Tailscale…"
        Task {
            let (code, body) = await hub.postJSON("/api/connections/tailscale", dict: [:])
            note = code == 200 ? "Served on your tailnet." : "Tailscale said: \(body)"
            await load()
        }
    }

    private func link() {
        guard PanelView.confirmLinkJobhunt() else { return }
        note = "Linking jobhunt…"
        Task {
            note = await hub.linkJobhunt()
            await load()
            linkFailed = c.map { Self.jobhuntUnlinked($0.jobhunt) } ?? true
        }
    }

    private func phoneLine(_ p: Phone?) -> String {
        guard let p else { return "No pushes yet." }
        let asked = p.last_ask.map { "iPhone last asked: " + Date(timeIntervalSince1970: $0).formatted(date: .abbreviated, time: .shortened) } ?? "The iPhone hasn't asked yet."
        return "\(asked)  ·  \(p.pushes) device\(p.pushes == 1 ? "" : "s") subscribed."
    }

    private func saveToken(_ account: String, _ value: String) {
        note = MacHelpers.keychainWrite(account: account, value: value) ? "Saved to the Keychain; restarting the hub…" : "Couldn't write to the Keychain."
        MacHelpers.kickstart(MacHelpers.hubLabel)
        Task { try? await Task.sleep(for: .seconds(3)); await load() }
    }

    private func load() async {
        c = await hub.fetch("/api/connections")
    }
}
