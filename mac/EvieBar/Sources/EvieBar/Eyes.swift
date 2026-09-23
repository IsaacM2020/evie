import AppKit
import ApplicationServices

// Phase 3b: Evie's eyes and fingers for any app on the Mac, designed from zero (Isaac, 2026-09-23).
//
// She reads every element on screen at once, like Tab-cycling through every button but in one
// go, as structured text, never a screenshot:
//   - Safari pages: a small script tags every clickable/typeable element on the page and lists it
//     (works while Safari sits behind other apps).
//   - Any other app: the macOS Accessibility tree of its front window (buttons, fields, menus...).
// Each element gets a short id ("w12" on a page, "a7" in an app) that is only valid for that
// snapshot, so the planner can only ever point at something that is really there.
// She acts in the background first (Accessibility actions, page script, keys sent to one app);
// taking over the real screen is a separate, announced step.

// MARK: Pure helpers (selftest)

enum AXPick {
    static let actionable: Set<String> = [
        "AXButton", "AXLink", "AXTextField", "AXTextArea", "AXSearchField", "AXCheckBox", "AXRadioButton",
        "AXPopUpButton", "AXMenuButton", "AXComboBox", "AXMenuItem", "AXSlider", "AXDisclosureTriangle",
        "AXIncrementor", "AXSegmentedControl", "AXTab", "AXRow", "AXCell", "AXImage",
    ]
    static let typeable: Set<String> = ["AXTextField", "AXTextArea", "AXSearchField", "AXComboBox"]

    /// The words a person would use for it: its title, else description, else help or placeholder.
    static func label(title: String?, desc: String?, value: String?, help: String?, placeholder: String?,
                      role: String) -> String {
        let candidates = typeable.contains(role) ? [title, desc, placeholder, help] : [title, desc, value, help, placeholder]
        let first = candidates.compactMap { $0?.trimmingCharacters(in: .whitespacesAndNewlines) }.first { !$0.isEmpty }
        return String((first ?? "").prefix(100))
    }

    static func keep(role: String, label: String, width: Double, height: Double) -> Bool {
        guard actionable.contains(role), width >= 2, height >= 2 else { return false }
        if role == "AXImage" || role == "AXCell" || role == "AXRow" { return !label.isEmpty }
        return !label.isEmpty || typeable.contains(role)
    }

    static func short(_ role: String) -> String {
        role.hasPrefix("AX") ? String(role.dropFirst(2)).lowercased() : role
    }
}

// Letters and keys for shortcuts sent to one app ("cmd+t", "return").
enum KeyMap {
    static let codes: [String: CGKeyCode] = [
        "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9, "b": 11, "q": 12,
        "w": 13, "e": 14, "r": 15, "y": 16, "t": 17, "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23,
        "9": 25, "7": 26, "8": 28, "0": 29, "o": 31, "u": 32, "i": 34, "p": 35, "l": 37, "j": 38, "k": 40,
        "n": 45, "m": 46, "return": 36, "enter": 36, "tab": 48, "space": 49, "delete": 51, "escape": 53,
        "esc": 53, "left": 123, "right": 124, "down": 125, "up": 126, ",": 43, ".": 47, "/": 44, "[": 33, "]": 30,
    ]

    static func parse(_ combo: String) -> (CGKeyCode, CGEventFlags)? {
        var flags: CGEventFlags = []
        var key: CGKeyCode?
        for part in combo.lowercased().split(separator: "+").map({ $0.trimmingCharacters(in: .whitespaces) }) {
            switch part {
            case "cmd", "command": flags.insert(.maskCommand)
            case "shift": flags.insert(.maskShift)
            case "alt", "option", "opt": flags.insert(.maskAlternate)
            case "ctrl", "control": flags.insert(.maskControl)
            default: key = codes[part]
            }
        }
        guard let k = key else { return nil }
        return (k, flags)
    }
}

// MARK: The page reader (runs inside Safari)

