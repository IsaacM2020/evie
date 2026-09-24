import AppKit

// Evie's own menu bar mark, drawn in code (a template image, so macOS tints it for light/dark
// menu bars). The same capsule and line as her mark on screen:
//   idle: the line   listening: solid capsule, wave cut out   speaking: capsule + wave
//   thinking: a dash moving along   working: the line half filled   offline: dashed capsule
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
        let capRect = NSRect(x: c.x - 8, y: c.y - 4.6, width: 16, height: 9.2)
        let cap = NSBezierPath(roundedRect: capRect, xRadius: 4.6, yRadius: 4.6)
        cap.lineWidth = 1.4

        func line(_ from: CGFloat, _ to: CGFloat, width: CGFloat = 1.5) {
            let p = NSBezierPath()
            p.move(to: NSPoint(x: c.x + from, y: c.y))
            p.line(to: NSPoint(x: c.x + to, y: c.y))
            p.lineWidth = width
            p.lineCapStyle = .round
            p.stroke()
        }
        func wave(_ amp: CGFloat) {
            let p = NSBezierPath()
            for i in 0...16 {
                let f = CGFloat(i) / 16
                let pt = NSPoint(x: c.x - 4.5 + 9 * f, y: c.y + sin(f * .pi) * amp * sin(f * 3 * .pi))
                if i == 0 { p.move(to: pt) } else { p.line(to: pt) }
            }
            p.lineWidth = 1.2
            p.lineCapStyle = .round
            p.stroke()
        }

        switch state {
        case "listening":  // filled capsule, the wave cut out of it
            NSBezierPath(roundedRect: capRect.insetBy(dx: -0.4, dy: -0.4), xRadius: 5, yRadius: 5).fill()
            NSGraphicsContext.current?.compositingOperation = .destinationOut
            wave(2.4)
            NSGraphicsContext.current?.compositingOperation = .sourceOver
        case "speaking":
            cap.stroke()
            wave(2.0)
        case "thinking":
            cap.stroke()
            line(-4, -1.5, width: 1.1)
            line(0.5, 4.5, width: 1.9)
        case "working":
            cap.stroke()
            line(-4.5, 0.5, width: 2.0)
            line(2.2, 4.5, width: 0.8)
        case "offline":
            cap.setLineDash([2.0, 1.8], count: 2, phase: 0)
            cap.stroke()
        case "attention":
            cap.stroke()
            line(-4, 2)
            NSBezierPath(ovalIn: NSRect(x: c.x + 3.2, y: c.y - 1, width: 2, height: 2)).fill()
        default:  // idle: her line, at rest
            cap.stroke()
            line(-4, 4)
        }
    }
}
