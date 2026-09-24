import AppKit
import SwiftUI

// Evie's floating orb: a 44 pt glass circle that shows what she's doing, and a bubble that grows
// out of it with what she heard, what she said and what she's working on (plus a Stop button).
//
// Why it's built like this (2026-09-24): the old pill crashed the app twice. Both crashes were
// inside SwiftUI's own mouse handling (hover dispatch, window auto-sizing). So here AppKit owns
// every mouse event: the panels handle clicks, drags and right-clicks themselves, the SwiftUI
// views never receive a single mouse event (InertHostingView), and no window ever changes size.
// The orb and the bubble are two separate fixed-size panels, so flipping sides never re-lays out
// a window, and nothing invisible sits over Isaac's apps while the bubble is closed.

enum OrbSide: String { case left, right }

// MARK: - Geometry (pure: the selftest checks it)

enum OrbGeometry {
    static let orb: CGFloat = 44
    static let pad: CGFloat = 12  // room for the glass edge and shadow
    static let gap: CGFloat = 8  // between the orb and the bubble
    static let capsuleWidth: CGFloat = 340
    static let orbPanel = NSSize(width: orb + 2 * pad, height: orb + 2 * pad)
    static let capsulePanel = NSSize(width: capsuleWidth + 12, height: 104)
    static let stop: CGFloat = 24

    /// The orb panel's frame for an orb centred at `center` (screen coordinates).
    static func orbFrame(center: CGPoint) -> NSRect {
        NSRect(x: center.x - orbPanel.width / 2, y: center.y - orbPanel.height / 2,
               width: orbPanel.width, height: orbPanel.height)
    }

    /// The bubble grows away from the screen edge the orb is snapped to.
    static func capsuleFrame(orbCenter c: CGPoint, side: OrbSide) -> NSRect {
        let x = side == .left ? c.x + orb / 2 + gap - 6 : c.x - orb / 2 - gap - capsulePanel.width + 6
        return NSRect(x: x, y: c.y - capsulePanel.height / 2, width: capsulePanel.width, height: capsulePanel.height)
    }

    /// Stop button, in the bubble panel's coordinates: at the far end, level with the orb.
    static func stopRect(_ side: OrbSide) -> NSRect {
        let inset: CGFloat = 6 + 10
        let x = side == .left ? capsulePanel.width - inset - stop : inset
        return NSRect(x: x, y: (capsulePanel.height - stop) / 2, width: stop, height: stop)
    }
}

// MARK: - Motion (pure)

enum OrbSnap {
    /// Where momentum carries a thrown object (Apple's deceleration projection).
    static func project(_ v: CGFloat, rate: CGFloat = 0.998) -> CGFloat { v / 1000 * rate / (1 - rate) }

    /// Where the orb comes to rest after a drag: the side edge nearer to where the throw was
    /// heading, height kept (clamped on screen).
    static func rest(center: CGPoint, velocity: CGVector, in frame: NSRect,
                     margin: CGFloat = 16) -> (side: OrbSide, center: CGPoint) {
        let r = OrbGeometry.orb / 2
        let px = center.x + project(velocity.dx), py = center.y + project(velocity.dy)
        let side: OrbSide = px < frame.midX ? .left : .right
        let x = side == .left ? frame.minX + margin + r : frame.maxX - margin - r
        let y = min(max(py, frame.minY + margin + r), frame.maxY - margin - r)
        return (side, CGPoint(x: x, y: y))
    }
}

/// A critically damped spring (damping 1.0): fast, and it never overshoots.
struct Spring {
    var response: Double = 0.35
    var damping: Double = 1.0

    func step(x: CGFloat, v: CGFloat, target: CGFloat, dt: Double) -> (CGFloat, CGFloat) {
        let w = 2 * Double.pi / response, k = w * w, c = 2 * damping * w
        var x = Double(x), v = Double(v)
        let n = 8, h = dt / Double(n)
        for _ in 0..<n {  // semi-implicit Euler in small steps: stable at 60-120 Hz
            v += (-k * (x - Double(target)) - c * v) * h
            x += v * h
        }
        return (CGFloat(x), CGFloat(v))
    }
}

