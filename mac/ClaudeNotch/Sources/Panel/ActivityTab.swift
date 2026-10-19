import SwiftUI

/// Activity on the Thread: every task a knot, its steps small knots on a fork. The one running has
/// Pause / Resume / Cancel and a line to redirect it. Fed by /api/tasks and the "task" events. Under it, what
/// Maxie does on his own (In the background) and, folded, what runs on a schedule.
struct ActivityTab: View {
    let hub: HubClient
    @State private var redirect = ""
    @State private var expanded: Set<Int> = []

    var body: some View {
        ZStack(alignment: .topLeading) {
            ThreadLine(mode: hub.currentTask == nil ? .idle : .thinking).frame(width: 14).frame(maxHeight: .infinity)
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 14) {
                    if let t = hub.currentTask { running(t) }
                    BackgroundList(hub: hub)
                    if hub.tasks.isEmpty {
                        HStack(spacing: 10) {
                            Knot(kind: .thinking)
                            Text("Nothing yet. Everything you ask shows up here, step by step.")
                                .font(Light.text).foregroundStyle(Light.quiet)
                        }
                    }
                    ForEach(hub.tasks.filter { $0.id != hub.currentTask?.id }) { t in
                        TaskRow(task: t, open: expanded.contains(t.id)) {
                            withAnimation(Light.spring) { if expanded.contains(t.id) { expanded.remove(t.id) } else { expanded.insert(t.id) } }
                        }
                    }
                    ScheduledList(hub: hub)
                }
                .padding(.vertical, 6)
            }
            .scrollIndicators(.never)
        }
        .task {
            await hub.loadTasks()
            await hub.loadBackground()
        }
    }

    private func running(_ t: TaskInfo) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .top, spacing: 10) {
                Knot(kind: .thinking).padding(.top, 4)
                VStack(alignment: .leading, spacing: 2) {
                    Text(t.status == "paused" ? "Paused" : "Working on").font(Light.small).foregroundStyle(Light.gold)
                    Text(t.text).font(Light.text).lineSpacing(Light.leading).textSelection(.enabled)
                }
            }
            StepsFork(steps: t.steps)
            HStack(spacing: 6) {
                if t.status == "paused" {
                    Button("Resume") { hub.control(task: t.id, "resume") }.buttonStyle(.wordPrimary)
                } else {
                    Button("Pause") { hub.control(task: t.id, "pause") }.buttonStyle(.wordPrimary)
                }
                Button("Cancel") { hub.control(task: t.id, "cancel") }.buttonStyle(.word)
            }
            .padding(.leading, 18)
            TextField("Tell it what to do instead, then Return", text: $redirect)
                .textFieldStyle(.plain).font(Light.text).padding(.leading, 24)
                .onSubmit { send(redirectFrom: t.id) }
        }
    }

    private func send(redirectFrom id: Int) {
        let text = redirect.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        hub.control(task: id, "cancel")
        hub.send(text: text)
        redirect = ""
    }
}

struct TaskRow: View {
    let task: TaskInfo
    let open: Bool
    let toggle: () -> Void

    private var knot: Knot.Kind {
        switch task.status {
        case "done": .claude
        case "running", "paused": .thinking
        default: .step(ok: false)
        }
    }

    /// "calendar, 2 steps, 1.2 s, $0.0012" plus the status word when it is not plain done.
    private var meta: String {
        var parts = [task.route.replacingOccurrences(of: "_", with: " ")]
        if task.status != "done" { parts.append(task.status == "error" ? "failed" : task.status) }
        if !task.steps.isEmpty { parts.append("\(task.steps.count) step\(task.steps.count == 1 ? "" : "s")") }
        if let ms = task.total_ms { parts.append(ms < 100 ? "instant" : String(format: "%.1f s", ms / 1000)) }
        if task.cost > 0 { parts.append(String(format: "$%.4f", task.cost)) }
        return parts.joined(separator: ", ")
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Button(action: toggle) {
                HStack(alignment: .top, spacing: 10) {
                    Knot(kind: knot).padding(.top, 4)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(task.text).font(Light.text).lineLimit(open ? nil : 1)
                        Text(meta).font(Light.small).foregroundStyle(task.status == "error" ? Light.bad : Light.quiet)
                    }
                    Spacer(minLength: 8)
                    Text(Date(timeIntervalSince1970: task.ts), style: .time).font(Light.small).foregroundStyle(Light.quiet)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityHint(open ? "Hide the steps" : "Show the steps")
            if open {
                StepsFork(steps: task.steps)
                if !task.reply.isEmpty {
                    Text(task.reply).font(Light.text).lineSpacing(Light.leading).foregroundStyle(Light.quiet)
                        .textSelection(.enabled).padding(.leading, 24)
                }
            }
        }
    }
}

/// The steps of one task as knots on a small fork: tool, failed or not, how long it took.
struct StepsFork: View {
    let steps: [TaskStep]
    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            ForEach(Array(steps.enumerated()), id: \.offset) { _, s in
                HStack(spacing: 8) {
                    Knot(kind: .step(ok: s.ok))
                    Text(s.name.replacingOccurrences(of: "_", with: " ") + (s.ok ? "" : ", failed"))
                        .font(Light.small).foregroundStyle(s.ok ? Light.quiet : Light.bad)
                    Spacer()
                    Text("\(s.ms) ms").font(Light.small.monospacedDigit()).foregroundStyle(Light.quiet)
                }
            }
            if steps.isEmpty { Text("No tool steps").font(Light.small).foregroundStyle(Light.quiet).padding(.leading, 22) }
        }
        .padding(.leading, 14)
    }
}
