import AppKit
import SwiftUI

// Where the pill may sit: always fully on the screen it was dropped on. Pure, for the selftest.
enum PillPlacement {
    static func clamp(_ origin: NSPoint, size: NSSize, in frame: NSRect) -> NSPoint {
        NSPoint(x: min(max(origin.x, frame.minX), frame.maxX - size.width),
                y: min(max(origin.y, frame.minY), frame.maxY - size.height))
    }

    static func defaultOrigin(size: NSSize, in frame: NSRect) -> NSPoint {
        NSPoint(x: frame.midX - size.width / 2, y: frame.maxY - size.height - 14)
    }
}

// A panel that moves when dragged anywhere on it. SwiftUI views swallow mouse-downs, so
// isMovableByWindowBackground alone never fires; the panel starts the drag itself.
final class DragPanel: NSPanel {
    override func sendEvent(_ event: NSEvent) {
        if event.type == .leftMouseDown {
            performDrag(with: event)
            return
        }
        super.sendEvent(event)
    }
}

// The floating pill: a small glass capsule that floats above every app on every Space, shows
// what Evie is doing, and stays wherever Isaac drags it. It never takes focus: clicking or
// dragging it doesn't pull you out of the app you're in.
@MainActor
final class PillController {
    private var panel: NSPanel?
    private var moveObserver: NSObjectProtocol?
    private static let key = "pillOrigin"
    static let size = NSSize(width: 400, height: 60)

    func show(_ model: AppModel) {
        if let p = panel {
            p.orderFrontRegardless()
            return
        }
        // FIXED size. Letting SwiftUI resize the window to fit the text caused an endless
        // resize -> layout -> resize loop that crashed the app (2026-09-23). The capsule sits at
        // the left of a transparent window instead; transparent pixels click through.
        let host = NSHostingView(rootView: PillView(model: model)
            .frame(width: Self.size.width, height: Self.size.height, alignment: .leading))
        host.frame = NSRect(origin: .zero, size: Self.size)
        host.sizingOptions = []  // never let SwiftUI touch the window size (that was the crash)
        let p = DragPanel(contentRect: host.frame, styleMask: [.borderless, .nonactivatingPanel],
                          backing: .buffered, defer: false)
        p.contentView = host
        p.isFloatingPanel = true
        p.level = .floating
        p.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary, .ignoresCycle]
        p.backgroundColor = .clear
        p.isOpaque = false
        p.hasShadow = false
        p.hidesOnDeactivate = false
        p.setFrameOrigin(savedOrigin(size: p.frame.size))
        moveObserver = NotificationCenter.default.addObserver(forName: NSWindow.didMoveNotification, object: p,
                                                              queue: .main) { [weak p] _ in
            guard let o = p?.frame.origin else { return }
            UserDefaults.standard.set([o.x, o.y], forKey: PillController.key)
        }
        p.orderFrontRegardless()
        panel = p
    }

    func hide() {
        panel?.orderOut(nil)
    }

    private func savedOrigin(size: NSSize) -> NSPoint {
        let screen = NSScreen.main?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1440, height: 900)
        if let xy = UserDefaults.standard.array(forKey: Self.key) as? [Double], xy.count == 2 {
            let frame = NSScreen.screens.first { $0.frame.contains(NSPoint(x: xy[0], y: xy[1])) }?.visibleFrame ?? screen
            return PillPlacement.clamp(NSPoint(x: xy[0], y: xy[1]), size: size, in: frame)
        }
        return PillPlacement.defaultOrigin(size: size, in: screen)
    }
}

struct PillView: View {
    @ObservedObject var model: AppModel
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        HStack(spacing: 8) {
            Orb(state: model.online ? model.state : "offline", animate: !reduceMotion, size: 30)
            if let line = model.pillLine {
                Text(line)
                    .font(.callout.weight(.medium))
                    .lineLimit(1)
                    .truncationMode(.tail)
                    .frame(maxWidth: 320, alignment: .leading)
                    .transition(.opacity)
                    .contentTransition(.opacity)
            }
        }
        .padding(5)
        .padding(.trailing, model.pillLine == nil ? 0 : 9)
        .glassEffect(.regular.interactive(), in: Capsule())
        .padding(10)  // room for the glass edge inside the transparent window
        .animation(reduceMotion ? nil : .spring(response: 0.35, dampingFraction: 1.0), value: model.pillLine)
        .help("Evie. Drag to move.")
    }
}