// MARK: - Look (pure)

/// Each state is a palette of liquid light inside the glass, plus how the mark moves.
struct OrbLook: Equatable {
    enum Motion: Equatable { case still, level, spin, progress }
    let motion: Motion
    let palette: [Color]  // 3 colours: the liquid inside the orb
    let dashed: Bool

    var tint: Color { palette[1] }

    private static func rgb(_ r: Double, _ g: Double, _ b: Double) -> Color { Color(red: r, green: g, blue: b) }

    static func of(state: String, online: Bool) -> OrbLook {
        guard online else {
            return OrbLook(motion: .still, palette: [rgb(0.45, 0.47, 0.52), rgb(0.58, 0.6, 0.64), rgb(0.36, 0.38, 0.42)], dashed: true)
        }
        switch state {
        case "listening":  // warm: she's all ears
            return OrbLook(motion: .level, palette: [rgb(1.0, 0.33, 0.42), rgb(1.0, 0.52, 0.36), rgb(0.98, 0.24, 0.62)], dashed: false)
        case "thinking":
            return OrbLook(motion: .spin, palette: [rgb(1.0, 0.7, 0.24), rgb(1.0, 0.45, 0.45), rgb(0.95, 0.32, 0.7)], dashed: false)
        case "speaking":  // cool: her voice
            return OrbLook(motion: .level, palette: [rgb(0.2, 0.78, 1.0), rgb(0.26, 0.45, 1.0), rgb(0.58, 0.36, 1.0)], dashed: false)
        case "working":
            return OrbLook(motion: .progress, palette: [rgb(0.62, 0.36, 1.0), rgb(0.95, 0.35, 0.85), rgb(0.3, 0.45, 1.0)], dashed: false)
        default:  // Evie at rest: indigo into teal
            return OrbLook(motion: .still, palette: [rgb(0.36, 0.33, 0.95), rgb(0.55, 0.38, 0.98), rgb(0.18, 0.72, 0.85)], dashed: false)
        }
    }
}

// MARK: - Views

