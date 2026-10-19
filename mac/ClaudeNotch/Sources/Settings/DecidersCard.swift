import SwiftUI

/// Settings › Speed › Deciders: per decision type (route, direct action, yes/no), how fast and how
/// right each cheap decider is on labelled commands, and whether it passed the gate (95% on enough labels).
struct DecidersCard: View {
    let hub: HubClient
    @State private var report: Report?
    @State private var running = false
    private let isSample: Bool

    struct Decider: Decodable, Identifiable {
        let decider: String; let n: Int; let answered: Int; let accuracy: Double; let coverage: Double
        let p50_ms: Double; let live: Bool; let note: String
        var id: String { decider }
    }
    struct DecisionType: Decodable, Identifiable {
        let type: String; let n: Int; let deciders: [Decider]; let best: String?
        var id: String { type }
    }
    struct Report: Decodable { let updated: Double?; let types: [DecisionType] }

    private static let names = ["route": "Which route", "action": "Which Mac action", "yes_no": "Yes or no"]

    init(hub: HubClient, sample: Report? = nil) {
        self.hub = hub
        isSample = sample != nil
        _report = State(initialValue: sample)
    }

    var body: some View {
        SunsetCard {
            HStack(alignment: .firstTextBaseline) {
                Text("Deciders").font(.headline)
                Spacer()
                Button(running ? "Scoring…" : "Run now") { Task { await run() } }
                    .buttonStyle(.sunset).disabled(running || isSample)
            }
            Text("Free, fast deciders scored on labelled commands. One goes live for a decision only at 95% right on enough labels; until then Haiku decides.")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            if let types = report?.types, !types.isEmpty {
                ForEach(types) { t in typeRows(t) }
            } else {
                Text("Not scored yet. Run now takes a few seconds (Laya, when it's on, works at low priority).")
                    .foregroundStyle(.secondary)
            }
        }
        .task { if !isSample { report = await hub.fetch("/api/decisions") } }
    }

    @ViewBuilder private func typeRows(_ t: DecisionType) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(Self.names[t.type] ?? t.type).font(.callout.weight(.semibold))
                Text("\(t.n) labels").font(.caption).foregroundStyle(.secondary)
                Spacer()
                Text(t.best.map { "live: \($0)" } ?? "rules, then Haiku").font(.caption.weight(.medium))
                    .foregroundStyle(t.best == nil ? .secondary : Sunset.good)
            }
            ForEach(t.deciders) { d in
                HStack(spacing: 12) {
                    let always = d.decider == "rules"
                    Image(systemName: always ? "bolt.fill" : d.live ? "checkmark.circle.fill" : "circle.dashed")
                        .foregroundStyle(always ? Sunset.accent : d.live ? Sunset.good : .secondary).font(.caption)
                        .frame(width: 14)
                    Text(d.decider).font(.callout).frame(width: 64, alignment: .leading)
                    if d.answered > 0 {
                        Text(String(format: "%.0f%% right", d.accuracy * 100)).frame(width: 78, alignment: .leading)
                        Text("answers \(d.answered) of \(d.n)").foregroundStyle(.secondary).frame(width: 118, alignment: .leading)
                        Text(d.p50_ms < 0.1 ? "<0.1 ms" : String(format: "%.1f ms", d.p50_ms)).foregroundStyle(.secondary)
                    }
                    if !d.note.isEmpty { Text(d.note).foregroundStyle(.secondary).lineLimit(1) }
                    Spacer(minLength: 0)
                }
                .font(.system(.caption, design: .rounded)).monospacedDigit()
            }
        }
        .padding(.top, 4)
    }

    private func run() async {
        running = true
        defer { running = false }
        _ = await hub.postJSON("/api/decisions/run", dict: [:])
        report = await hub.fetch("/api/decisions")
    }
}

extension DecidersCard.Report {
    /// Synthetic numbers for the off-screen screenshot.
    static var sample: Self {
        typealias D = DecidersCard.Decider
        return .init(updated: 0, types: [
            .init(type: "route", n: 192, deciders: [
                D(decider: "rules", n: 192, answered: 42, accuracy: 1, coverage: 0.22, p50_ms: 0.01, live: false, note: "exact patterns: always on when they match"),
                D(decider: "lexicon", n: 192, answered: 192, accuracy: 0.875, coverage: 0.47, p50_ms: 0.3, live: false, note: ""),
                D(decider: "laya", n: 192, answered: 0, accuracy: 0, coverage: 0, p50_ms: 0, live: false, note: "Laya is not running"),
            ], best: nil),
            .init(type: "yes_no", n: 48, deciders: [
                D(decider: "rules", n: 48, answered: 37, accuracy: 1, coverage: 0.77, p50_ms: 0.0, live: false, note: "exact patterns: always on when they match"),
            ], best: nil),
        ])
    }
}
