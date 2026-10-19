import AppKit

/// The notch bot as plain Core Animation. The loop's frames and each eye's place in every frame are
/// keyframe animations that the render server plays on its own, so an idle Maxie doesn't wake the app 30 times
/// a second. The app only wakes to start a blink (every 2-5.5 s) and to nudge the eyes toward where the user works,
/// leaning with them (a few times a second, and only when the nudge changed). The model layers hold frame 0, so a still capture
/// (and Reduce Motion) shows the first frame.
final class MascotLayerView: NSView {
    private let body = CALayer()  // all of him: leans toward where the user works, from his feet
    private let frameLayer = CALayer()
    private let look = CALayer()  // both eyes: nudged toward the cursor
    private let holders = [CALayer(), CALayer()]  // each eye's place and size, per frame
    private let lids = [CALayer(), CALayer()]  // blinks
    private let eyes = [CALayer(), CALayer()]
    private let shines = [[CALayer(), CALayer()], [CALayer(), CALayer()]]  // Maxie's eye highlights
    private let irises = [CALayer(), CALayer()]  // and the warm glow low in each eye
    private var glossy = false
    private var loop: MascotLoop?
    private var cgFrames: [CGImage] = []
    private var cursorTimer: Timer?
    private var blinkTimer: Timer?
    private var lastLook = CGSize.zero
    private(set) var lean: Double = 0  // degrees, + is to his right
    private var observers: [NSObjectProtocol] = []

    static let loopKey = "maxie.loop"
    static let eyeKey = "maxie.eye"
    static let cursorHz = 8.0

    var state: BotState = .idle { didSet { if state != oldValue { reload() } } }
    /// 1 at the notch (the slot size, 2x frames); 1.5 outside it (3x frames, still crisp).
    var scale: CGFloat = 1 { didSet { if scale != oldValue { reload() } } }
    var reduceMotion = false { didSet { if reduceMotion != oldValue { restart() } } }
    var followCursor: Bool { state != .think }

    override var isFlipped: Bool { true }  // eye anchors are top-left based, like the PNGs

    // For tests: what is playing.
    var loopAnimation: CAKeyframeAnimation? { frameLayer.animation(forKey: Self.loopKey) as? CAKeyframeAnimation }
    var eyeAnimations: [CAKeyframeAnimation] { holders.compactMap { $0.animation(forKey: Self.eyeKey) as? CAKeyframeAnimation } }
    var ticksEveryFrame: Bool { false }
    var firstFrameShown: CGImage? { frameLayer.contents.map { $0 as! CGImage } }

    override init(frame: NSRect) {
        super.init(frame: frame)
        wantsLayer = true
        layer?.masksToBounds = false
        frameLayer.contentsGravity = .resize
        frameLayer.contentsScale = 2
        frameLayer.magnificationFilter = .trilinear
        frameLayer.minificationFilter = .trilinear
        body.anchorPoint = CGPoint(x: 0.5, y: 1)  // the view is flipped: his feet
        layer?.addSublayer(body)
        body.addSublayer(frameLayer)
        body.addSublayer(look)
        for n in 0..<2 {
            let (holder, lid, eye) = (holders[n], lids[n], eyes[n])
            look.addSublayer(holder)
            holder.addSublayer(lid)
            lid.addSublayer(eye)
            eye.shadowRadius = 1.2
            eye.shadowOffset = .zero
            irises[n].backgroundColor = NSColor(GlossyEye.irisColor).withAlphaComponent(GlossyEye.irisOpacity).cgColor
            eye.addSublayer(irises[n])
            for h in shines[n] {
                h.backgroundColor = NSColor.white.cgColor
                eye.addSublayer(h)
            }
        }
        let center = NSWorkspace.shared.notificationCenter
        observers = [
            center.addObserver(forName: NSWorkspace.screensDidSleepNotification, object: nil, queue: .main) { [weak self] _ in
                MainActor.assumeIsolated { self?.stopTimers() }
            },
            center.addObserver(forName: NSWorkspace.screensDidWakeNotification, object: nil, queue: .main) { [weak self] _ in
                MainActor.assumeIsolated { self?.restart() }
            },
        ]
        NotificationCenter.default.addObserver(forName: .mascotChanged, object: nil, queue: .main) { [weak self] _ in
            MainActor.assumeIsolated { self?.reload() }  // character or look changed (Settings › Maxie)
        }
        reload()
    }

