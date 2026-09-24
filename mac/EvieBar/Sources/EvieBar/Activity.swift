import AppKit
import ApplicationServices

// What the app notices for Evie's proactive side (Phase 4/5):
//   active  he unlocked the Mac or woke the screen (the morning brief keys off the first one)
//   idle    20 min without keyboard or mouse ("where was I?" snapshots what was open)
//   back    input again after that
//   screen text   every minute, if a dev app is in front: its focused window's text, for the stuck
//                 detector. It goes only to the core on this Mac, and only the last 6000 characters.
@MainActor
final class ActivityWatch {
    static let devApps: Set<String> = ["Terminal", "iTerm2", "Code", "Visual Studio Code", "Cursor", "Xcode", "Warp",
                                       "Ghostty", "Zed"]
    nonisolated static let idleAfter: Double = 20 * 60

    private var core: CoreClient?
    private var idle = false
    private var timers: [Timer] = []
    private var observers: [Any] = []

    /// Pure: idle/back from seconds since the last input.
    nonisolated static func transition(idle: Bool, secondsSinceInput s: Double) -> String? {
        if !idle && s >= idleAfter { return "idle" }
        if idle && s < 30 { return "back" }
        return nil
    }

    func start(_ core: CoreClient) {
        self.core = core
        let nc = NSWorkspace.shared.notificationCenter
        for name in [NSWorkspace.sessionDidBecomeActiveNotification, NSWorkspace.screensDidWakeNotification] {
            observers.append(nc.addObserver(forName: name, object: nil, queue: .main) { [weak self] _ in
                MainActor.assumeIsolated { self?.send("active") }
            })
        }
        send("active")  // the app starting counts (login)
        timers.append(Timer.scheduledTimer(withTimeInterval: 30, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.checkIdle() }
        })
        timers.append(Timer.scheduledTimer(withTimeInterval: 60, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.readDevApp() }
        })
    }

    private func send(_ kind: String) {
        guard let core else { return }
        Task { await core.activity(kind) }
    }

    private func checkIdle() {
        let any = CGEventType(rawValue: ~0)!
        let s = CGEventSource.secondsSinceLastEventType(.combinedSessionState, eventType: any)
        if let t = Self.transition(idle: idle, secondsSinceInput: s) {
            idle = t == "idle"
            send(t)
        }
    }

    private func readDevApp() {
        guard AXIsProcessTrusted(), !idle, let app = NSWorkspace.shared.frontmostApplication,
              let name = app.localizedName, Self.devApps.contains(name), let core else { return }
        let pid = app.processIdentifier
        Task.detached(priority: .utility) {
            guard let text = ActivityWatch.windowText(pid: pid), !text.isEmpty else { return }
            await core.screenText(app: name, text: String(text.suffix(6000)))
        }
    }

    /// The focused window's longest text (a terminal's scrollback, an editor's buffer).
    nonisolated static func windowText(pid: pid_t) -> String? {
        let ax = AXUIElementCreateApplication(pid)
        AXUIElementSetMessagingTimeout(ax, 1.0)
        var win: CFTypeRef?
        guard AXUIElementCopyAttributeValue(ax, kAXFocusedWindowAttribute as CFString, &win) == .success,
              let w = win else { return nil }
        var best = ""
        func walk(_ el: AXUIElement, depth: Int) {
            guard depth < 8 else { return }
            var role: CFTypeRef?
            AXUIElementCopyAttributeValue(el, kAXRoleAttribute as CFString, &role)
            if let r = role as? String, r == "AXTextArea" || r == "AXStaticText" {
                var v: CFTypeRef?
                if AXUIElementCopyAttributeValue(el, kAXValueAttribute as CFString, &v) == .success,
                   let s = v as? String, s.count > best.count { best = s }
            }
            var kids: CFTypeRef?
            if AXUIElementCopyAttributeValue(el, kAXChildrenAttribute as CFString, &kids) == .success,
               let arr = kids as? [AXUIElement] {
                for k in arr.prefix(60) { walk(k, depth: depth + 1) }
            }
        }
        walk(w as! AXUIElement, depth: 0)
        return best
    }
}