/// The orb: a Liquid Glass sphere with moving liquid light inside it, and Evie's mark on top (the
/// same ring-around-what-she's-doing idea as the menu bar icon).
struct OrbMarkView: View {
    @ObservedObject var model: AppModel
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        let look = OrbLook.of(state: model.orbState, online: model.online)
        let still = reduceMotion || look.motion == .still
        let level = CGFloat(model.orbState == "speaking" ? max(model.voiceLevel, 0.3) : model.micLevel)
        TimelineView(.animation(minimumInterval: 1.0 / 30, paused: still)) { tl in
            let t = still ? 0 : tl.date.timeIntervalSinceReferenceDate
            ZStack {
                LiquidCore(palette: look.palette, t: t, level: look.motion == .level ? level : 0.15)
                    .clipShape(Circle())
                    .opacity(look.dashed ? 0.55 : 1)
                // Light catching the glass: a bright rim at the top fading out below, and a soft highlight.
                Circle()
                    .strokeBorder(LinearGradient(colors: [.white.opacity(0.55), .white.opacity(0.05)],
                                                 startPoint: .top, endPoint: .bottom), lineWidth: 1)
                Ellipse().fill(LinearGradient(colors: [.white.opacity(0.22), .clear], startPoint: .top, endPoint: .bottom))
                    .frame(width: OrbGeometry.orb * 0.62, height: OrbGeometry.orb * 0.32)
                    .offset(y: -OrbGeometry.orb * 0.26)
                    .blendMode(.plusLighter)
                Canvas { ctx, size in
                    OrbMarkView.drawMark(ctx: ctx, size: size, look: look, t: t, level: level, progress: model.jobProgress)
                }
            }
            .frame(width: OrbGeometry.orb, height: OrbGeometry.orb)
        }
        .glassEffect(.regular.tint(look.tint.opacity(0.25)), in: Circle())
        .shadow(color: look.tint.opacity(model.orbState == "idle" ? 0.25 : 0.5), radius: model.orbState == "idle" ? 5 : 9)
        .overlay(alignment: .topTrailing) {
            if model.earsMode == "live" {  // Live mic on: a small green light, like the Mac's own
                Circle().fill(Color(red: 0.2, green: 0.85, blue: 0.4)).frame(width: 8, height: 8)
                    .overlay(Circle().strokeBorder(.white.opacity(0.9), lineWidth: 1.2))
                    .shadow(color: .green.opacity(0.6), radius: 3)
                    .offset(x: 1, y: -1)
            }
        }
        .animation(reduceMotion ? nil : .spring(response: 0.35, dampingFraction: 1.0), value: model.orbState)
        .accessibilityElement()
        .accessibilityLabel("Evie, \(model.orbState)")
    }

    static func drawMark(ctx: GraphicsContext, size: CGSize, look: OrbLook, t: Double, level: CGFloat,
                         progress: Double?) {
        let c = CGPoint(x: size.width / 2, y: size.height / 2)
        let r = size.width / 2 - 9
        let white = GraphicsContext.Shading.color(.white)
        func bars(_ base: [CGFloat]) {
            let w: CGFloat = 2.4, gap: CGFloat = 2.3
            let total = CGFloat(base.count) * w + CGFloat(base.count - 1) * gap
            for (i, b) in base.enumerated() {
                let wob = 0.5 + 0.5 * sin(t * 9 + Double(i) * 1.7)  // a little life at a steady level
                let h = max(3, min(2 * r - 4, b * (0.3 + 1.3 * level) + CGFloat(wob) * 3 * level))
                let x = c.x - total / 2 + CGFloat(i) * (w + gap)
                ctx.fill(Path(roundedRect: CGRect(x: x, y: c.y - h / 2, width: w, height: h), cornerRadius: w / 2), with: white)
            }
        }
        switch look.motion {
        case .still:
            let ring = Path(ellipseIn: CGRect(x: c.x - r, y: c.y - r, width: 2 * r, height: 2 * r))
            ctx.stroke(ring, with: .color(.white.opacity(0.85)), style: StrokeStyle(lineWidth: 1.6, dash: look.dashed ? [3, 2.6] : []))
            if !look.dashed {  // the pupil: Evie, awake and waiting
                var glow = ctx
                glow.addFilter(.blur(radius: 3))
                glow.fill(Path(ellipseIn: CGRect(x: c.x - 5, y: c.y - 5, width: 10, height: 10)), with: .color(.white.opacity(0.7)))
                ctx.fill(Path(ellipseIn: CGRect(x: c.x - 3, y: c.y - 3, width: 6, height: 6)), with: white)
            }
        case .level:
            bars([7, 13, 19, 13, 7])
        case .spin:  // three lights orbiting, like a thought going round
            for i in 0..<3 {
                let a = t * 2 * .pi / 1.2 + Double(i) * 2 * .pi / 3
                let p = CGPoint(x: c.x + cos(a) * r * 0.62, y: c.y + sin(a) * r * 0.62)
                let s: CGFloat = i == 0 ? 3.4 : 2.6
                ctx.fill(Path(ellipseIn: CGRect(x: p.x - s, y: p.y - s, width: 2 * s, height: 2 * s)), with: white)
            }
        case .progress:
            let rr = size.width / 2 - 3.5  // on the glass rim itself
            let track = Path(ellipseIn: CGRect(x: c.x - rr, y: c.y - rr, width: 2 * rr, height: 2 * rr))
            ctx.stroke(track, with: .color(.white.opacity(0.22)), lineWidth: 2.2)
            var arc = Path()
            if let p = progress {
                arc.addArc(center: c, radius: rr, startAngle: .degrees(-90), endAngle: .degrees(-90 + 360 * max(0.04, p)),
                           clockwise: false)
            } else {  // no step count yet: a short arc going round
                let a = Angle.radians(t * 2 * .pi / 1.6)
                arc.addArc(center: c, radius: rr, startAngle: a, endAngle: a + .degrees(80), clockwise: false)
            }
            ctx.stroke(arc, with: white, style: StrokeStyle(lineWidth: 2.4, lineCap: .round))
            ctx.fill(Path(ellipseIn: CGRect(x: c.x - 3.2, y: c.y - 3.2, width: 6.4, height: 6.4)), with: white)
        }
    }
}

