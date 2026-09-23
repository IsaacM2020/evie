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
        ]
        for (name, setup) in states {
            for dark in [false, true] {
                let m = AppModel(preview: true)
                setup(m)
                let view = PanelView(model: m)
                    .background(dark ? Color(white: 0.12) : Color(white: 0.95))
                    .environment(\.colorScheme, dark ? .dark : .light)
                // Glass is drawn by the window server, so render into a real (briefly shown) window
                // and capture just that window with screencapture.
                let host = NSHostingView(rootView: view)
                host.frame.size = host.fittingSize
                let w = NSWindow(contentRect: host.frame, styleMask: [.borderless], backing: .buffered, defer: false)
                w.contentView = host
                w.appearance = NSAppearance(named: dark ? .darkAqua : .aqua)
                w.setFrameOrigin(NSPoint(x: 40, y: 80))
                w.orderFrontRegardless()
                RunLoop.main.run(until: Date().addingTimeInterval(0.6))
                let out = "\(dir)/\(name)-\(dark ? "dark" : "light").png"
                let p = Process()
                p.executableURL = URL(fileURLWithPath: "/usr/sbin/screencapture")
                p.arguments = ["-x", "-o", "-l", "\(w.windowNumber)", out]
                try? p.run()
                p.waitUntilExit()
                w.orderOut(nil)
            }
        }
    }
}
