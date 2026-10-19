import SwiftUI
import WebKit

/// The live bot: the same web character as the phone (web/bot.html, served by the hub), not a rewrite.
/// Maxie by default, the Classic robot when chosen; transparent and capped by the page's fps.
/// It never takes a click or focus from the controls around it, talks to the page only when the
/// state or look really changed, and stops drawing whenever its window is hidden or covered.
struct LiveBotView: NSViewRepresentable {
    let base: URL
    let state: BotState
    var character = MaxieLook.character().rawValue
    var look: [String: String] = [:]
    var framing = "body"
    var fps = 30

    final class Coordinator { var character = ""; var look: [String: String] = [:]; var state = "" }

    func makeCoordinator() -> Coordinator { Coordinator() }

    func makeNSView(context: Context) -> BotWebView {
        let web = BotWebView(frame: .zero, configuration: WKWebViewConfiguration())
        web.setValue(false, forKey: "drawsBackground")  // transparent
        load(web, context.coordinator)
        return web
    }

    func updateNSView(_ web: BotWebView, context: Context) {
        let c = context.coordinator
        if c.character != character {
            load(web, c)  // a different character: a fresh page
            return
        }
        if c.look != look, let json = Self.json(look) {
            c.look = look
            web.evaluateJavaScript("window.bot && bot.setCustom && bot.setCustom(\(json))")
        }
        if c.state != state.rawValue {
            c.state = state.rawValue
            web.evaluateJavaScript("window.bot && bot.setState('\(state.rawValue)')")
        }
    }

    private func load(_ web: WKWebView, _ c: Coordinator) {
        c.character = character
        c.look = look
        c.state = state.rawValue
        var url = URLComponents(url: base.appending(path: "/bot.html"), resolvingAgainstBaseURL: false)!
        url.queryItems = [.init(name: "framing", value: framing), .init(name: "fps", value: String(fps)),
                          .init(name: "state", value: state.rawValue), .init(name: "character", value: character),
                          .init(name: "interactive", value: "0"), .init(name: "custom", value: Self.json(look) ?? "{}")]
        web.load(URLRequest(url: url.url!))
    }

    static func json(_ look: [String: String]) -> String? {
        (try? JSONSerialization.data(withJSONObject: look, options: [.sortedKeys])).flatMap { String(data: $0, encoding: .utf8) }
    }

    static func dismantleNSView(_ web: BotWebView, coordinator: Coordinator) {
        web.evaluateJavaScript("window.bot && bot.dispose()")
        web.stopLoading()
    }
}

/// Maxie's web view: see-through to the mouse (clicks reach the SwiftUI controls and drag strips around it),
/// never first responder, and paused while its window is off screen, hidden or fully covered.
final class BotWebView: WKWebView {
    private var occlusion: NSObjectProtocol?

    override func hitTest(_ point: NSPoint) -> NSView? { nil }
    override var acceptsFirstResponder: Bool { false }

    override func viewDidMoveToWindow() {
        super.viewDidMoveToWindow()
        if let occlusion { NotificationCenter.default.removeObserver(occlusion) }
        occlusion = nil
        guard let window else { return }
        occlusion = NotificationCenter.default.addObserver(forName: NSWindow.didChangeOcclusionStateNotification, object: window,
                                                           queue: .main) { [weak self] n in
            let visible = (n.object as? NSWindow)?.occlusionState.contains(.visible) ?? false
            MainActor.assumeIsolated { self?.setDrawing(visible) }
        }
    }

    func setDrawing(_ on: Bool) {
        evaluateJavaScript(on ? "window.bot && bot.start()" : "window.bot && bot.stop()")
    }
}