/// Liquid light: a 3x3 mesh gradient whose middle points drift, so the colour flows like liquid
/// inside the glass. It swells with the voice (level).
struct LiquidCore: View {
    let palette: [Color]
    let t: Double
    let level: CGFloat

    var body: some View {
        let a = Float(t * 0.9), k = Float(0.12 + 0.18 * level)
        let mid = SIMD2<Float>(0.5 + k * cos(a), 0.5 + k * sin(a * 1.3))
        let top = SIMD2<Float>(0.5 + 0.2 * sin(a * 0.7), 0)
        let side = SIMD2<Float>(1, 0.5 + 0.2 * cos(a * 0.8))
        let (p0, p1, p2) = (palette[0], palette[1], palette[2])
        MeshGradient(width: 3, height: 3, points: [
            [0, 0], top, [1, 0],
            [0, 0.5], mid, side,
            [0, 1], [0.5, 1], [1, 1],
        ], colors: [
            p0, p1, p2,
            p2, p0, p1,
            p1, p2, p0,
        ])
        .blur(radius: 3)
        .scaleEffect(1.15)
    }
}

/// The bubble: what she heard (so a mishear is visible straight away), what she said, and the
/// task she's on with its progress and a Stop button.
struct OrbBubbleView: View {
    @ObservedObject var model: AppModel
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency

