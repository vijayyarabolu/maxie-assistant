import SwiftUI

/// Memory on the Thread (second brain v1): every fact a small gold knot with where it came from and when.
/// "Find" narrows the list; hovering a fact shows "Edit" and "Forget". A fact remembered since the panel
/// last looked glows once. Facts come only from the user's own words ("remember …", or the brain's remember tool).
struct MemoryTab: View {
    let hub: HubClient
    @State private var facts: [Fact] = []
    @State private var query = ""
    @State private var editing: Int? = nil
    @State private var draft = ""
    @AppStorage("memorySeenUpTo") private var seenUpTo: Double = 0
    @State private var glowAfter: Double = .infinity

    struct Fact: Decodable, Identifiable {
        let id: Int; let topic: String; let text: String; let source: String?; let created: Double?
        var when: String {
            guard let created else { return "" }
            return Date(timeIntervalSince1970: created).formatted(.dateTime.month(.abbreviated).day())
        }
        var provenance: String { [source ?? "you told me", when].filter { !$0.isEmpty }.joined(separator: ", ") }
    }
    struct Memory: Decodable { let facts: [Fact] }

    private var shown: [Fact] {
        query.isEmpty ? facts : facts.filter { $0.text.localizedCaseInsensitiveContains(query) || $0.topic.localizedCaseInsensitiveContains(query) }
    }
    private var topics: [String] { Array(Set(shown.map(\.topic))).sorted() }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {  // a field that looks like one: glyph, quiet prompt, hairline edge
                Image(systemName: "magnifyingglass").font(.system(size: 12, weight: .medium)).foregroundStyle(Light.quiet)
                ZStack(alignment: .leading) {
                    if query.isEmpty { Text("Find what I know (what did I decide about…)").foregroundStyle(Light.quiet) }
                    TextField("", text: $query).textFieldStyle(.plain).accessibilityLabel("Find what I know")
                }
            }
            .font(Light.text)
            .padding(.vertical, 6).padding(.horizontal, 10)
            .background(RoundedRectangle(cornerRadius: 8, style: .continuous).strokeBorder(Light.edge, lineWidth: 1))
            .padding(.leading, 24).padding(.bottom, 6)  // its edge on the text column below
            ZStack(alignment: .topLeading) {
                ThreadLine(mode: .idle).frame(width: 14).frame(maxHeight: .infinity)
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 14) {
                        if shown.isEmpty {
                            HStack(spacing: 10) {
                                Knot(kind: .fact)
                                Text(facts.isEmpty ? "Nothing yet. Say \"remember …\" and it lands here." : "Nothing matches.")
                                    .font(Light.text).foregroundStyle(Light.quiet)
                            }
                        }
                        ForEach(topics, id: \.self) { topic in
                            VStack(alignment: .leading, spacing: 8) {
                                Text(topic.capitalized).font(Light.small).foregroundStyle(Light.quiet).padding(.leading, 24)
                                ForEach(shown.filter { $0.topic == topic }) { f in row(f) }
                            }
                        }
                    }
                    .padding(.vertical, 6)
                }
                .scrollIndicators(.never)
            }
        }
        .task { await load() }
    }

    private func row(_ f: Fact) -> some View {
        FactRow(fact: f, glowing: (f.created ?? 0) > glowAfter, editing: editing == f.id, draft: $draft,
                onEdit: { editing = f.id; draft = f.text }, onSave: { save(f) }, onCancel: { editing = nil }, onForget: { forget(f) })
    }

    private func load() async {
        guard let m: Memory = await hub.fetch("/api/memory") else { return }
        facts = m.facts
        if glowAfter == .infinity { glowAfter = seenUpTo }
        seenUpTo = max(seenUpTo, facts.compactMap(\.created).max() ?? 0)
    }

    private func save(_ f: Fact) {
        let text = draft.trimmingCharacters(in: .whitespaces)
        editing = nil
        guard !text.isEmpty, text != f.text else { return }
        Task {
            _ = await hub.put("/api/memory/\(f.id)", dict: ["text": text])
            await load()
        }
    }

    private func forget(_ f: Fact) {
        withAnimation(Light.spring) { facts.removeAll { $0.id == f.id } }
        Task { _ = await hub.delete("/api/memory/\(f.id)") }
    }
}

private struct FactRow: View {
    let fact: MemoryTab.Fact
    let glowing: Bool
    let editing: Bool
    @Binding var draft: String
    let onEdit: () -> Void, onSave: () -> Void, onCancel: () -> Void, onForget: () -> Void
    @State private var hover = false

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Knot(kind: .fact, glowing: glowing).padding(.top, 4)
            VStack(alignment: .leading, spacing: 2) {
                if editing {
                    TextField("", text: $draft).textFieldStyle(.plain).font(Light.text).onSubmit(onSave)
                } else {
                    Text(fact.text).font(Light.text).lineSpacing(Light.leading).textSelection(.enabled)
                }
                Text(fact.provenance).font(Light.small).foregroundStyle(Light.quiet)
            }
            Spacer(minLength: 8)
            HStack(spacing: 2) {
                if editing {
                    Button("Save", action: onSave).buttonStyle(.wordPrimary)
                    Button("Cancel", action: onCancel).buttonStyle(.word)
                } else {
                    Button("Edit", action: onEdit).buttonStyle(.word)
                    Button("Forget", action: onForget).buttonStyle(.word)
                }
            }
            .opacity(hover || editing ? 1 : 0)
            .allowsHitTesting(hover || editing)
        }
        .contentShape(Rectangle())
        .onHover { hover = $0 }
    }
}
