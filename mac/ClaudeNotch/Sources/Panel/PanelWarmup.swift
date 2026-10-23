import AppKit
import SwiftUI

/// the first chat panel after launch took ~350 ms to build (it starts WebKit for the live Maxie view) and
/// later ones ~60 ms. A few seconds after launch the panel is built once in an invisible, click-through window far
/// off every screen and thrown away, so the first "Hey Maxie" opens as fast as the rest. Nothing is ever shown.
@MainActor
enum PanelWarmup {
    static let after: Duration = .seconds(4)
    static let keep: Duration = .seconds(3)

    static func run(hub: HubClient) {
        Task { @MainActor in
            try? await Task.sleep(for: after)
            let view = PanelView(hub: hub, onClose: {}, onSettings: { _ in }, onPin: { _ in }, onQuit: {},
                                 onResize: { _ in }, onResizeEnd: {}, source: .top)
                .frame(width: PanelLayout.width, height: PanelLayout.defaultSize.height)
            let w = NotchController.makeChatPanel(CGRect(origin: CGPoint(x: -40_000, y: -40_000), size: PanelLayout.defaultSize))
            w.alphaValue = 0
            w.ignoresMouseEvents = true
            w.contentView = NSHostingView(rootView: view)
            w.orderBack(nil)
            try? await Task.sleep(for: keep)
            NotchController.discard(w)
        }
    }
}
