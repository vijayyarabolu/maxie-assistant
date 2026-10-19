import AppKit
import SwiftUI

/// Settings › Connections › Google: the accounts the hub is signed in to, each with its photo. Signing in and
/// out happens in the hub's web Settings (the hub runs the Google flow there); "Manage in browser" opens it.
struct GoogleCard: View {
    let hub: HubClient
    let google: ConnectionsSection.Google

    /// What each well-known account label is for. Any other label the hub sends is listed too, without a line.
    static let scopes: [String: String] = ["main": "Mail, drafts, calendar and Drive", "job": "The job inbox, read-only"]

    var body: some View {
        SunsetCard {
            HStack {
                Text("Google").font(.headline)
                Spacer(minLength: 8)
                Button("Manage in browser") { hub.openWebSettings("google") }.buttonStyle(.sunsetProminent).fixedSize()
                    .clickTarget("google.manage")
            }
            if labels.isEmpty {
                Text("No Google account yet. Sign in from the hub's web Settings.")
                    .font(Light.small).foregroundStyle(Light.quiet).fixedSize(horizontal: false, vertical: true)
            }
            ForEach(Array(labels.enumerated()), id: \.element) { i, label in
                if i > 0 { Color.clear.frame(height: 2) }
                row(label, scope: Self.scopes[label], info: google.accounts?[label])
            }
            ForEach(google.refused ?? [], id: \.service) { refusal($0) }
            if let waiting = Self.waitingLine(google.login_status) {
                Text(waiting).font(Light.small).foregroundStyle(Light.quiet)
            }
        }
    }

    private var labels: [String] { Self.orderedLabels(google.accounts) }

    /// The account labels in a steady order: "main", then "job", then the rest by name.
    static func orderedLabels(_ accounts: [String: ConnectionsSection.GoogleAccount]?) -> [String] {
        let known = ["main", "job"]
        let keys = Array((accounts ?? [:]).keys)
        return known.filter { keys.contains($0) } + keys.filter { !known.contains($0) }.sorted()
    }

    /// "Finish signing in in your browser…" while a consent page is open; the error words if it failed.
    static func waitingLine(_ status: String) -> String? {
        if status.hasPrefix("waiting") { return "Finish signing in in your browser…" }
        if status.hasPrefix("not signed in") { return status.prefix(1).uppercased() + status.dropFirst() + "." }
        return nil
    }

    private func row(_ account: String, scope: String?, info: ConnectionsSection.GoogleAccount?) -> some View {
        let connected = info?.connected ?? false
        let name = info?.name ?? ""
        let email = info?.email ?? ""
        let detail = [connected ? name : "", scope ?? ""].filter { !$0.isEmpty }.joined(separator: " · ")
        return HStack(alignment: .center, spacing: 12) {
            Avatar(url: info?.picture ?? "", name: name, connected: connected)
            VStack(alignment: .leading, spacing: 2) {
                Text(connected ? "Connected as \(email)" : (email.isEmpty ? account : email)).font(Light.text)
                if !detail.isEmpty {
                    Text(detail).font(Light.small).foregroundStyle(Light.quiet)
                }
            }
            Spacer(minLength: 8)
            if !connected {
                Text("Not connected").font(Light.small).foregroundStyle(Light.quiet)
            }
        }
    }

    /// "Calendar: Google said no" in the brief points here: why, and the fix as one button.
    private func refusal(_ r: ConnectionsSection.Refusal) -> some View {
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: "exclamationmark.circle").foregroundStyle(Light.quiet)
            VStack(alignment: .leading, spacing: 6) {
                Text("\(r.service): \(r.line)").font(Light.small).foregroundStyle(Light.quiet).fixedSize(horizontal: false, vertical: true)
                switch r.fix {
                case .enable(let url):
                    Button("Enable \(r.service) API") { NSWorkspace.shared.open(url) }.buttonStyle(.sunsetProminent)
                case .signInAgain:
                    Button("Sign in again in the browser") { hub.openWebSettings("google") }.buttonStyle(.sunsetProminent)
                }
            }
        }
        .padding(.top, 4)
    }

    /// The hub's {"detail": "..."} as plain words.
    static func detail(_ body: String) -> String {
        struct D: Decodable { let detail: String }
        return (try? JSONDecoder().decode(D.self, from: Data(body.utf8)).detail) ?? body
    }
}

/// The account's Google photo in a circle; its initial (or a person) until it loads or when signed out.
private struct Avatar: View {
    let url: String
    let name: String
    let connected: Bool

    var body: some View {
        ZStack {
            Circle().fill(Light.card)
            Circle().strokeBorder(Light.edge, lineWidth: 1)
            if connected, let u = URL(string: url), !url.isEmpty {
                AsyncImage(url: u) { $0.resizable().scaledToFill() } placeholder: { initial }
                    .clipShape(Circle())
            } else {
                initial
            }
        }
        .frame(width: 30, height: 30)
        .accessibilityHidden(true)
    }

    private var initial: some View {
        Group {
            if let c = name.first { Text(String(c)).font(.system(size: 13, weight: .semibold)) } else { Image(systemName: "person") }
        }
        .foregroundStyle(Light.quiet)
    }
}
