import SwiftUI

/// "Can I use the screen for ~20 s?": a task that truly needs the keyboard and mouse asks first.
/// Use it now, When I'm away (it waits until the user has left the Mac 10 minutes), or Not now.
struct ScreenTurn: View {
    let c: Confirmation
    let hub: HubClient

    private var later: Bool { c.status == "away" }

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Knot(kind: .fact, glowing: !later).padding(.top, 4)
            VStack(alignment: .leading, spacing: 4) {
                Text(ScreenAsk.title(c)).font(Light.small).foregroundStyle(Light.notice)
                Text(ScreenAsk.detail(c)).font(Light.text).lineSpacing(Light.leading).textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
                HStack(spacing: 8) {
                    if later {
                        Button("Do it now") { hub.answer(c, approve: true) }.buttonStyle(.wordPrimary).clickTarget("screen.now")
                        Button("Cancel") { hub.answer(c, approve: false) }.buttonStyle(.word).clickTarget("screen.no")
                    } else {
                        Button("Use it now") { hub.answer(c, approve: true) }.buttonStyle(.wordPrimary).clickTarget("screen.now")
                        Button("When I'm away") { hub.answerLater(c) }.buttonStyle(.word).clickTarget("screen.away")
                        Button("Not now") { hub.answer(c, approve: false) }.buttonStyle(.word).clickTarget("screen.no")
                    }
                }
                .padding(.leading, -6)
            }
        }
    }
}

/// Activity › In the background: what Maxie is doing on his own, newest first. A queued task can be cancelled;
/// a running one stops with "Max, stop".
struct BackgroundList: View {
    let hub: HubClient
    static let shown = 5

    private var jobs: [BackgroundJob] {
        Array((hub.backgroundJobs.filter(\.isOpen) + hub.backgroundJobs.filter { !$0.isOpen }).prefix(Self.shown))
    }

    var body: some View {
        if !jobs.isEmpty {
            VStack(alignment: .leading, spacing: 8) {
                Text("In the background").font(Light.small.weight(.semibold)).foregroundStyle(Light.notice).padding(.leading, 24)
                ForEach(jobs) { JobRow(job: $0, hub: hub) }
            }
        }
    }
}

private struct JobRow: View {
    let job: BackgroundJob
    let hub: HubClient

    private var knot: Knot.Kind {
        switch job.status {
        case "running", "queued": .thinking
        case "done": .claude
        case "waiting": .fact
        default: .step(ok: false)
        }
    }

    private var line: String {
        let first = job.reply.split(separator: "\n").first.map(String.init) ?? ""
        return first.isEmpty || job.status == "waiting" ? job.statusLine : "\(job.statusLine): \(first)"
    }

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Knot(kind: knot).padding(.top, 4)
            VStack(alignment: .leading, spacing: 2) {
                Text(job.text).font(Light.text).lineLimit(2)
                Text(line).font(Light.small).lineLimit(2)
                    .foregroundStyle(job.status == "error" ? Light.bad : job.status == "waiting" ? Light.notice : Light.quiet)
            }
            Spacer(minLength: 8)
            if job.status == "queued" {
                Button("Cancel") { hub.cancelBackground(job.id) }.buttonStyle(.word).clickTarget("background.cancel.\(job.id)")
            }
        }
    }
}

/// Activity › Scheduled: everything that runs on its own, folded away until the user opens it. Each says whether it
/// only reads; routines open apps, behind the user's windows.
struct ScheduledList: View {
    let hub: HubClient
    @State private var open: Bool

    init(hub: HubClient, open: Bool = false) {
        self.hub = hub
        _open = State(initialValue: open)
    }

    var body: some View {
        if !hub.scheduled.isEmpty {
            VStack(alignment: .leading, spacing: 8) {
                Button {
                    withAnimation(Light.spring) { open.toggle() }
                } label: {
                    HStack(spacing: 6) {
                        Image(systemName: "chevron.right").font(.system(size: 10, weight: .semibold))
                            .rotationEffect(.degrees(open ? 90 : 0))
                        Text("Scheduled · \(hub.scheduled.count)").font(Light.small.weight(.semibold))
                    }
                    .foregroundStyle(Light.quiet).contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .padding(.leading, 24)
                .clickTarget("scheduled.toggle")
                .accessibilityHint(open ? "Hide what runs on its own" : "Show what runs on its own")
                if open {
                    ForEach(hub.scheduled) { item in
                        VStack(alignment: .leading, spacing: 2) {
                            HStack(spacing: 6) {
                                Text(item.name).font(Light.text)
                                Text(item.reads_only ? "reads only" : "opens apps").font(Light.small).foregroundStyle(Light.quiet)
                            }
                            Text("\(item.when) · \(item.what)").font(Light.small).foregroundStyle(Light.quiet)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        .padding(.leading, 24)
                        .clickTarget("scheduled.\(item.name)")
                    }
                }
            }
        }
    }
}