    var body: some View {
        let side = model.orbSide
        let look = OrbLook.of(state: model.orbState, online: model.online)
        HStack(spacing: 11) {
            if side == .right, model.stopVisible { stopButton }
            VStack(alignment: .leading, spacing: 4) {
                if let you = model.youLine {
                    HStack(spacing: 6) {
                        Circle().fill(Color.secondary.opacity(0.6)).frame(width: 5, height: 5)
                        Text(you).font(.system(size: 12)).foregroundStyle(.secondary)
                            .lineLimit(1).truncationMode(.head)
                    }
                }
                if let evie = model.evieLine {
                    Text(evie)
                        .font(.system(size: 13.5, weight: .medium, design: .rounded))
                        .tracking(-0.1)
                        .lineLimit(2).truncationMode(.tail)
                        .fixedSize(horizontal: false, vertical: true)
                        .contentTransition(.opacity)
                }
                if let step = model.stepLine {
                    VStack(alignment: .leading, spacing: 5) {
                        Text(step).font(.system(size: 11.5)).foregroundStyle(.secondary).lineLimit(1)
                        LiquidBar(progress: model.jobProgress, palette: OrbLook.of(state: "working", online: true).palette)
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            if side == .left, model.stopVisible { stopButton }
        }
        .padding(.horizontal, 15)
        .padding(.vertical, 11)
        .frame(width: OrbGeometry.capsuleWidth)
        .background {
            if reduceTransparency {
                RoundedRectangle(cornerRadius: 22, style: .continuous).fill(Color(nsColor: .windowBackgroundColor))
            }
        }
        // The bubble takes a whisper of her current colour, so it reads as part of the orb.
        .glassEffect(.regular.tint(look.tint.opacity(0.08)), in: RoundedRectangle(cornerRadius: 22, style: .continuous))
        .frame(width: OrbGeometry.capsulePanel.width, height: OrbGeometry.capsulePanel.height)
        .animation(reduceMotion ? nil : .spring(response: 0.35, dampingFraction: 1.0), value: model.evieLine)
        .animation(reduceMotion ? nil : .spring(response: 0.35, dampingFraction: 1.0), value: model.stepLine)
    }

    private var stopButton: some View {
        Image(systemName: "stop.fill")
            .font(.system(size: 9.5, weight: .bold))
            .foregroundStyle(.white)
            .frame(width: OrbGeometry.stop, height: OrbGeometry.stop)
            // Solid red under the glass: a tint alone goes grey in a window that never becomes key.
            .background(Circle().fill(LinearGradient(colors: [Color(red: 1.0, green: 0.4, blue: 0.4), Color(red: 0.92, green: 0.2, blue: 0.28)],
                                                     startPoint: .top, endPoint: .bottom)))
            .glassEffect(.regular, in: Circle())
            .accessibilityLabel("Stop")
    }
}

/// A thin progress line of the same liquid light; it drifts when the step count isn't known yet.
struct LiquidBar: View {
    let progress: Double?
    let palette: [Color]

    var body: some View {
        GeometryReader { g in
            ZStack(alignment: .leading) {
                Capsule().fill(Color.secondary.opacity(0.18))
                Capsule()
                    .fill(LinearGradient(colors: palette, startPoint: .leading, endPoint: .trailing))
                    .frame(width: g.size.width * CGFloat(progress ?? 0.25))
                    .shadow(color: palette[1].opacity(0.6), radius: 3)
            }
        }
        .frame(height: 3.5)
    }
}

// MARK: - AppKit: panels that own the mouse

/// A hosting view that never takes part in mouse handling: no hit testing, no tracking areas, no
/// hover dispatch. That's the code path both crashes came from.
final class InertHostingView<Content: View>: NSHostingView<Content> {
    override func hitTest(_ point: NSPoint) -> NSView? { nil }
    override func updateTrackingAreas() {
        super.updateTrackingAreas()
        trackingAreas.forEach(removeTrackingArea)
    }
    override func mouseMoved(with event: NSEvent) {}
    override func mouseEntered(with event: NSEvent) {}
    override func mouseExited(with event: NSEvent) {}
    override var acceptsFirstResponder: Bool { false }
}

/// Holds the SwiftUI content and reports hover over its whole area (AppKit tracking only).
final class HoverContainer: NSView {
    var onHover: ((Bool) -> Void)?
    private var area: NSTrackingArea?

    override func updateTrackingAreas() {
        super.updateTrackingAreas()
        if let a = area { removeTrackingArea(a) }
        let a = NSTrackingArea(rect: bounds.insetBy(dx: OrbGeometry.pad - 2, dy: OrbGeometry.pad - 2),
                               options: [.mouseEnteredAndExited, .activeAlways], owner: self, userInfo: nil)
        addTrackingArea(a)
        area = a
    }
    override func mouseEntered(with event: NSEvent) { onHover?(true) }
    override func mouseExited(with event: NSEvent) { onHover?(false) }
}

protocol OrbMouse: AnyObject {
    func down(_ e: NSEvent, in panel: OrbPanel)
    func dragged(_ e: NSEvent)
    func up(_ e: NSEvent, in panel: OrbPanel)
    func menu(_ e: NSEvent, in panel: OrbPanel)
}

final class OrbPanel: NSPanel {
    weak var mouse: OrbMouse?

    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }

    override func sendEvent(_ e: NSEvent) {
        switch e.type {
        case .leftMouseDown: mouse?.down(e, in: self)
        case .leftMouseDragged: mouse?.dragged(e)
        case .leftMouseUp: mouse?.up(e, in: self)
        case .rightMouseDown: mouse?.menu(e, in: self)
        default: super.sendEvent(e)
        }
    }

    static func make(size: NSSize) -> OrbPanel {
        let p = OrbPanel(contentRect: NSRect(origin: .zero, size: size), styleMask: [.borderless, .nonactivatingPanel],
                         backing: .buffered, defer: false)
        p.isFloatingPanel = true
        p.level = .floating
        p.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary, .ignoresCycle]
        p.backgroundColor = .clear
        p.isOpaque = false
        p.hasShadow = false
        p.hidesOnDeactivate = false
        p.acceptsMouseMovedEvents = false
        return p
    }
}

@MainActor
final class OrbController: NSObject, OrbMouse {
    private var orbPanel: OrbPanel?
    private var bubblePanel: OrbPanel?
    private weak var model: AppModel?
    private var center = CGPoint.zero
    private var bubbleShown = false
    private var observers: [Any] = []
    // drag state
    private var downAt: CGPoint?
    private var centerAtDown = CGPoint.zero
    private var dragging = false
    private var pressedStop = false
    private var samples: [(t: TimeInterval, p: CGPoint)] = []
    // snap animation
    private var anim: Timer?
    private var vel = CGVector.zero
    private var target = CGPoint.zero

