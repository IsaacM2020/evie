import AppKit
import SwiftUI

// `EvieBar --snapshot <dir>` renders the panel in a few states to PNGs, so the design can be
// checked without clicking the menu bar.
@MainActor
enum Snapshot {
    static func run(to dir: String) {
        let states: [(String, (AppModel) -> Void)] = [
            ("idle", { m in m.online = true; m.jevOk = true }),
            ("job", { m in
                m.online = true; m.jevOk = true; m.state = "speaking"
                m.heard = "Evie, what's on tomorrow?"
                m.verdict = "act · answer"
                m.said = "Tomorrow's all-day Vedant's birthday, then school at 8 and chem at 8pm."
                m.job = JobView(id: "a1", goal: "fix the chase bug in my cricket model",
                                started: Date().addingTimeInterval(-94),
                                lines: ["Read model.py", "Ran: uv run pytest -q", "Found it: the chase target is off by one."])
            }),
            ("warnings", { m in m.online = false; m.axTrusted = false; m.calendarDenied = true }),
            ("openmic", { m in
                m.online = true; m.jevOk = true; m.earsMode = "shadow"
                m.voiceprint = VoicePrintDTO(clips: 3, seconds: 7.4, ready: false)
                m.enrolling = true; m.enrollStartClips = 1
                m.shadowLog = [ShadowRow(text: "play some lofi", would: "act · quick_action"),
                               ShadowRow(text: "whats the time", would: "clarify · unsure it was for me")]
            }),
        ]
        let pills: [(String, (AppModel) -> Void)] = [
            ("pill-idle", { m in m.online = true }),
            ("pill-listening", { m in m.online = true; m.state = "listening" }),
            ("pill-speaking", { m in m.online = true; m.state = "speaking"; m.said = "You've got chem at 8pm tomorrow." }),
            ("pill-working", { m in m.online = true; m.state = "working"; m.pillNote = "Ran: uv run pytest -q" }),
            ("pill-shadow", { m in m.online = true; m.pillNote = "Would have: act · quick_action" }),
        ]
        for (name, setup) in pills {
            let m = AppModel(preview: true)
            setup(m)
            capture(AnyView(PillView(model: m).background(Color(white: 0.55))), dark: false, to: "\(dir)/\(name).png")
        }
        for state in MenuIcon.states {  // 8x so the mark can be judged by eye
            let img = MenuIcon.image(state)
            let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: 144, pixelsHigh: 144, bitsPerSample: 8,
                                       samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB,
                                       bytesPerRow: 0, bitsPerPixel: 0)!
            NSGraphicsContext.saveGraphicsState()
            NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
            NSColor.white.setFill()
            NSRect(x: 0, y: 0, width: 144, height: 144).fill()
            img.draw(in: NSRect(x: 0, y: 0, width: 144, height: 144))
            NSGraphicsContext.restoreGraphicsState()
            try? rep.representation(using: .png, properties: [:])?.write(to: URL(fileURLWithPath: "\(dir)/icon-\(state).png"))
        }
        for (name, setup) in states {
            for dark in [false, true] {
                let m = AppModel(preview: true)
                setup(m)
                let view = PanelView(model: m)
                    .background(dark ? Color(white: 0.12) : Color(white: 0.95))
                capture(AnyView(view), dark: dark, to: "\(dir)/\(name)-\(dark ? "dark" : "light").png")
            }
        }
    }

    // Glass is drawn by the window server, so render into a real (briefly shown) window
    // and capture just that window with screencapture.
    private static func capture(_ view: AnyView, dark: Bool, to out: String) {
        let host = NSHostingView(rootView: view.environment(\.colorScheme, dark ? .dark : .light))
        host.frame.size = host.fittingSize
        let w = NSWindow(contentRect: host.frame, styleMask: [.borderless], backing: .buffered, defer: false)
        w.contentView = host
        w.appearance = NSAppearance(named: dark ? .darkAqua : .aqua)
        w.setFrameOrigin(NSPoint(x: 40, y: 80))
        w.orderFrontRegardless()
        RunLoop.main.run(until: Date().addingTimeInterval(0.6))
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/sbin/screencapture")
        p.arguments = ["-x", "-o", "-l", "\(w.windowNumber)", out]
        try? p.run()
        p.waitUntilExit()
        w.orderOut(nil)
    }
}
