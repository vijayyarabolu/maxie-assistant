import SwiftUI
import XCTest
@testable import ClaudeNotch

/// The background queue and the Scheduled list in Activity, and the "Can I use the screen?" card
/// with Use it now / When I'm away / Not now. Rendered in the off-screen harness only.
@MainActor
final class BackgroundTests: XCTestCase {
    private func hub() -> HubClient { HubClient(base: URL(string: "http://127.0.0.1:9")!, token: nil) }

    private func decode<T: Decodable>(_ json: String) throws -> T {
        try JSONDecoder().decode(T.self, from: Data(json.utf8))
    }

    func testAScreenAskDecodesAndOldCardsStillDo() throws {
        let screen: Confirmation = try decode(#"{"id":"a","tool":"run_flow","args":{"name":"x"},"summary":"Use the screen for ~20 s to replay Expense report","after_untrusted":false,"status":"pending","screen":true}"#)
        XCTAssertTrue(screen.screen)
        XCTAssertEqual(screen.status, "pending")
        let old: Confirmation = try decode(#"{"id":"b","summary":"Send an email","after_untrusted":true}"#)
        XCTAssertFalse(old.screen)
        XCTAssertEqual(old.status, "pending")
    }

    func testTheScreenCardAsksHowLongAndWhy() {
        let c = Confirmation(id: "a", summary: "Use the screen for ~20 s to replay Expense report", afterUntrusted: false, screen: true)
        XCTAssertEqual(ScreenAsk.title(c), "Can I use the screen for ~20 s?")
        XCTAssertEqual(ScreenAsk.detail(c), "To replay Expense report.")
        let later = Confirmation(id: "a", summary: c.summary, afterUntrusted: false, status: "away", screen: true)
        XCTAssertEqual(ScreenAsk.title(later), "When you step away")
        XCTAssertEqual(ScreenAsk.detail(later), "I'll replay Expense report once you've been away 10 minutes.")
        let odd = Confirmation(id: "b", summary: "Replay x on your Mac", afterUntrusted: false, screen: true)
        XCTAssertEqual(ScreenAsk.title(odd), "Can I use the screen?")
        XCTAssertEqual(ScreenAsk.detail(odd), "Replay x on your Mac.")
    }

    func testBackgroundJobsAndScheduledDecode() throws {
        let jobs: [BackgroundJob] = try decode(#"[{"id":3,"ts":1760600000,"text":"find 5 jobs","source":"you","status":"waiting","reply":"Needs your OK","cost":0.01,"started":null,"finished":null,"task_id":7}]"#)
        XCTAssertEqual(jobs.first?.statusLine, "Needs your OK")
        XCTAssertTrue(jobs.first?.isOpen ?? false)
        let items: [ScheduledItem] = try decode(#"[{"name":"Job inbox","when":"Every 5 minutes","what":"Interview mail.","reads_only":true},{"name":"Routine","when":"Weekdays at 9:00","what":"Open jobhunt on weekdays at 9:00.","reads_only":false}]"#)
        XCTAssertEqual(items.map(\.id), ["Job inbox|Interview mail.", "Routine|Open jobhunt on weekdays at 9:00."])
        XCTAssertEqual(BackgroundJob.label("queued"), "Waiting its turn")
        XCTAssertEqual(BackgroundJob.label("running"), "Working on it")
        XCTAssertEqual(BackgroundJob.label("error"), "Couldn't finish")
    }

    func testABackgroundTaskNeverTakesOverTheRunningSlot() throws {
        let h = hub()
        let bg: TaskInfo = try decode(#"{"id":9,"ts":1,"text":"find jobs","device":"background","route":"multi_step","model":null,"status":"running","steps":[],"reply":"","cost":0,"first_ms":null,"total_ms":null}"#)
        h.applyTask(bg)
        XCTAssertNil(h.currentTask, "the user's own task keeps Pause / Cancel; background work shows under In the background")
        XCTAssertEqual(h.tasks.first?.id, 9, "its steps are still in the timeline")
    }

    func testTheScreenCardButtonsAnswerWithOneClick() {
        let h = hub()
        let c = Confirmation(id: "a", summary: "Use the screen for ~20 s to replay Expense report", afterUntrusted: false, screen: true)
        h.confirmations = [c]
        let harness = ClickHarness(ScreenTurn(c: c, hub: h).frame(width: 420).padding(20), size: CGSize(width: 460, height: 200))
        defer { harness.close() }
        harness.settle(0.2)
        harness.click(target: "screen.away")
        XCTAssertEqual(h.confirmations.first?.status, "away", "When I'm away: the card stays, waiting")
        h.confirmations = [c]
        harness.settle(0.1)
        harness.click(target: "screen.no")
        XCTAssertTrue(h.confirmations.isEmpty, "Not now: gone")
    }

    func testActivityShowsTheQueueAndTheSchedule() {
        let h = hub()
        h.backgroundJobs = [BackgroundJob(id: 1, ts: 0, text: "find 5 data engineer jobs in Austin", source: "you",
                                          status: "queued", reply: "")]
        h.scheduled = [ScheduledItem(name: "Job inbox", when: "Every 5 minutes", what: "Interview mail.", reads_only: true)]
        let harness = ClickHarness(ActivityTab(hub: h).frame(width: 420, height: 600), size: CGSize(width: 420, height: 600))
        defer { harness.close() }
        harness.settle(0.3)
        XCTAssertNotNil(harness.center(target: "background.cancel.1"), "a queued task can be cancelled")
        XCTAssertNil(harness.center(target: "scheduled.Job inbox"), "folded until the user opens it")
        harness.click(target: "scheduled.toggle")
        harness.settle(0.3)
        XCTAssertNotNil(harness.center(target: "scheduled.Job inbox"), "the schedule opens with one click")
    }

    func testTheWarmSonnetCardSaysWhereTheRunIs() {
        let s = WarmSonnetCard.Report.sample
        XCTAssertEqual(WarmSonnetCard.phaseLine(s.status), "Day 3 of 14: week one, Sonnet not warmed.")
        XCTAssertEqual(WarmSonnetCard.side("Cold", s.verdict.off), "Cold: $0.31 a day over 3 days · first word 2450 ms")
        XCTAssertEqual(WarmSonnetCard.side("Warm", s.verdict.on), "Warm: no days yet")
    }

    func testAQueryInAPathStaysAQuery() {
        let base = URL(string: "http://127.0.0.1:8780")!
        XCTAssertEqual(HubClient.url(base, "/api/backup?verify=true").absoluteString, "http://127.0.0.1:8780/api/backup?verify=true")
        XCTAssertEqual(HubClient.url(base, "/api/background").absoluteString, "http://127.0.0.1:8780/api/background")
    }
}