    private static let key = "orbCenter"

    func show(_ model: AppModel) {
        self.model = model
        if orbPanel == nil { build(model) }
        orbPanel?.orderFrontRegardless()
        refresh()
    }

    func hide() {
        orbPanel?.orderOut(nil)
        bubblePanel?.orderOut(nil)
        bubbleShown = false
    }

    private func build(_ model: AppModel) {
        let orb = OrbPanel.make(size: OrbGeometry.orbPanel)
        orb.mouse = self
        let oc = HoverContainer(frame: NSRect(origin: .zero, size: OrbGeometry.orbPanel))
        let oh = InertHostingView(rootView: OrbMarkView(model: model)
            .frame(width: OrbGeometry.orbPanel.width, height: OrbGeometry.orbPanel.height))
        oh.sizingOptions = []
        oh.frame = oc.bounds
        oc.addSubview(oh)
        oc.onHover = { [weak model] h in model?.orbHover = h }
        orb.contentView = oc

        let bubble = OrbPanel.make(size: OrbGeometry.capsulePanel)
        bubble.mouse = self
        let bh = InertHostingView(rootView: OrbBubbleView(model: model))
        bh.sizingOptions = []
        bh.frame = NSRect(origin: .zero, size: OrbGeometry.capsulePanel)
        let bc = HoverContainer(frame: bh.frame)
        bc.addSubview(bh)
        bc.onHover = { [weak model] h in model?.bubbleHover = h }
        bubble.contentView = bc
        bubble.alphaValue = 0

        orbPanel = orb
        bubblePanel = bubble
        center = savedCenter()
        model.orbSide = OrbSnap.rest(center: center, velocity: .zero, in: screenFrame(for: center)).side
        place(center)
        // Show or hide the bubble whenever what it should show changes.
        observers.append(model.objectWillChange.sink { [weak self] _ in
            DispatchQueue.main.async { MainActor.assumeIsolated { self?.refresh() } }
        })
        let n = NotificationCenter.default.addObserver(forName: NSApplication.didChangeScreenParametersNotification,
                                                       object: nil, queue: .main) { [weak self] _ in
            MainActor.assumeIsolated { self?.settle(velocity: .zero) }
        }
        observers.append(n)
    }

    private func refresh() {
        guard let model, let bubble = bubblePanel, orbPanel?.isVisible == true else { return }
        let want = model.orbExpanded
        guard want != bubbleShown else { return }
        bubbleShown = want
        bubble.setFrame(OrbGeometry.capsuleFrame(orbCenter: center, side: model.orbSide), display: false)
        if want { bubble.orderFrontRegardless() }
        let reduce = NSWorkspace.shared.accessibilityDisplayShouldReduceMotion
        NSAnimationContext.runAnimationGroup({ ctx in
            ctx.duration = reduce ? 0.15 : 0.22
            ctx.timingFunction = CAMediaTimingFunction(name: want ? .easeOut : .easeIn)
            bubble.animator().alphaValue = want ? 1 : 0
        }, completionHandler: { [weak self] in
            MainActor.assumeIsolated {
                if self?.bubbleShown == false { self?.bubblePanel?.orderOut(nil) }
            }
        })
    }

    private func place(_ c: CGPoint) {
        center = c
        orbPanel?.setFrame(OrbGeometry.orbFrame(center: c), display: false)
        if let model, bubbleShown {
            bubblePanel?.setFrame(OrbGeometry.capsuleFrame(orbCenter: c, side: model.orbSide), display: false)
        }
    }

