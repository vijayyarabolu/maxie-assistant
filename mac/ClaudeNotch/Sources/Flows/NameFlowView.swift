import SwiftUI

/// After watching: what was seen, in words, and one line for its name. Return saves.
struct NameFlowView: View {
    let hub: HubClient
    let steps: [FlowStep]
    @State private var name = ""
    @State private var problem = ""
    @FocusState private var focused: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(Self.summary(steps)).font(Light.text)
            TextField("", text: $name, prompt: Text("Call it, like expense report").foregroundStyle(Light.quiet))
                .textFieldStyle(.line)
                .focused($focused)
                .onSubmit(save)
            HStack(spacing: 16) {
                Button("Save", action: save).buttonStyle(.wordPrimary)
                    .disabled(name.trimmingCharacters(in: .whitespaces).isEmpty)
                Button("Discard") { hub.pendingFlow = nil }.buttonStyle(.word)
            }
            if !problem.isEmpty { Text(problem).font(Light.small).foregroundStyle(Light.bad) }
        }
        .padding(.bottom, 12)
        .onAppear { focused = true }
    }

    private func save() {
        let n = name.trimmingCharacters(in: .whitespaces)
        guard !n.isEmpty else { return }
        Task { problem = await hub.saveFlow(name: n) ?? "" }
    }

    /// "I saw 6 steps in Safari and Mail. What should I call it?"
    static func summary(_ steps: [FlowStep]) -> String {
        let n = steps.filter { $0.kind != .openApp }.count
        var apps: [String] = []
        for s in steps where s.kind == .openApp && !apps.contains(s.name) { apps.append(s.name) }
        let where_ = apps.isEmpty ? "" : " in " + (apps.count == 1 ? apps[0] : apps.dropLast().joined(separator: ", ") + " and " + apps.last!)
        return "I saw \(n) step\(n == 1 ? "" : "s")\(where_). What should I call it?"
    }
}
