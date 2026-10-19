import AppKit

/// The pre-rendered loops (assets/mascot: Maxie, 36x42 pt; assets/mascot-classic: the Classic robot, 30x50 pt), one
/// seamless loop per state.
/// Round-eye states also ship eyeless frames + per-frame eye anchors so the face layer can draw live eyes.
struct MascotLoop: @unchecked Sendable {  // immutable once loaded; NSImage/CGImage are only read
    struct Eye: Decodable { let x, y, w, h: Double }
    struct Anchors: Decodable { let left, right: Eye }
    struct Meta: Decodable {
        var state: String? = nil
        let fps: Int
        let frames: Int
        let size_pt: [Double]
        let eye_anchors_2x: [Anchors]?
        let eye_color: String?
        /// "glossy": Maxie's dark eyes with two highlights; nil/"glow": the Classic robot's glowing screen eyes.
        var eye_style: String? = nil
    }

    let meta: Meta
    let frames: [NSImage]
    /// The same frames, decoded once: views use these as they are, nothing re-decodes per view.
    var images: [CGImage] = []
    /// True when `frames` are the eyeless ones and the app draws the eyes.
    let liveEyes: Bool
    /// Pixels per point the frames were rendered at (2 or 3).
    var px: Int = 2

    var size: CGSize { CGSize(width: meta.size_pt[0], height: meta.size_pt[1]) }

    func frame(at date: Date) -> Int {
        Int(date.timeIntervalSinceReferenceDate * Double(meta.fps)) % max(meta.frames, 1)
    }
}

@MainActor
final class MascotLibrary {
    static let shared = MascotLibrary()
    typealias Reader = @Sendable (URL?, BotState, Bool, Int) -> MascotLoop?
    private var cache: [String: MascotLoop] = [:]
    private let rootURL: () -> URL?
    private let fallbackURL: () -> URL?
    private let bundle: URL?
    private let reader: Reader
    /// His look's folders read in the background and found complete; only these are read on the main thread.
    private var checked: Set<URL> = []
    /// Being read, or found incomplete (until his look changes).
    private var pending: Set<URL> = []

    init(root: @escaping () -> URL? = { MaxieLook.loopsRoot() }, fallback: @escaping () -> URL? = { MaxieLook.bundledRoot() },
         bundle: URL? = Bundle.main.resourceURL, read: @escaping Reader = { MascotLibrary.read(root: $0, state: $1, liveEyes: $2, px: $3) }) {
        rootURL = root
        fallbackURL = fallback
        self.bundle = bundle
        reader = read
    }

    /// Where loops are read from now. The app's own folder right away; a custom one (maybe in ~/Documents) only once a background
    /// read found it complete. The main thread used to read it first, and with a new code signature that read
    /// waited for the user's answer to "Maxie would like to access your Documents folder": "Maxie is not responding".
    /// Until then, the bundled look.
    private var root: URL? {
        guard let want = rootURL() else { return nil }
        if Self.isInside(want, bundle) || checked.contains(want) { return want }
        check(want)
        return fallbackURL()
    }

    private func check(_ url: URL) {
        guard !pending.contains(url) else { return }
        pending.insert(url)
        let read = reader
        Task.detached(priority: .utility) {
            guard let idle = read(url, .idle, true, 2) else { return }  // incomplete or refused: the bundled look stays
            await MainActor.run {
                self.pending.remove(url)
                self.checked.insert(url)
                self.forget(keeping: [Self.key(.idle, true, 2): idle])
            }
        }
    }

    nonisolated static func isInside(_ url: URL, _ folder: URL?) -> Bool {
        guard let folder else { return false }
        return url.standardizedFileURL.path.hasPrefix(folder.standardizedFileURL.path + "/")
    }

    static let classicSize = CGSize(width: 30, height: 50)

    /// States the Classic robot has no loop for borrow a close one.
    static let fallback: [BotState: BotState] = [.wave: .happy, .closed: .sleep, .stretch: .happy]

    /// Maxie's size in points (the active character's loops say; Maxie is rounder than the Classic robot).
    var slotSize: CGSize { loop(.idle)?.size ?? Self.classicSize }

    /// Forget every loaded loop (character or look changed) and tell the views showing Maxie.
    func reset() {
        pending.removeAll()
        forget()
    }

    private func forget(keeping loaded: [String: MascotLoop] = [:]) {
        generation += 1
        cache = loaded
        preloading.removeAll()
        NotificationCenter.default.post(name: .mascotChanged, object: nil)
    }

    /// Loads (once) the loop for a state at 2x (the notch) or 3x (the bigger bot outside it).
    /// Prefers eyeless frames so the eyes can follow the cursor.
    func loop(_ state: BotState, liveEyes: Bool = true, px: Int = 2) -> MascotLoop? {
        let key = Self.key(state, liveEyes, px)
        if let hit = cache[key] { return hit }
        guard let read = reader(root, state, liveEyes, px) else {
            return Self.fallback[state].flatMap { loop($0, liveEyes: liveEyes, px: px) }
        }
        cache[key] = read
        preload(px: px)  // the other states, off the main thread, so a state change never decodes on it
        return read
    }

    private var preloading: Set<Int> = []
    /// Bumped whenever the loops are forgotten, so a preload from before never lands after.
    private var generation = 0

    /// Decodes every state's loop at `px` in the background (once per look); loop(_:) then never touches disk.
    func preload(px: Int) {
        guard !preloading.contains(px) else { return }
        preloading.insert(px)
        let root = root, read = reader, started = generation
        let missing = BotState.allCases.filter { cache[Self.key($0, true, px)] == nil }
        Task.detached(priority: .utility) {
            let loaded = missing.compactMap { s in read(root, s, true, px).map { (s, $0) } }
            await MainActor.run {
                guard self.generation == started else { return }
                for (s, l) in loaded where self.cache[Self.key(s, true, px)] == nil { self.cache[Self.key(s, true, px)] = l }
            }
        }
    }

    nonisolated static func key(_ state: BotState, _ liveEyes: Bool, _ px: Int) -> String {
        "\(state.rawValue)@\(px)\(liveEyes ? "live" : "")"
    }

    /// Reads and decodes one loop from disk (any thread).
    nonisolated static func read(root: URL?, state: BotState, liveEyes: Bool, px: Int) -> MascotLoop? {
        guard let dir = root?.appending(path: state.rawValue),
              let data = try? Data(contentsOf: dir.appending(path: "meta.json")),
              let meta = try? JSONDecoder().decode(MascotLoop.Meta.self, from: data) else { return nil }
        let bare = dir.appending(path: "noeyes/\(px)x")
        let useLive = liveEyes && meta.eye_anchors_2x != nil && FileManager.default.fileExists(atPath: bare.path)
        let folder = useLive ? bare : dir.appending(path: "\(px)x")
        let size = CGSize(width: meta.size_pt[0], height: meta.size_pt[1])  // 2x pixels -> points
        let images: [CGImage] = (0..<meta.frames).compactMap { i in decoded(folder.appending(path: String(format: "%03d.png", i))) }
        guard images.count == meta.frames else { return nil }
        let frames = images.map { NSImage(cgImage: $0, size: size) }
        return MascotLoop(meta: meta, frames: frames, images: images, liveEyes: useLive, px: px)
    }

    /// A PNG decoded now, not on first draw.
    nonisolated static func decoded(_ url: URL) -> CGImage? {
        guard let src = CGImageSourceCreateWithURL(url as CFURL, nil) else { return nil }
        return CGImageSourceCreateImageAtIndex(src, 0, [kCGImageSourceShouldCacheImmediately: true] as CFDictionary)
    }
}
