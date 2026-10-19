import SwiftUI

/// Speed: first word per kind of turn, measured on every turn, against the targets, and who
/// answered: Apple's on-device model and this Mac for free, Claude paid.
struct SpeedSection: View {
    let hub: HubClient
    @State private var r: Report?
    private let isSample: Bool  // a screenshot with synthetic turns: never loads the user's real ones

    init(hub: HubClient, sample: Report? = nil) {
        self.hub = hub
        isSample = sample != nil
        _r = State(initialValue: sample)
    }

    struct Kind: Decodable { let n: Int; let target_ms: Int; let first_p50: Double; let first_p95: Double; let total_p50: Double; let met: Int; let ok: Bool }
    struct Recent: Decodable, Identifiable { let id: Int; let ts: Double; let kind: String; let text: String; let source: String; let first_ms: Double?; let total_ms: Double; var model: String? = nil }
    struct Day: Decodable { let day: String; let apple: Int; let local: Int; let claude: Int }
    struct Share: Decodable { let apple: Int; let local: Int; let claude: Int; let free_pct: Int; let days: Int; var daily: [Day] = [] }
    struct Report: Decodable { let today: [String: Kind]; let week: [String: Kind]; let recent: [Recent]; let line: String; var share: Share? = nil }

    private let kinds: [(String, String, String)] = [
        ("action", "Mac actions", "rules, no model"),
        ("quick", "Quick answers", "Apple on-device or Haiku"),
        ("heavy", "Heavy answers", "ack first, then Sonnet / Opus streams"),
    ]

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(spacing: 12) {
                ForEach(kinds, id: \.0) { k in
                    let t = r?.today[k.0]
                    SunsetCard(padding: 14) {
                        Text(k.1).font(.headline)
                        Text(k.2).font(.caption).foregroundStyle(.secondary)
                        HStack(alignment: .firstTextBaseline, spacing: 4) {
                            Text(t.map { $0.n > 0 ? String(format: "%.0f", $0.first_p50) : "none" } ?? "…")
                                .font(.system(.title, design: .rounded, weight: .bold)).monospacedDigit()
                            Text("ms").font(.caption).foregroundStyle(.secondary)
                        }
                        if let t, t.n > 0 {
                            Label(t.ok ? "under \(t.target_ms) ms" : "target \(t.target_ms) ms",
                                  systemImage: t.ok ? "checkmark.circle.fill" : "exclamationmark.circle")
                                .font(.caption).foregroundStyle(t.ok ? Sunset.good : Sunset.warn)
                            Text(String(format: "p95 %.0f ms · %d of %d met · %d today", t.first_p95, t.met, t.n, t.n))
                                .font(.caption2).foregroundStyle(.secondary)
                        } else {
                            Label("target \(t?.target_ms ?? 0) ms · none today", systemImage: "clock").font(.caption).foregroundStyle(.secondary)
                        }
                    }
                }
            }
            if let share = r?.share { WhoAnswered(share: share) }
            WarmSonnetCard(hub: hub, sample: isSample ? .sample : nil)
            DecidersCard(hub: hub, sample: isSample ? .sample : nil)
            SunsetCard {
                Text("This week").font(.headline)
                ForEach(kinds, id: \.0) { k in
                    if let w = r?.week[k.0], w.n > 0 {
                        HStack {
                            Text(k.1)
                            Spacer()
                            Text(String(format: "first word %.0f ms (p95 %.0f) · whole answer %.0f ms · %d turns", w.first_p50, w.first_p95, w.total_p50, w.n))
                                .font(.system(.callout, design: .monospaced)).foregroundStyle(.secondary)
                        }
                    }
                }
                if r?.week.values.allSatisfy({ $0.n == 0 }) ?? true { Text("No turns yet.").foregroundStyle(.secondary) }
            }
            SunsetCard {
                Text("Last turns").font(.headline)
                ForEach(r?.recent ?? []) { t in
                    HStack(spacing: 10) {
                        Chip(text: t.kind, symbol: t.kind == "action" ? "bolt.fill" : t.kind == "quick" ? "hare.fill" : "tortoise.fill",
                             tint: t.kind == "action" ? Sunset.accent : Sunset.warn)
                        Text(t.text).lineLimit(1).font(.callout)
                        Spacer()
                        if t.model == "apple-on-device" {
                            Text("Apple · free").font(.caption2.weight(.medium)).foregroundStyle(Sunset.good)
                        }
                        Text(t.first_ms.map { String(format: "%.0f ms", $0) } ?? "none").font(.system(.callout, design: .monospaced)).foregroundStyle(.secondary)
                    }
                }
                if r?.recent.isEmpty ?? true { Text("Nothing yet.").foregroundStyle(.secondary) }
            }
            Label("Measured in the hub on every turn: time to the first word (or the spoken ack) from the moment the command arrives.", systemImage: "stopwatch")
                .font(.caption).foregroundStyle(.secondary)
            Button { Task { if !isSample { await load() } } } label: { Label("Refresh", systemImage: "arrow.clockwise") }.buttonStyle(.sunset)
        }
        .task { if !isSample { await load() } }
    }

    private func load() async {
        if let fresh: Report = await hub.fetch("/api/latency") { r = fresh }
    }
}

