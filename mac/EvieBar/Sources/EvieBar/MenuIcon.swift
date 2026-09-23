import AppKit

// Evie's own menu bar mark, drawn in code (a template image, so macOS tints it for light/dark
// menu bars). One idea in every state: a ring (Evie) around what she's doing right now.
//   idle: ring + dot   listening: solid disc, bars cut out   speaking: ring + bars
//   thinking: ring + three dots   working: ring with a gap + dot   offline: dashed ring
enum MenuIcon {
    static let states = ["idle", "listening", "speaking", "thinking", "working", "offline", "attention"]

    static func state(online: Bool, state: String, working: Bool, jevOk: Bool?) -> String {
        if !online { return "offline" }
        if state == "listening" || state == "speaking" || state == "thinking" { return state }
        if working { return "working" }
        if jevOk == false { return "attention" }
        return "idle"
    }

    static func image(_ state: String) -> NSImage {
        let img = NSImage(size: NSSize(width: 18, height: 18), flipped: false) { rect in
            draw(state, in: rect)
            return true
        }
        img.isTemplate = true
        img.accessibilityDescription = "Evie, \(state)"
        return img
    }

    private static func draw(_ state: String, in rect: NSRect) {
        NSColor.black.set()
        let c = NSPoint(x: rect.midX, y: rect.midY)
        let ringRect = rect.insetBy(dx: 2.2, dy: 2.2)
        let ring = NSBezierPath(ovalIn: ringRect)
        ring.lineWidth = 1.6

        func dot(_ r: CGFloat, at p: NSPoint) {
            NSBezierPath(ovalIn: NSRect(x: p.x - r, y: p.y - r, width: 2 * r, height: 2 * r)).fill()
        }
        func bars(_ heights: [CGFloat]) {
            let w: CGFloat = 1.6, gap: CGFloat = 1.4
            let total = CGFloat(heights.count) * w + CGFloat(heights.count - 1) * gap
            for (i, h) in heights.enumerated() {
                let x = c.x - total / 2 + CGFloat(i) * (w + gap)
                NSBezierPath(roundedRect: NSRect(x: x, y: c.y - h / 2, width: w, height: h), xRadius: w / 2,
                             yRadius: w / 2).fill()
            }
        }

        switch state {
        case "listening":
            NSBezierPath(ovalIn: rect.insetBy(dx: 1.4, dy: 1.4)).fill()
            NSGraphicsContext.current?.compositingOperation = .destinationOut
            bars([4, 7.5, 4])
            NSGraphicsContext.current?.compositingOperation = .sourceOver
        case "speaking":
            ring.stroke()
            bars([3, 6.5, 3])
        case "thinking":
            ring.stroke()
            for dx in [-3.2, 0, 3.2] as [CGFloat] { dot(1.05, at: NSPoint(x: c.x + dx, y: c.y)) }
        case "working":
            let arc = NSBezierPath()
            arc.appendArc(withCenter: c, radius: ringRect.width / 2, startAngle: 110, endAngle: 40, clockwise: false)
            arc.lineWidth = 1.6
            arc.lineCapStyle = .round
            arc.stroke()
            dot(2.4, at: c)
        case "offline":
            ring.setLineDash([2.2, 2.0], count: 2, phase: 0)
            ring.stroke()
        case "attention":
            ring.stroke()
            NSBezierPath(roundedRect: NSRect(x: c.x - 0.8, y: c.y - 0.6, width: 1.6, height: 4.4), xRadius: 0.8,
                         yRadius: 0.8).fill()
            dot(0.9, at: NSPoint(x: c.x, y: c.y - 2.4))
        default:  // idle
            ring.stroke()
            dot(2.6, at: c)
        }
    }
}
