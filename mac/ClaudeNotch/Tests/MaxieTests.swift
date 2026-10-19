import XCTest
@testable import ClaudeNotch

/// Maxie (Mochi) everywhere, the Classic robot as an option, and the new states.
@MainActor
final class MaxieTests: XCTestCase {
    private var defaults: UserDefaults!
    private let suite = "maxie.tests.\(UUID().uuidString)"
    /// The loops as the app ships them (its own bundle). Never the repo's assets/ (often under ~/Documents): a test
    /// build reading there asks the user for Documents access.
    private let assets = Bundle.main.resourceURL!

    override func setUp() {
        defaults = UserDefaults(suiteName: suite)  // never the user's real app settings
    }

    override func tearDown() {
        defaults.removePersistentDomain(forName: suite)
    }

    func testMaxieIsTheDefaultCharacter() {
        XCTAssertEqual(MaxieLook.character(defaults), .maxie)
        XCTAssertEqual(MaxieLook.loopsRoot(bundle: URL(fileURLWithPath: "/b"), defaults)?.path, "/b/mascot")
    }

    func testClassicComesFromItsOwnFolder() {
        MaxieLook.apply(character: "classic", loopsDir: nil, defaults)
        XCTAssertEqual(MaxieLook.loopsRoot(bundle: URL(fileURLWithPath: "/b"), defaults)?.path, "/b/mascot-classic")
    }

    /// Where a custom look lives comes from the settings alone: looking on disk (maybe ~/Documents) is the library's job,
    /// off the main thread.
    func testCustomLoopsComeFromTheSettingsWithoutTouchingDisk() {
        let dir = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)  // never created
        MaxieLook.apply(character: "maxie", loopsDir: dir.path, defaults)
        XCTAssertEqual(MaxieLook.customLoops(defaults)?.path, dir.path)
        XCTAssertEqual(MaxieLook.loopsRoot(bundle: URL(fileURLWithPath: "/b"), defaults)?.path, dir.path)
        XCTAssertEqual(MaxieLook.bundledRoot(bundle: URL(fileURLWithPath: "/b"), defaults)?.path, "/b/mascot")
        MaxieLook.apply(character: "classic", loopsDir: nil, defaults)
        XCTAssertEqual(MaxieLook.bundledRoot(bundle: URL(fileURLWithPath: "/b"), defaults)?.path, "/b/mascot-classic")
    }

    /// "Maxie is not responding": the main thread read the custom look from ~/Documents, and a new code
    /// signature made that read wait for macOS's "access your Documents folder?" answer. Folders outside the app are now only
    /// read in the background; until then the bundled look shows.
    func testOutsideFoldersAreNeverReadOnTheMainThread() throws {
        let custom = try loopsOutsideTheApp()
        let reads = Reads()
        let lib = MascotLibrary(root: { custom }, fallback: { self.assets.appending(path: "mascot-classic") }, bundle: assets,
                                read: { root, state, live, px in
                                    if root == custom { reads.add(main: Thread.isMainThread) }
                                    return MascotLibrary.read(root: root, state: state, liveEyes: live, px: px)
                                })
        let swapped = expectation(forNotification: .mascotChanged, object: nil)
        XCTAssertEqual(lib.slotSize, MascotLibrary.classicSize, "the bundled look while the custom one is read")
        wait(for: [swapped], timeout: 10)
        XCTAssertEqual(lib.slotSize, CGSize(width: 36, height: 42), "then the custom one")
        XCTAssertEqual(lib.loop(.idle)?.meta.state, "idle")
        XCTAssertEqual(reads.firstOnMain, false, "the first read of the custom folder (the one macOS can hold) was on the main thread")
        XCTAssertGreaterThan(reads.count, 0)
        try? FileManager.default.removeItem(at: custom)
    }

    func testAnIncompleteLookKeepsTheBundledOne() throws {
        let custom = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
        try FileManager.default.createDirectory(at: custom.appending(path: "idle"), withIntermediateDirectories: true)
        try Data("{}".utf8).write(to: custom.appending(path: "idle/meta.json"))
        let lib = MascotLibrary(root: { custom }, fallback: { self.assets.appending(path: "mascot-classic") }, bundle: assets)
        let swapped = expectation(forNotification: .mascotChanged, object: nil)
        swapped.isInverted = true
        XCTAssertEqual(lib.slotSize, MascotLibrary.classicSize)
        wait(for: [swapped], timeout: 1.5)
        XCTAssertEqual(lib.slotSize, MascotLibrary.classicSize)
        try? FileManager.default.removeItem(at: custom)
    }

    /// Maxie's idle loop copied outside the app, like a look the hub rendered into the user's folders.
    private func loopsOutsideTheApp() throws -> URL {
        let dir = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        try FileManager.default.copyItem(at: assets.appending(path: "mascot/idle"), to: dir.appending(path: "idle"))
        return dir
    }

    /// The test host is Maxie.app and shares the user's real defaults; following them into ~/Documents from a test build
    /// (a new app to macOS) put "Maxie would like to access files in your Documents folder" on the user's screen.
    func testUnitTestsNeverFollowTheUsersDefaultsIntoTheirFolders() {
        XCTAssertFalse(MaxieLook.readsUserFolders(.standard), "a unit test with the user's real defaults")
        XCTAssertTrue(MaxieLook.readsUserFolders(.standard, isUnitTest: false), "the real app")
        XCTAssertTrue(MaxieLook.readsUserFolders(defaults), "a test's own defaults point at its own temp folders")
    }

    func testApplySaysWhetherAnythingChanged() {
        XCTAssertTrue(MaxieLook.apply(character: "maxie", loopsDir: "/x", defaults))
        XCTAssertFalse(MaxieLook.apply(character: "maxie", loopsDir: "/x", defaults))
        XCTAssertTrue(MaxieLook.apply(character: "classic", loopsDir: "/x", defaults))
    }

    func testUnknownCharacterFallsBackToMaxie() {
        defaults.set("r2d2", forKey: MaxieLook.characterKey)
        XCTAssertEqual(MaxieLook.character(defaults), .maxie)
    }

    func testMaxieLoopsAreRoundAndGlossyEyed() throws {
        let lib = MascotLibrary(root: { self.assets.appending(path: "mascot") })
        XCTAssertEqual(lib.slotSize, CGSize(width: 36, height: 42))
        let idle = try XCTUnwrap(lib.loop(.idle))
        XCTAssertEqual(idle.meta.eye_style, "glossy")
        for state in BotState.allCases {
            XCTAssertNotNil(lib.loop(state, liveEyes: false), "Maxie has a loop for \(state)")
        }
    }

    func testClassicBorrowsLoopsForTheNewStates() throws {
        let lib = MascotLibrary(root: { self.assets.appending(path: "mascot-classic") })
        XCTAssertEqual(lib.slotSize, CGSize(width: 30, height: 50))
        XCTAssertEqual(try XCTUnwrap(lib.loop(.wave, liveEyes: false)).meta.state, "happy")
        XCTAssertEqual(try XCTUnwrap(lib.loop(.closed, liveEyes: false)).meta.state, "sleep")
    }

    func testNoLoopsMeansTheClassicSlot() {
        XCTAssertEqual(MascotLibrary(root: { nil }).slotSize, MascotLibrary.classicSize)
    }


    func testGlossyHighlightsSitInsideTheEye() {
        let eye = CGRect(x: 10, y: 10, width: 8, height: 9)
        for (dx, dy, k) in GlossyEye.highlights {
            XCTAssertTrue(eye.contains(GlossyEye.spot(in: eye, dx: dx, dy: dy, k: k)))
        }
    }
}