/// Who answered this week, as one bar: Apple's on-device model (free, private), this Mac with no model
/// (free), Claude (paid). And the honest line about Apple's bigger cloud model.
private struct WhoAnswered: View {
    let share: SpeedSection.Share

    private var total: Int { max(share.apple + share.local + share.claude, 1) }
    private var parts: [(String, Int, Color)] {
        [("Apple (free)", share.apple, Sunset.good), ("No model (free)", share.local, Sunset.accent),
         ("Claude (paid)", share.claude, Sunset.warn)]
    }

    var body: some View {
        SunsetCard {
            HStack(alignment: .firstTextBaseline) {
                Text("Who answered").font(.headline)
                Spacer()
                Text("\(share.free_pct)% free · last \(share.days) days").font(.callout).foregroundStyle(.secondary).monospacedDigit()
            }
            GeometryReader { g in
                HStack(spacing: 2) {
                    ForEach(parts.filter { $0.1 > 0 }, id: \.0) { p in
                        RoundedRectangle(cornerRadius: 3).fill(p.2.gradient)
                            .frame(width: max(6, (g.size.width - 4) * CGFloat(p.1) / CGFloat(total)))
                    }
                }
            }
            .frame(height: 10)
            .accessibilityLabel("\(share.free_pct) percent of answers were free")
            HStack(spacing: 18) {
                ForEach(parts, id: \.0) { p in
                    HStack(spacing: 6) {
                        Circle().fill(p.2).frame(width: 7, height: 7)
                        Text(p.0).font(.callout)
                        Text("\(p.1)").font(.callout).foregroundStyle(.secondary).monospacedDigit()
                    }
                }
            }
            if !share.daily.isEmpty { PerDay(days: share.daily) }
            Text("Summaries, rewrites and replies go to Apple's on-device model first: free, and the text never leaves your Mac. Claude answers when Apple can't. Apple's larger Private Cloud Compute model isn't open to apps like Maxie yet, so Maxie doesn't use it; your Shortcuts can, with the Use Model action. And no app can drive Siri itself: Siri reaches Maxie through \"Ask Maxie\" and the other Maxie shortcuts.")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
        }
    }
}

/// One small stacked column per day: free (Apple + no model) under paid (Claude).
private struct PerDay: View {
    let days: [SpeedSection.Day]
    private let height: CGFloat = 44
    private let barWidth: CGFloat = 22

    var body: some View {
        let peak = CGFloat(max(days.map { $0.apple + $0.local + $0.claude }.max() ?? 0, 1))
        HStack(alignment: .bottom, spacing: 10) {
            ForEach(Array(days.enumerated()), id: \.offset) { _, d in
                VStack(spacing: 4) {
                    VStack(spacing: 1) {
                        Spacer(minLength: 0)
                        segment(d.claude, peak, Sunset.warn)
                        segment(d.local, peak, Sunset.accent)
                        segment(d.apple, peak, Sunset.good)
                        if d.apple + d.local + d.claude == 0 { Capsule().fill(Sunset.hairline).frame(height: 2) }
                    }
                    .frame(width: barWidth, height: height)
                    Text(d.day).font(.caption2).foregroundStyle(.secondary)
                }
                .frame(maxWidth: .infinity)
                .accessibilityElement(children: .ignore)
                .accessibilityLabel("\(d.day): \(d.apple) Apple, \(d.local) no model, \(d.claude) Claude")
            }
        }
    }

    @ViewBuilder private func segment(_ n: Int, _ peak: CGFloat, _ color: Color) -> some View {
        if n > 0 { RoundedRectangle(cornerRadius: 2).fill(color).frame(height: max(2, height * CGFloat(n) / peak)) }
    }
}

extension SpeedSection.Report {
    /// Synthetic numbers for the off-screen screenshot; never the user's turns.
    static var sample: Self {
        let k = { (n: Int, p50: Double, t: Int) in SpeedSection.Kind(n: n, target_ms: t, first_p50: p50, first_p95: p50 * 1.6, total_p50: p50 * 2.2, met: n - 1, ok: p50 <= Double(t)) }
        let kinds = ["action": k(14, 22, 300), "quick": k(9, 640, 700), "heavy": k(3, 520, 700)]
        let recent = [
            SpeedSection.Recent(id: 3, ts: 0, kind: "quick", text: "summarize this: the offsite moved to Thursday…", source: "rules", first_ms: 1850, total_ms: 1850, model: "apple-on-device"),
            SpeedSection.Recent(id: 2, ts: 0, kind: "action", text: "find the address Riley texted me", source: "rules", first_ms: 41, total_ms: 41),
            SpeedSection.Recent(id: 1, ts: 0, kind: "quick", text: "what's the capital of Peru", source: "lexicon", first_ms: 610, total_ms: 900, model: "claude-haiku-5-5"),
        ]
        return .init(today: kinds, week: kinds, recent: recent, line: "", share: .init(apple: 11, local: 14, claude: 12, free_pct: 68, days: 7,
                                                                                daily: zip(["Fri", "Sat", "Sun", "Mon", "Tue", "Wed", "Thu"],
                                                                                           [(0, 2, 3), (1, 1, 1), (0, 0, 0), (2, 3, 2), (3, 2, 2), (2, 4, 3), (3, 2, 1)])
                                                                                    .map { .init(day: $0, apple: $1.0, local: $1.1, claude: $1.2) }))
    }
}
