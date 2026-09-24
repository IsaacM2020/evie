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
        let orbs: [(String, (AppModel) -> Void)] = [
            ("orb-idle", { m in m.online = true }),
            ("orb-hover", { m in m.online = true; m.orbHover = true; m.heard = "what's on tomorrow"
                m.said = "School at 8, then chem at 8pm." }),
            ("orb-listening", { m in m.online = true; m.state = "listening"; m.micLevel = 0.7; m.earsMode = "live" }),
            ("orb-thinking", { m in m.online = true; m.state = "thinking"; m.heard = "play the newest networkchuck video" }),
            ("orb-speaking", { m in m.online = true; m.state = "speaking"; m.voiceLevel = 0.6
                m.heard = "play the newest networkchuck video"; m.said = "Playing 'I hacked my own network' from NetworkChuck." }),
            ("orb-working", { m in m.online = true; m.state = "working"; m.orbHover = true; m.jobProgress = 0.4
                m.heard = "check why my website deploy failed"
                m.job = JobView(id: "a1", goal: "Website deploy", started: Date(), lines: ["Step 2 of 5, reading the build log"]) }),
            ("orb-left", { m in m.online = true; m.orbSide = .left; m.state = "speaking"; m.said = "Volume 30." }),
            ("orb-offline", { m in m.online = false }),
        ]
        for (name, setup) in orbs {
            for dark in [false, true] {
                let m = AppModel(preview: true)
                setup(m)
                let orb = OrbMarkView(model: m).frame(width: OrbGeometry.orbPanel.width, height: OrbGeometry.orbPanel.height)
                let bubble = OrbBubbleView(model: m).opacity(m.orbExpanded ? 1 : 0)
                let row = HStack(spacing: -12) {
                    if m.orbSide == .right { bubble; orb } else { orb; bubble }
                }
                .padding(20)
                .background(dark ? Color(white: 0.16) : Color(red: 0.86, green: 0.88, blue: 0.92))
                capture(AnyView(row), dark: dark, to: "\(dir)/\(name)-\(dark ? "dark" : "light").png")
            }
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