    required init?(coder: NSCoder) { fatalError("not used") }

    override func layout() {
        super.layout()
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        body.bounds = bounds
        body.position = CGPoint(x: bounds.midX, y: bounds.maxY)
        frameLayer.frame = body.bounds
        look.frame = body.bounds
        CATransaction.commit()
    }

    private func reload() {
        if !followCursor { follow(.zero) }  // working: eyes on the task, upright
        let px = scale > 1.25 ? 3 : 2
        guard let loop = MascotLibrary.shared.loop(state, px: px) else { return }
        self.loop = loop
        cgFrames = loop.images  // decoded once by MascotLibrary
        let color = NSColor(Color(hex: loop.meta.eye_color ?? "#FFC48A")).cgColor
        glossy = loop.meta.eye_style == "glossy"
        for n in 0..<2 {
            eyes[n].backgroundColor = color
            eyes[n].shadowColor = color
            eyes[n].shadowOpacity = glossy ? 0 : 0.9
            holders[n].isHidden = !loop.liveEyes
            shines[n].forEach { $0.isHidden = !glossy }
            irises[n].isHidden = !glossy
        }
        restart()
    }

    /// The eye rects (points, top-left) in every frame of the loop.
    private func eyeRects(_ loop: MascotLoop) -> [[CGRect]] {
        guard loop.liveEyes, let list = loop.meta.eye_anchors_2x, !list.isEmpty else { return [] }
        return list.map { f in
            [f.left, f.right].map { a in
                let w = a.w / 2 * scale, h = max(0.6, a.h / 2 * scale)
                return CGRect(x: a.x / 2 * scale - w / 2, y: a.y / 2 * scale - h / 2, width: w, height: h)
            }
        }
    }

    private func restart() {
        stopTimers()
        CATransaction.begin()
        CATransaction.setDisableActions(true)
        frameLayer.removeAnimation(forKey: Self.loopKey)
        holders.forEach { $0.removeAnimation(forKey: Self.eyeKey) }
        guard let loop, let first = cgFrames.first else { CATransaction.commit(); return }
        frameLayer.contents = first
        let rects = eyeRects(loop)
        if let first = rects.first { placeEyes(first) }
        let animate = !reduceMotion && cgFrames.count > 1
        if animate {
            let duration = Double(cgFrames.count) / Double(max(loop.meta.fps, 1))
            frameLayer.add(Self.keyframes("contents", cgFrames, duration), forKey: Self.loopKey)
            if rects.count == cgFrames.count {
                for n in 0..<2 {
                    let base = rects[0][n]
                    let values = rects.map { r in
                        CATransform3DConcat(CATransform3DMakeScale(r[n].width / max(base.width, 0.01), r[n].height / max(base.height, 0.01), 1),
                                            CATransform3DMakeTranslation(r[n].midX - base.midX, r[n].midY - base.midY, 0))
                    }
                    holders[n].add(Self.keyframes("transform", values.map { NSValue(caTransform3D: $0) }, duration), forKey: Self.eyeKey)
                }
            }
        }
        CATransaction.commit()
        guard animate, window != nil, loop.liveEyes else { return }
        scheduleBlink()
        if followCursor { startCursor() }
    }

    /// Frame 0's eyes: holder centred on the eye, the lid and the eye filling it.
    private func placeEyes(_ rects: [CGRect]) {
        for n in 0..<2 {
            let r = rects[n]
            holders[n].bounds = CGRect(origin: .zero, size: r.size)
            holders[n].position = CGPoint(x: r.midX, y: r.midY)
            holders[n].transform = CATransform3DIdentity
            lids[n].frame = holders[n].bounds
            eyes[n].frame = lids[n].bounds
            eyes[n].cornerRadius = min(r.width, r.height) / 2
            guard glossy else { continue }
            let local = eyes[n].bounds
            irises[n].frame = GlossyEye.iris(in: local)
            irises[n].cornerRadius = min(irises[n].frame.width, irises[n].frame.height) / 2
            for (shine, spec) in zip(shines[n], GlossyEye.highlights) {
                shine.frame = GlossyEye.spot(in: local, dx: spec.0, dy: spec.1, k: spec.2)
                shine.cornerRadius = shine.frame.width / 2
            }
        }
    }

