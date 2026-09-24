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
        let rows = [OptionRow(id: "a", label: "$1 vs $1,000,000 Hotel Room", meta: "2 days ago"),
                    OptionRow(id: "b", label: "I Survived 7 Days In An Abandoned City", meta: "9 days ago"),
                    OptionRow(id: "c", label: "Last To Leave The Island Wins", meta: "2 weeks ago")]
        let orbs: [(String, (AppModel) -> Void)] = [
            ("orb-idle", { m in m.online = true }),
            ("orb-next", { m in m.online = true; m.nextEvent = "Sax · 12m" }),
            ("orb-listening", { m in m.online = true; m.state = "listening"; m.micLevel = 0.7; m.earsMode = "live" }),
            ("orb-reply", { m in m.online = true; m.linger = true; m.heard = "play the newest networkchuck video"
                m.said = "Playing 'I hacked my own network' from NetworkChuck." }),
            ("orb-which", { m in m.online = true; m.state = "thinking"; m.options = rows; m.asked = "Here are MrBeast's latest." }),
            ("orb-job", { m in m.online = true; m.orbHover = true; m.jobProgress = 0.6
                m.job = JobView(id: "a1", goal: "Fixing the website deploy", started: Date(),
                                lines: ["Step 3 of 5: running the tests"]) }),
            ("orb-class", { m in m.online = true; m.quietMode = "text"; m.quietWhy = "Chem Class"; m.linger = true
                m.said = "6.022 × 10²³ per mole. It's the number of particles in one mole of anything." }),
            ("orb-followup", { m in m.online = true; m.followupFresh = true
                m.followups = [FollowCard(id: "f", about: "overheard", line: "Heard you've got the dentist on Wednesday. What time?",
                                          ask: true)] }),
            ("orb-offer", { m in m.online = true; m.followupFresh = true
                m.followups = [FollowCard(id: "g", about: "tasks", line: "Email bio teacher is due today. Want help getting it done?",
                                          yes: true)] }),
            ("orb-countdown", { m in m.online = true; m.linger = true; m.countdownTotal = 6
                m.countdownUntil = Date().addingTimeInterval(4); m.said = "Sending \"on my way\" to Mom." }),
            ("orb-left", { m in m.online = true; m.orbSide = .left; m.linger = true; m.said = "Volume 30." }),
            ("orb-offline", { m in m.online = false }),
        ]
        for (name, setup) in orbs {
            let m = AppModel(preview: true)
            setup(m)
            let view = ZStack {
                if m.card == .none { OrbMarkView(model: m) } else { OrbCardView(model: m) }
            }
            .padding(24)
            .background(LinearGradient(colors: [Color(red: 0.32, green: 0.33, blue: 0.35), Color(red: 0.62, green: 0.45, blue: 0.3)],
                                       startPoint: .topLeading, endPoint: .bottomTrailing))
            capture(AnyView(view), dark: true, to: "\(dir)/\(name).png")
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