    // MARK: mouse

    func down(_ e: NSEvent, in panel: OrbPanel) {
        anim?.invalidate()
        let p = e.locationInWindow
        if panel === orbPanel {
            let orb = NSRect(x: OrbGeometry.pad, y: OrbGeometry.pad, width: OrbGeometry.orb, height: OrbGeometry.orb)
            guard orb.insetBy(dx: -4, dy: -4).contains(p) else { downAt = nil; return }
            pressedStop = false
        } else {
            pressedStop = (model?.stopVisible ?? false)
                && OrbGeometry.stopRect(model?.orbSide ?? .left).insetBy(dx: -6, dy: -6).contains(p)
        }
        downAt = NSEvent.mouseLocation
        centerAtDown = center
        dragging = false
        samples = [(e.timestamp, NSEvent.mouseLocation)]
    }

    func dragged(_ e: NSEvent) {
        guard let start = downAt else { return }
        let now = NSEvent.mouseLocation
        if !dragging, hypot(now.x - start.x, now.y - start.y) > 3 { dragging = true }
        guard dragging else { return }
        place(CGPoint(x: centerAtDown.x + now.x - start.x, y: centerAtDown.y + now.y - start.y))  // 1:1, keeps the grab offset
        samples.append((e.timestamp, now))
        samples = samples.filter { e.timestamp - $0.t < 0.1 }
    }

    func up(_ e: NSEvent, in panel: OrbPanel) {
        defer { downAt = nil; dragging = false }
        guard downAt != nil, let model else { return }
        if dragging {
            var v = CGVector.zero
            if let a = samples.first, let b = samples.last, b.t - a.t > 0.005 {
                v = CGVector(dx: (b.p.x - a.p.x) / (b.t - a.t), dy: (b.p.y - a.p.y) / (b.t - a.t))
            }
            settle(velocity: v)
            return
        }
        if pressedStop { Task { await model.stopAll() }; return }
        if panel === orbPanel { model.orbClick() }
    }

