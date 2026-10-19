import SwiftUI

/// Settings › Speed: should Maxie keep Sonnet warm too? (maxie.warm_experiment.)
/// The hub runs it on its own: 7 days off, 7 days on, then keeps the cheaper one (or the faster, within 10 %).
/// The toggle is the same `warm_sonnet` flag; a flip by hand is fine, each day counts by what really happened.
struct WarmSonnetCard: View {
    let hub: HubClient
    @State private var report: Report?
    @State private var on = false
    private let isSample: Bool

    struct Side: Decodable { let days: Int; let usd_per_day: Double; let first_p50: Double?; let first_turns: Int }
    struct Verdict: Decodable { let off: Side; let on: Side; let keep_on: Bool; let line: String }
    struct Status: Decodable { let phase: String; let day: Int; let of: Int; let verdict: String; let warm_sonnet: Bool }
    struct Report: Decodable { let status: Status; let verdict: Verdict }

    init(hub: HubClient, sample: Report? = nil) {
        self.hub = hub
        isSample = sample != nil
        _report = State(initialValue: sample)
        _on = State(initialValue: sample?.status.warm_sonnet ?? false)
    }

    static func phaseLine(_ s: Status) -> String {
        switch s.phase {
        case "off": "Day \(s.day) of \(s.of): week one, Sonnet not warmed."
        case "on": "Day \(s.day) of \(s.of): week two, Sonnet warmed every 55 min."
        case "done": s.verdict
        default: "Starts with the hub's next minute."
        }
    }

    static func side(_ name: String, _ s: Side) -> String {
        guard s.days > 0 else { return "\(name): no days yet" }
        let speed = s.first_p50.map { String(format: " · first word %.0f ms", $0) } ?? ""
        return String(format: "%@: $%.2f a day over %d day%@%@", name, s.usd_per_day, s.days, s.days == 1 ? "" : "s", speed)
    }

    var body: some View {
        SunsetCard {
            SettingRow(title: "Keep Sonnet warm too",
                       subtitle: "Haiku's memory is refreshed every 55 min so quick answers start fast. Does doing it for Sonnet pay? Measured, not guessed.") {
                Toggle("", isOn: $on.onSet { v in Task { _ = await hub.putSettings(["warm_sonnet": v]); await load() } })
                    .labelsHidden()
                    .clickTarget("speed.warmSonnet")
            }
            if let r = report {
                Label(Self.phaseLine(r.status), systemImage: r.status.phase == "done" ? "checkmark.seal.fill" : "flask.fill")
                    .font(.callout).foregroundStyle(r.status.phase == "done" ? Sunset.good : .secondary)
                    .fixedSize(horizontal: false, vertical: true)
                Text(Self.side("Cold", r.verdict.off) + "\n" + Self.side("Warm", r.verdict.on))
                    .font(.system(.callout, design: .monospaced)).foregroundStyle(.secondary)
            }
            Text("7 days off, then 7 on, the same daily use. It keeps the cheaper one; within 10 % on cost, the faster one.")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
        }
        .task { if !isSample { await load() } }
    }

    private func load() async {
        if let fresh: Report = await hub.fetch("/api/experiments/warm_sonnet") {
            report = fresh
            on = fresh.status.warm_sonnet
        }
    }
}

extension WarmSonnetCard.Report {
    /// Synthetic numbers for the off-screen screenshot; never the user's.
    static var sample: Self {
        .init(status: .init(phase: "off", day: 3, of: 14, verdict: "", warm_sonnet: false),
              verdict: .init(off: .init(days: 3, usd_per_day: 0.31, first_p50: 2450, first_turns: 6),
                             on: .init(days: 0, usd_per_day: 0, first_p50: nil, first_turns: 0), keep_on: false, line: ""))
    }
}