enum WebReader {
    // Lists every visible element a person could click or type into, on-screen ones first.
    static let read = #"""
    (() => {
      const sel = 'a[href],button,input:not([type=hidden]),textarea,select,summary,video,[role=button],[role=link],[role=tab],[role=menuitem],[role=checkbox],[role=option],[role=switch],[role=searchbox],[role=textbox],[role=combobox],[contenteditable=""],[contenteditable=true],[tabindex]:not([tabindex="-1"])';
      document.querySelectorAll('[data-evie-id]').forEach(e => e.removeAttribute('data-evie-id'));
      const vw = innerWidth, vh = innerHeight, found = [];
      for (const el of document.querySelectorAll(sel)) {
        if (found.length >= 800) break;
        const r = el.getBoundingClientRect();
        if (r.width < 2 || r.height < 2) continue;
        const st = getComputedStyle(el);
        if (st.visibility === 'hidden' || st.display === 'none' || +st.opacity === 0) continue;
        const tag = el.tagName.toLowerCase();
        const role = el.getAttribute('role') || (tag === 'a' ? 'link' : tag === 'input' ? 'input:' + (el.type || 'text') : tag);
        const typeable = tag === 'input' || tag === 'textarea' || el.isContentEditable || /textbox|searchbox|combobox/.test(role);
        let label = (el.getAttribute('aria-label') || el.getAttribute('title') || el.innerText || el.getAttribute('alt') || '').trim();
        if (!label && typeable) label = el.getAttribute('placeholder') || el.name || '';
        if (!label && tag === 'video') label = 'video';
        if (!label && tag === 'a') { const img = el.querySelector('img[alt]'); if (img) label = img.alt; }
        label = label.replace(/\s+/g, ' ').slice(0, 100);
        if (!label && !typeable) continue;
        const onscreen = r.bottom > 0 && r.top < vh && r.right > 0 && r.left < vw;
        const region = (el.closest('header,nav,main,aside,footer,form,[role=dialog],[role=navigation],[role=main],[role=search]') || {}).tagName;
        found.push({el, role, label, onscreen, typeable, region: region ? region.toLowerCase() : '',
                    href: el.href ? String(el.href).slice(0, 160) : '', value: typeable && el.value ? String(el.value).slice(0, 80) : ''});
      }
      found.sort((a, b) => (b.onscreen - a.onscreen));
      const out = found.slice(0, 250).map((f, i) => {
        const id = 'w' + (i + 1);
        f.el.setAttribute('data-evie-id', id);
        return {id, role: f.role, label: f.label, href: f.href, value: f.value, onscreen: f.onscreen, region: f.region, typeable: f.typeable};
      });
      return JSON.stringify({url: location.href, title: document.title, elements: out});
    })()
    """#

    static func click(_ id: String) -> String {
        """
        (() => { const e = document.querySelector('[data-evie-id=\(json(id))]');
          if (!e) return 'missing';
          e.scrollIntoView({block: 'center'});
          if (e.tagName === 'VIDEO') { e.paused ? e.play() : e.pause(); return 'ok'; }
          e.click(); return 'ok'; })()
        """
    }

    static func setText(_ id: String, _ text: String, submit: Bool) -> String {
        """
        (() => { const e = document.querySelector('[data-evie-id=\(json(id))]');
          if (!e) return 'missing';
          e.focus();
          const t = \(json(text));
          if (e.isContentEditable) { e.textContent = t; }
          else { const d = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(e), 'value');
                 d && d.set ? d.set.call(e, t) : (e.value = t); }
          e.dispatchEvent(new Event('input', {bubbles: true}));
          e.dispatchEvent(new Event('change', {bubbles: true}));
          if (\(submit)) {
            const o = {key: 'Enter', code: 'Enter', keyCode: 13, which: 13, bubbles: true};
            e.dispatchEvent(new KeyboardEvent('keydown', o)); e.dispatchEvent(new KeyboardEvent('keyup', o));
            if (e.form) { e.form.requestSubmit ? e.form.requestSubmit() : e.form.submit(); }
          }
          return 'ok'; })()
        """
    }

    static let pageText = #"""
    (() => JSON.stringify({url: location.href, title: document.title,
      selected: String(getSelection() || '').slice(0, 2000),
      text: (document.body ? document.body.innerText : '').replace(/\n{3,}/g, '\n\n').slice(0, 6000)}))()
    """#

    static let ready = "document.readyState"

    static func json(_ s: String) -> String {
        let d = (try? JSONSerialization.data(withJSONObject: [s])) ?? Data("[\"\"]".utf8)
        let arr = String(data: d, encoding: .utf8) ?? "[\"\"]"
        return String(arr.dropFirst().dropLast())  // the quoted, escaped string
    }
}

// MARK: Eyes + fingers

@MainActor
final class Eyes {
    private var snapshot = ""
    private var axElements: [String: AXUIElement] = [:]
    private var webApp = ""  // the browser the w-ids belong to
    private var pid: pid_t = 0

    static let browsers = ["com.apple.Safari": "Safari"]

    func run(_ op: String, _ a: [String: JSONValue]) async -> HandsOutcome {
        guard AXIsProcessTrusted() else {
            return HandsOutcome(ok: false, detail: "I need Accessibility access for that")
        }
        switch op {
        case "observe": return await observe(app: a["app"]?.string)
        case "press": return await press(a["id"]?.string ?? "", snap: a["snapshot"]?.string ?? "")
        case "set_text":
            return await setText(a["id"]?.string ?? "", a["text"]?.string ?? "", submit: a["submit"]?.bool ?? false,
                                 snap: a["snapshot"]?.string ?? "")
        case "key": return key(a["combo"]?.string ?? "", app: a["app"]?.string)
        case "type": return typeText(a["text"]?.string ?? "", app: a["app"]?.string)
        case "open_url": return openURL(a["url"]?.string ?? "", app: a["app"]?.string, front: a["front"]?.bool ?? false)
        case "menu": return menu(a["path"]?.string ?? "", app: a["app"]?.string)
        case "activate": return activate(a["app"]?.string ?? "")
        case "screen_info": return await screenInfo(withPage: a["page"]?.bool ?? false)
        case "wait_page": return await waitPage(a["app"]?.string ?? "Safari", seconds: 8)
        default: return HandsOutcome(ok: false, detail: "I don't know how to \(op) yet")
        }
    }

    // MARK: finding the app

    private func app(named name: String?) -> NSRunningApplication? {
        guard let n = name?.lowercased(), !n.isEmpty else { return NSWorkspace.shared.frontmostApplication }
        return NSWorkspace.shared.runningApplications.first {
            ($0.localizedName?.lowercased() == n) || ($0.bundleIdentifier?.lowercased() == n)
        } ?? NSWorkspace.shared.runningApplications.first { $0.localizedName?.lowercased().contains(n) ?? false }
    }

    // MARK: observe

    private func observe(app name: String?) async -> HandsOutcome {
        guard let target = app(named: name) else { return HandsOutcome(ok: false, detail: "\(name ?? "that app") isn't open") }
        let appName = target.localizedName ?? ""
        snapshot = String(UUID().uuidString.prefix(8))
        axElements = [:]
        webApp = ""
        pid = target.processIdentifier
        if let browser = Self.browsers[target.bundleIdentifier ?? ""] {
            let r = await Self.safariJS(WebReader.read)
            if r.ok, let obj = try? JSONSerialization.jsonObject(with: Data(r.out.utf8)) as? [String: Any] {
                webApp = browser
                let els = (obj["elements"] as? [[String: Any]]) ?? []
                let data = (try? JSONSerialization.data(withJSONObject: els)).flatMap { String(data: $0, encoding: .utf8) } ?? "[]"
                return HandsOutcome(ok: true, detail: "\(els.count) elements", data: [
                    "snapshot": snapshot, "app": appName, "kind": "web", "elements": data,
                    "url": obj["url"] as? String ?? "", "window": obj["title"] as? String ?? ""])
            }
            // Page script not allowed or no page: fall through to Safari's own Accessibility tree.
        }
        let root = AXUIElementCreateApplication(target.processIdentifier)
        AXUIElementSetAttributeValue(root, "AXManualAccessibility" as CFString, kCFBooleanTrue)  // Chrome/Electron
        AXUIElementSetAttributeValue(root, "AXEnhancedUserInterface" as CFString, kCFBooleanTrue)
        let window: AXUIElement? = Self.attr(root, kAXFocusedWindowAttribute) ?? Self.attr(root, kAXMainWindowAttribute)
        guard let win = window else { return HandsOutcome(ok: false, detail: "\(appName) has no window open") }
        let title: String = Self.attr(win, kAXTitleAttribute) ?? ""
        var rows: [[String: Any]] = []
        var queue: [(AXUIElement, String)] = [(win, "")]
        let deadline = Date().addingTimeInterval(0.25)
        var visited = 0
        while !queue.isEmpty, rows.count < 250, visited < 5000, Date() < deadline {
            let (el, region) = queue.removeFirst()
            visited += 1
            let role: String = Self.attr(el, kAXRoleAttribute) ?? ""
            let sub: String = Self.attr(el, kAXSubroleAttribute) ?? ""
            let label = AXPick.label(title: Self.attr(el, kAXTitleAttribute), desc: Self.attr(el, kAXDescriptionAttribute),
                                     value: Self.attr(el, kAXValueAttribute), help: Self.attr(el, kAXHelpAttribute),
                                     placeholder: Self.attr(el, kAXPlaceholderValueAttribute), role: role)
            let size = Self.size(el)
            let r = sub == "AXSearchField" ? "AXSearchField" : role
            if AXPick.keep(role: r, label: label, width: size.width, height: size.height) {
                let id = "a\(rows.count + 1)"
                axElements[id] = el
                let enabled: Bool = Self.attr(el, kAXEnabledAttribute) ?? true
                let value: String = AXPick.typeable.contains(r) ? ((Self.attr(el, kAXValueAttribute) as String?) ?? "") : ""
                rows.append(["id": id, "role": AXPick.short(r), "label": label, "enabled": enabled,
                             "value": String(value.prefix(80)), "region": region,
                             "typeable": AXPick.typeable.contains(r)])
            }
            let nextRegion = ["AXToolbar", "AXSheet", "AXDialog", "AXOutline", "AXTabGroup", "AXWebArea", "AXMenuBar"]
                .contains(role) ? AXPick.short(role) : region
            let kids: [AXUIElement] = Self.attr(el, kAXChildrenAttribute) ?? []
            queue.append(contentsOf: kids.map { ($0, nextRegion) })
        }
        let data = (try? JSONSerialization.data(withJSONObject: rows)).flatMap { String(data: $0, encoding: .utf8) } ?? "[]"
        return HandsOutcome(ok: true, detail: "\(rows.count) elements", data: [
            "snapshot": snapshot, "app": appName, "kind": "app", "elements": data, "window": title])
    }

    // MARK: act

    private func press(_ id: String, snap: String) async -> HandsOutcome {
        guard snap == snapshot else { return HandsOutcome(ok: false, detail: "the screen changed, look again") }
        if id.hasPrefix("w"), !webApp.isEmpty {
            let r = await Self.safariJS(WebReader.click(id))
            return r.ok && r.out.contains("ok") ? HandsOutcome(ok: true, detail: "pressed") : HandsOutcome(ok: false, detail: "that button's gone")
        }
        guard let el = axElements[id] else { return HandsOutcome(ok: false, detail: "no such element") }
        let actions: [String] = {
            var names: CFArray?
            AXUIElementCopyActionNames(el, &names)
            return (names as? [String]) ?? []
        }()
        let action = actions.contains(kAXPressAction) ? kAXPressAction
            : actions.contains("AXPick") ? "AXPick" : actions.contains(kAXConfirmAction) ? kAXConfirmAction : kAXPressAction
        let err = AXUIElementPerformAction(el, action as CFString)
        if err != .success && actions.isEmpty {
            // Rows and cells often only "select": selecting is the press.
            AXUIElementSetAttributeValue(el, kAXSelectedAttribute as CFString, kCFBooleanTrue)
            return HandsOutcome(ok: true, detail: "selected")
        }
        return err == .success ? HandsOutcome(ok: true, detail: "pressed") : HandsOutcome(ok: false, detail: "it didn't respond")
    }

    private func setText(_ id: String, _ text: String, submit: Bool, snap: String) async -> HandsOutcome {
        guard snap == snapshot else { return HandsOutcome(ok: false, detail: "the screen changed, look again") }
        if id.hasPrefix("w"), !webApp.isEmpty {
            let r = await Self.safariJS(WebReader.setText(id, text, submit: submit))
            return r.ok && r.out.contains("ok") ? HandsOutcome(ok: true, detail: "typed") : HandsOutcome(ok: false, detail: "that field's gone")
        }
        guard let el = axElements[id] else { return HandsOutcome(ok: false, detail: "no such element") }
        AXUIElementSetAttributeValue(el, kAXFocusedAttribute as CFString, kCFBooleanTrue)
        let err = AXUIElementSetAttributeValue(el, kAXValueAttribute as CFString, text as CFString)
        if err != .success {
            _ = typeText(text, pid: pid)  // some fields only take real key presses
        }
        if submit { _ = key("return", pid: pid) }
        return HandsOutcome(ok: true, detail: "typed")
    }

    private func key(_ combo: String, app name: String?) -> HandsOutcome {
        guard let target = app(named: name) else { return HandsOutcome(ok: false, detail: "that app isn't open") }
        return key(combo, pid: target.processIdentifier)
    }

    private func key(_ combo: String, pid: pid_t) -> HandsOutcome {
        guard let (code, flags) = KeyMap.parse(combo) else { return HandsOutcome(ok: false, detail: "I don't know the key \(combo)") }
        for down in [true, false] {
            guard let e = CGEvent(keyboardEventSource: nil, virtualKey: code, keyDown: down) else { continue }
            e.flags = flags
            e.postToPid(pid)
        }
        return HandsOutcome(ok: true, detail: "sent \(combo)")
    }

    private func typeText(_ text: String, app name: String?) -> HandsOutcome {
        guard let target = app(named: name) else { return HandsOutcome(ok: false, detail: "that app isn't open") }
        return typeText(text, pid: target.processIdentifier)
    }

    private func typeText(_ text: String, pid: pid_t) -> HandsOutcome {
        let chars = Array(text.utf16)
        var i = 0
        while i < chars.count {
            let chunk = Array(chars[i..<min(i + 16, chars.count)])
            for down in [true, false] {
                guard let e = CGEvent(keyboardEventSource: nil, virtualKey: 0, keyDown: down) else { continue }
                chunk.withUnsafeBufferPointer { e.keyboardSetUnicodeString(stringLength: chunk.count, unicodeString: $0.baseAddress) }
                e.postToPid(pid)
            }
            i += 16
        }
        return HandsOutcome(ok: true, detail: "typed")
    }

    private func openURL(_ s: String, app name: String?, front: Bool) -> HandsOutcome {
        guard let url = URL(string: s), ["http", "https", "whatsapp", "mailto", "spotify", "notion"].contains(url.scheme ?? "") else {
            return HandsOutcome(ok: false, detail: "that link isn't safe to open")
        }
        let cfg = NSWorkspace.OpenConfiguration()
        cfg.activates = front  // background first: don't pull Isaac out of what he's doing
        if let n = name, let appURL = NSWorkspace.shared.urlForApplication(withBundleIdentifier: Self.bundle(for: n)) {
            NSWorkspace.shared.open([url], withApplicationAt: appURL, configuration: cfg)
        } else {
            NSWorkspace.shared.open(url, configuration: cfg)
        }
        return HandsOutcome(ok: true, detail: "opened")
    }

    private static func bundle(for name: String) -> String {
        ["safari": "com.apple.Safari", "whatsapp": "net.whatsapp.WhatsApp", "notes": "com.apple.Notes",
         "messages": "com.apple.MobileSMS", "mail": "com.apple.mail"][name.lowercased()] ?? name
    }

    private func menu(_ path: String, app name: String?) -> HandsOutcome {
        guard let target = app(named: name) else { return HandsOutcome(ok: false, detail: "that app isn't open") }
        let parts = path.split(separator: ">").map { $0.trimmingCharacters(in: .whitespaces).lowercased() }
        guard !parts.isEmpty else { return HandsOutcome(ok: false, detail: "which menu?") }
        let root = AXUIElementCreateApplication(target.processIdentifier)
        guard var node: AXUIElement = Self.attr(root, kAXMenuBarAttribute) else {
            return HandsOutcome(ok: false, detail: "no menu bar")
        }
        for (i, part) in parts.enumerated() {
            var kids: [AXUIElement] = Self.attr(node, kAXChildrenAttribute) ?? []
            if kids.count == 1, (Self.attr(kids[0], kAXRoleAttribute) as String?) == "AXMenu" {
                kids = Self.attr(kids[0], kAXChildrenAttribute) ?? []
            }
            guard let hit = kids.first(where: { ((Self.attr($0, kAXTitleAttribute) as String?) ?? "").lowercased() == part })
                ?? kids.first(where: { ((Self.attr($0, kAXTitleAttribute) as String?) ?? "").lowercased().hasPrefix(part) }) else {
                return HandsOutcome(ok: false, detail: "no \(part) in that menu")
            }
            if i == parts.count - 1 {
                return AXUIElementPerformAction(hit, kAXPressAction as CFString) == .success
                    ? HandsOutcome(ok: true, detail: "chose \(path)") : HandsOutcome(ok: false, detail: "that menu item didn't work")
            }
            node = hit
        }
        return HandsOutcome(ok: false, detail: "menu not found")
    }

    private func activate(_ name: String) -> HandsOutcome {
        guard let target = app(named: name) else { return HandsOutcome(ok: false, detail: "\(name) isn't open") }
        target.activate()
        return HandsOutcome(ok: true, detail: "brought \(target.localizedName ?? name) to the front")
    }

    // MARK: the screen, for answers ("summarise this page")

    private func screenInfo(withPage: Bool) async -> HandsOutcome {
        guard let front = NSWorkspace.shared.frontmostApplication else { return HandsOutcome(ok: false, detail: "no app in front") }
        var data: [String: String] = ["front_app": front.localizedName ?? ""]
        let root = AXUIElementCreateApplication(front.processIdentifier)
        if let win: AXUIElement = Self.attr(root, kAXFocusedWindowAttribute) {
            data["window"] = Self.attr(win, kAXTitleAttribute) ?? ""
        }
        if let focused: AXUIElement = Self.attr(root, kAXFocusedUIElementAttribute),
           let sel: String = Self.attr(focused, kAXSelectedTextAttribute), !sel.isEmpty {
            data["selected"] = String(sel.prefix(2000))
        }
        if front.bundleIdentifier == "com.apple.Safari" {
            let r = await Self.safariJS(withPage ? WebReader.pageText : "JSON.stringify({url: location.href, title: document.title, selected: String(getSelection()||'').slice(0,2000)})")
            if r.ok, let obj = try? JSONSerialization.jsonObject(with: Data(r.out.utf8)) as? [String: Any] {
                data["url"] = obj["url"] as? String ?? ""
                if let t = obj["text"] as? String { data["page_text"] = t }
                if let s = obj["selected"] as? String, !s.isEmpty { data["selected"] = s }
            }
        }
        return HandsOutcome(ok: true, detail: "ok", data: data)
    }

    private func waitPage(_ name: String, seconds: Double) async -> HandsOutcome {
        let end = Date().addingTimeInterval(seconds)
        try? await Task.sleep(for: .milliseconds(400))  // let the new page start loading
        while Date() < end {
            let r = await Self.safariJS(WebReader.ready)
            if r.ok, r.out.contains("complete") { return HandsOutcome(ok: true, detail: "loaded") }
            try? await Task.sleep(for: .milliseconds(250))
        }
        return HandsOutcome(ok: false, detail: "the page is taking too long")
    }

    // MARK: plumbing

    nonisolated static func attr<T>(_ el: AXUIElement, _ name: String) -> T? {
        var v: CFTypeRef?
        guard AXUIElementCopyAttributeValue(el, name as CFString, &v) == .success, let v else { return nil }
        return v as? T
    }

    nonisolated static func size(_ el: AXUIElement) -> (width: Double, height: Double) {
        var v: CFTypeRef?
        guard AXUIElementCopyAttributeValue(el, kAXSizeAttribute as CFString, &v) == .success, let v,
              CFGetTypeID(v) == AXValueGetTypeID() else { return (0, 0) }
        var s = CGSize.zero
        AXValueGetValue(v as! AXValue, .cgSize, &s)
        return (Double(s.width), Double(s.height))
    }

    /// Runs a page script in Safari's front tab. The script travels as an argument, never pasted
    /// into AppleScript source, so nothing on a page can break out of it.
    nonisolated static func safariJS(_ js: String) async -> (ok: Bool, out: String) {
        await withCheckedContinuation { cont in
            DispatchQueue.global(qos: .userInitiated).async {
                let p = Process()
                p.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")
                p.arguments = ["-e", "on run argv", "-e",
                               "tell application \"Safari\" to do JavaScript (item 1 of argv) in current tab of front window",
                               "-e", "end run", js]
                let out = Pipe(), err = Pipe()
                p.standardOutput = out
                p.standardError = err
                do { try p.run() } catch { cont.resume(returning: (false, "\(error)")); return }
                // Never hang on a page (or on macOS still waiting for "allow Evie to control Safari").
                DispatchQueue.global().asyncAfter(deadline: .now() + 6) { if p.isRunning { p.terminate() } }
                p.waitUntilExit()
                let o = String(data: out.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
                let e = String(data: err.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
                cont.resume(returning: (p.terminationStatus == 0, p.terminationStatus == 0 ? o.trimmingCharacters(in: .whitespacesAndNewlines) : e))
            }
        }
    }
}