    static func keyframes(_ keyPath: String, _ values: [Any], _ duration: Double) -> CAKeyframeAnimation {
        let a = CAKeyframeAnimation(keyPath: keyPath)
        a.values = values
        a.calculationMode = .discrete
        a.duration = duration
        a.repeatCount = .infinity
        a.isRemovedOnCompletion = false
        return a
    }

    // MARK: - Blinks and glances: the only times the app wakes

    private func scheduleBlink() {
        blinkTimer?.invalidate()
        let wait = Double.random(in: 0...1) < 0.2 ? 0.22 : Double.random(in: 2...5.5)
        let t = Timer(timeInterval: wait, repeats: false) { [weak self] _ in
            MainActor.assumeIsolated { self?.blink() }
        }
        t.tolerance = 0.1
        RunLoop.main.add(t, forMode: .common)
        blinkTimer = t
    }

    private func blink() {
        let h = holders[0].bounds.height
        let floor = h > 0 ? min(1, 0.6 / h) : 0.1  // never thinner than 0.6 pt, like before
        let a = CAKeyframeAnimation(keyPath: "transform.scale.y")
        a.values = (0...6).map { i in max(floor, abs(cos(Double(i) / 6 * .pi))) }  // open, shut, open: |cos|
        a.duration = 0.13
        lids.forEach { $0.add(a, forKey: "blink") }
        scheduleBlink()
    }

    private func startCursor() {
        cursorTimer?.invalidate()
        let t = Timer(timeInterval: 1 / Self.cursorHz, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.glance() }
        }
        t.tolerance = 0.05
        RunLoop.main.add(t, forMode: .common)
        cursorTimer = t
        glance()
    }

    private func glance() {
        let o = CursorLook.offset()
        guard abs(o.width - lastLook.width) > 0.03 || abs(o.height - lastLook.height) > 0.03 else { return }
        follow(o)
    }

    /// The eyes' nudge toward where the user works (points at the notch's size), and the lean that goes with it.
    func follow(_ o: CGSize) {
        lastLook = o
        pose(look: o, lean: Alive.tilt(look: CGSize(width: o.width / CursorLook.reach, height: 0), reduce: reduceMotion))
    }

    func pose(look o: CGSize, lean degrees: Double) {
        lean = degrees
        CATransaction.begin()
        CATransaction.setAnimationDuration(1 / Self.cursorHz)  // glide between nudges
        look.transform = CATransform3DMakeTranslation(o.width * scale, o.height * scale, 0)
        body.transform = CATransform3DMakeRotation(degrees * .pi / 180, 0, 0, 1)  // flipped: + turns clockwise
        CATransaction.commit()
    }

    private func stopTimers() {
        blinkTimer?.invalidate()
        blinkTimer = nil
        cursorTimer?.invalidate()
        cursorTimer = nil
    }

    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        // No deinit needed: the timers stop here when the view leaves its window (weak self inside them).
        if window == nil { stopTimers() } else { restart() }
    }
}

import SwiftUI

/// SwiftUI wrapper used by the notch.
struct MascotLayer: NSViewRepresentable {
    let state: BotState
    var scale: CGFloat = 1
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    func makeNSView(context: Context) -> MascotLayerView {
        let size = MascotLibrary.shared.slotSize
        let v = MascotLayerView(frame: NSRect(x: 0, y: 0, width: size.width * scale, height: size.height * scale))
        v.scale = scale
        v.state = state
        v.reduceMotion = reduceMotion
        return v
    }

    func updateNSView(_ v: MascotLayerView, context: Context) {
        v.scale = scale
        v.state = state
        v.reduceMotion = reduceMotion
    }
}