@MainActor
final class GlyphTests: XCTestCase {
    private func ink(_ img: NSImage) -> Int {
        guard let tiff = img.tiffRepresentation, let rep = NSBitmapImageRep(data: tiff) else { return 0 }
        var n = 0
        for x in 0..<rep.pixelsWide { for y in 0..<rep.pixelsHigh where (rep.colorAt(x: x, y: y)?.alphaComponent ?? 0) > 0.3 { n += 1 } }
        return n
    }

    func testMaxieGlyphIsATemplateAndNotTheClassicRobot() {
        let maxie = BotGlyph.image(classic: false), classic = BotGlyph.image(classic: true)
        XCTAssertTrue(maxie.isTemplate)
        XCTAssertGreaterThan(ink(maxie), 40)
        XCTAssertNotEqual(maxie.tiffRepresentation, classic.tiffRepresentation)
    }

    func testAsleepClosesTheEyes() {
        let awake = BotGlyph.image(classic: false), asleep = BotGlyph.image(asleep: true, classic: false)
        XCTAssertNotEqual(awake.tiffRepresentation, asleep.tiffRepresentation)
        XCTAssertGreaterThan(ink(asleep), 40)
    }

    func testFreeScaleFollowsSize() {
        let d = UserDefaults(suiteName: "glyph.tests.\(UUID().uuidString)")!
        XCTAssertEqual(MaxieLook.freeScale(d), 1.5)
        MaxieLook.apply(character: "maxie", loopsDir: nil, size: "large", d)
        XCTAssertEqual(MaxieLook.freeScale(d), 1.8)
    }
}

/// Which threads read a folder (the reads happen on a background task).
private final class Reads: @unchecked Sendable {
    private let lock = NSLock()
    private var first: Bool?
    private var n = 0

    func add(main isMain: Bool) { lock.withLock { first = first ?? isMain; n += 1 } }
    var firstOnMain: Bool? { lock.withLock { first } }
    var count: Int { lock.withLock { n } }
}