    func menu(_ e: NSEvent, in panel: OrbPanel) {
        guard let model, let view = panel.contentView else { return }
        let m = NSMenu()
        func item(_ title: String, on: Bool = false, _ action: @escaping () -> Void) {
            let i = NSMenuItem(title: title, action: #selector(MenuAction.fire), keyEquivalent: "")
            let a = MenuAction(action)
            i.target = a
            i.representedObject = a  // the item keeps its action alive
            i.state = on ? .on : .off
            m.addItem(i)
        }
        if model.earsMode != nil {
            for (mode, title) in [("live", "Live mic"), ("shadow", "Shadow (decide, don't act)"), ("off", "Mic off")] {
                item(title, on: model.earsMode == mode) { Task { await model.setEarsMode(mode) } }
            }
            m.addItem(.separator())
        }
        item("Show her work on screen", on: model.showWork) { model.setShowWork(!model.showWork) }
        if let rec = model.recording {
            item("Record for tuning", on: rec) { Task { await model.setRecording(!rec) } }
        }
        m.addItem(.separator())
        item("Hide the orb") { model.setShowPill(false) }
        item("Quit Evie") { NSApp.terminate(nil) }
        m.popUp(positioning: nil, at: e.locationInWindow, in: view)
    }

    // MARK: snapping

    /// Throw the orb to the edge its momentum points at, carrying the drag's velocity into the spring.
    private func settle(velocity: CGVector) {
        guard let model else { return }
        let frame = screenFrame(for: center)
        let rest = OrbSnap.rest(center: center, velocity: velocity, in: frame)
        if rest.side != model.orbSide {
            model.orbSide = rest.side
            if bubbleShown {
                bubblePanel?.setFrame(OrbGeometry.capsuleFrame(orbCenter: center, side: rest.side), display: false)
            }
        }
        target = rest.center
        vel = velocity
        UserDefaults.standard.set([rest.center.x, rest.center.y], forKey: Self.key)
        if NSWorkspace.shared.accessibilityDisplayShouldReduceMotion {
            place(target)
            return
        }
        anim?.invalidate()
        let spring = Spring()
        anim = Timer.scheduledTimer(withTimeInterval: 1.0 / 120, repeats: true) { [weak self] t in
            MainActor.assumeIsolated {
                guard let self else { t.invalidate(); return }
                // X and Y are separate springs, so a diagonal throw stays smooth.
                let (x, vx) = spring.step(x: self.center.x, v: self.vel.dx, target: self.target.x, dt: 1.0 / 120)
                let (y, vy) = spring.step(x: self.center.y, v: self.vel.dy, target: self.target.y, dt: 1.0 / 120)
                self.vel = CGVector(dx: vx, dy: vy)
                self.place(CGPoint(x: x, y: y))
                if hypot(x - self.target.x, y - self.target.y) < 0.5, hypot(vx, vy) < 5 {
                    self.place(self.target)
                    t.invalidate()
                }
            }
        }
    }

    private func screenFrame(for p: CGPoint) -> NSRect {
        (NSScreen.screens.first { $0.frame.contains(p) } ?? NSScreen.main)?.visibleFrame
            ?? NSRect(x: 0, y: 0, width: 1440, height: 900)
    }

    private func savedCenter() -> CGPoint {
        if let xy = UserDefaults.standard.array(forKey: Self.key) as? [Double], xy.count == 2 {
            let p = CGPoint(x: xy[0], y: xy[1])
            return OrbSnap.rest(center: p, velocity: .zero, in: screenFrame(for: p)).center
        }
        let f = screenFrame(for: CGPoint(x: 1, y: 1))
        return CGPoint(x: f.maxX - 16 - OrbGeometry.orb / 2, y: f.maxY - 120)
    }

    // MARK: selftest hooks

    var panelsForTest: [OrbPanel] { [orbPanel, bubblePanel].compactMap { $0 } }
}

final class MenuAction: NSObject {
    private let run: () -> Void
    init(_ run: @escaping () -> Void) { self.run = run }
    @objc func fire() { run() }
}

// MARK: - Stress (selftest)

/// Builds the real orb panels and hammers them: mouse moves, drags and clicks straight into the
/// panels while the state flips at 60 Hz. The old pill died in exactly this situation.
@MainActor
enum OrbStress {
    static func run(seconds: Double) -> Bool {
        _ = NSApplication.shared
        let model = AppModel(preview: true)
        model.online = true
        let c = OrbController()
        c.show(model)
        let states = ["idle", "listening", "thinking", "speaking", "working"]
        let end = Date().addingTimeInterval(seconds)
        var i = 0
        while Date() < end {
            i += 1
            model.state = states[i % states.count]
            model.heard = i % 3 == 0 ? "" : "open the newest networkchuck video \(i)"
            model.said = i % 2 == 0 ? "Playing it now." : ""
            model.micLevel = Float(i % 10) / 10
            model.orbHover = i % 7 < 3
            for p in c.panelsForTest {
                let loc = NSPoint(x: CGFloat(i % 60), y: CGFloat((i * 7) % 60))
                for type in [NSEvent.EventType.mouseMoved, .leftMouseDown, .leftMouseDragged, .leftMouseUp] {
                    if let e = NSEvent.mouseEvent(with: type, location: loc, modifierFlags: [], timestamp: Date().timeIntervalSince1970,
                                                  windowNumber: p.windowNumber, context: nil, eventNumber: i, clickCount: 1,
                                                  pressure: 1) {
                        if type == .leftMouseUp { model.clickTalkEnabled = false }  // don't start the mic in a test
                        p.sendEvent(e)
                    }
                }
                p.contentView?.subviews.first?.mouseMoved(with: NSEvent.mouseEvent(
                    with: .mouseMoved, location: loc, modifierFlags: [], timestamp: 0, windowNumber: p.windowNumber,
                    context: nil, eventNumber: i, clickCount: 0, pressure: 0)!)
            }
            RunLoop.main.run(until: Date().addingTimeInterval(1.0 / 60))
        }
        c.hide()
        return true
    }
}
