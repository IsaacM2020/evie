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
    // Reader v2 (Phase 3c): each element also carries its card's details (a video's channel and
    // age, an article's teaser) so "the newest one" can be picked from one read; duplicate links
    // (thumbnail + title to the same place) are listed once; selected tabs are marked.
    static let read = #"""
    (() => {
      const sel = 'a[href],button,input:not([type=hidden]),textarea,select,summary,video,[role=button],[role=link],[role=tab],[role=menuitem],[role=checkbox],[role=option],[role=switch],[role=searchbox],[role=textbox],[role=combobox],[contenteditable=""],[contenteditable=true],[tabindex]:not([tabindex="-1"])';
      const cardSel = 'ytd-rich-item-renderer,ytd-video-renderer,ytd-grid-video-renderer,ytd-compact-video-renderer,ytd-playlist-video-renderer,ytd-channel-renderer,article,li,[role=listitem],[role=article]';
      document.querySelectorAll('[data-evie-id]').forEach(e => e.removeAttribute('data-evie-id'));
      const vw = innerWidth, vh = innerHeight, found = [], seen = new Set(), cards = new Map();
      const clean = t => (t || '').replace(/\s+/g, ' ').trim();
      for (const el of document.querySelectorAll(sel)) {
        if (found.length >= 900) break;
        const r = el.getBoundingClientRect();
        if (r.width < 2 || r.height < 2) continue;
        const st = getComputedStyle(el);
        if (st.visibility === 'hidden' || st.display === 'none' || +st.opacity === 0) continue;
        const tag = el.tagName.toLowerCase();
        const role = el.getAttribute('role') || (tag === 'a' ? 'link' : tag === 'input' ? 'input:' + (el.type || 'text') : tag);
        const typeable = tag === 'input' || tag === 'textarea' || el.isContentEditable || /textbox|searchbox|combobox/.test(role);
        let label = clean(el.getAttribute('aria-label') || el.getAttribute('title') || el.innerText || el.getAttribute('alt') || '');
        if (!label && typeable) label = el.getAttribute('placeholder') || el.name || '';
        if (!label && tag === 'video') label = 'video';
        if (!label && tag === 'a') { const img = el.querySelector('img[alt]'); if (img) label = clean(img.alt); }
        label = label.slice(0, 100);
        if (!label && !typeable) continue;
        const href = el.href ? String(el.href).slice(0, 160) : '';
        const key = href ? href + '|' + label : '';
        if (key && seen.has(key)) continue;
        if (key) seen.add(key);
        const onscreen = r.bottom > 0 && r.top < vh && r.right > 0 && r.left < vw;
        const region = (el.closest('header,nav,main,aside,footer,form,[role=dialog],[role=navigation],[role=main],[role=search]') || {}).tagName;
        const card = el.closest(cardSel);
        let meta = '', group = '';
        if (card) {
          if (!cards.has(card)) {
            const m = card.querySelector('#metadata-line,#metadata,ytd-channel-name,time,.metadata,[class*=meta]');
            let t = clean(m ? m.innerText : card.innerText);
            cards.set(card, {n: cards.size + 1, meta: t.slice(0, 120)});
          }
          const c = cards.get(card);
          group = 'c' + c.n;
          meta = c.meta.replace(label, '').trim().slice(0, 90);
        }
        const selected = el.getAttribute('aria-selected') === 'true' || el.getAttribute('aria-current') === 'page' || el.getAttribute('aria-expanded') === 'true';
        found.push({el, role, label, onscreen, typeable, region: region ? region.toLowerCase() : '', href, meta, group, selected,
                    value: typeable && el.value ? String(el.value).slice(0, 80) : ''});
      }
      found.sort((a, b) => (b.onscreen - a.onscreen));
      const out = found.slice(0, 300).map((f, i) => {
        const id = 'w' + (i + 1);
        f.el.setAttribute('data-evie-id', id);
        const o = {id, role: f.role, label: f.label, href: f.href, value: f.value, onscreen: f.onscreen, region: f.region, typeable: f.typeable};
        if (f.meta) o.meta = f.meta;
        if (f.group) o.group = f.group;
        if (f.selected) o.selected = true;
        return o;
      });
      return JSON.stringify({url: location.href, title: document.title, elements: out});
    })()
    """#

    // wait_page v2: is the page loaded AND has it stopped changing? Single-page sites (YouTube)
    // never reload, so "loaded" alone says nothing; 300 ms without DOM changes does.
    static let probe = #"""
    (() => {
      if (!window.__evieMO) {
        window.__evieLast = Date.now();
        window.__evieMO = new MutationObserver(() => { window.__evieLast = Date.now(); });
        window.__evieMO.observe(document, {subtree: true, childList: true});
      }
      return JSON.stringify({ready: document.readyState, url: location.href, quiet: Date.now() - window.__evieLast});
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

enum PageSettle {
    static func done(ready: String, url: String, before: String, quietMs: Double) -> Bool {
        ready == "complete" && (before.isEmpty || url != before) && quietMs >= 300
    }
}

// MARK: AppleScript, in-process

/// Runs AppleScript inside the app (NSAppleScript) on one serial queue: no `osascript` process per
/// call (~100 ms each before, 2026-09-24). Every script is wrapped in a timeout, and the caller
/// stops waiting after `timeout` + 1 s, so a Safari permission prompt can never hang Evie.
enum AppleRun {
    private static let q = DispatchQueue(label: "evie.applescript", qos: .userInitiated)

    /// An AppleScript string literal: anything (page text, a message) is safe inside it.
    static func quote(_ s: String) -> String {
        "\"" + s.replacingOccurrences(of: "\\", with: "\\\\").replacingOccurrences(of: "\"", with: "\\\"") + "\""
    }

    final class Once: @unchecked Sendable {
        private let lock = NSLock()
        private var done = false
        func claim() -> Bool { lock.lock(); defer { lock.unlock() }; if done { return false }; done = true; return true }
    }

    static func run(_ source: String, timeout: Double = 6) async -> (ok: Bool, out: String) {
        let wrapped = "with timeout of \(Int(timeout)) seconds\n\(source)\nend timeout"
        return await withCheckedContinuation { cont in
            let once = Once()
            q.async {
                var err: NSDictionary?
                let r = NSAppleScript(source: wrapped)?.executeAndReturnError(&err)
                guard once.claim() else { return }
                if let e = err {
                    cont.resume(returning: (false, (e[NSAppleScript.errorMessage] as? String) ?? "AppleScript error"))
                } else {
                    cont.resume(returning: (true, r?.stringValue ?? ""))
                }
            }
            DispatchQueue.global().asyncAfter(deadline: .now() + timeout + 1) {
                if once.claim() { cont.resume(returning: (false, "Safari didn't answer (is Evie allowed to control it?)")) }
            }
        }
    }
}

/// Which Safari tab a page op goes to: one Evie opened or chose, never just "whatever's in front".
struct WebTab: Equatable {
    let window: Int
    let index: Int

    var ref: String { "tab \(index) of window id \(window)" }
}

// MARK: Eyes + fingers

@MainActor
final class Eyes {
    private var snapshot = ""
    private var axElements: [String: AXUIElement] = [:]
    private var axFrames: [String: CGRect] = [:]  // screen rects (top-left origin), for marked_shot
    private var webApp = ""  // the browser the w-ids belong to
    private var pid: pid_t = 0
    private var webTab: WebTab?  // the tab page ops go to (set by open_url / use_tab)

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
        case "open_url": return await openURL(a["url"]?.string ?? "", app: a["app"]?.string, front: a["front"]?.bool ?? false,
                                              newTab: a["new_tab"]?.bool ?? true, window: a["window"]?.int)
        case "use_tab": return await useTab(window: a["window"]?.int ?? 0, index: a["index"]?.int ?? 1, front: a["front"]?.bool ?? false)
        case "world": return await world()
        case "marked_shot": return await markedShot()
        // Only the core's fixed card templates arrive here (evie/computer/cards.py), every value quoted.
        case "applescript":
            let r = await AppleRun.run(a["source"]?.string ?? "", timeout: 10)
            return HandsOutcome(ok: r.ok, detail: r.ok ? "done" : r.out, data: ["out": r.out])
        case "menu": return menu(a["path"]?.string ?? "", app: a["app"]?.string)
        case "activate": return await activate(a["app"]?.string ?? "")
        case "screen_info": return await screenInfo(withPage: a["page"]?.bool ?? false)
        case "wait_page": return await waitPage(from: a["from_url"]?.string ?? "", seconds: 8)
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
        axFrames = [:]
        webApp = ""
        pid = target.processIdentifier
        if let browser = Self.browsers[target.bundleIdentifier ?? ""] {
            let r = await safariJS(WebReader.read)
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
                if let origin = Self.point(el) { axFrames[id] = CGRect(x: origin.x, y: origin.y, width: size.width, height: size.height) }
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
            let r = await safariJS(WebReader.click(id))
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
            let r = await safariJS(WebReader.setText(id, text, submit: submit))
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

    private func openURL(_ s: String, app name: String?, front: Bool, newTab: Bool, window: Int?) async -> HandsOutcome {
        guard let url = URL(string: s), ["http", "https", "whatsapp", "mailto", "spotify", "notion"].contains(url.scheme ?? "") else {
            return HandsOutcome(ok: false, detail: "that link isn't safe to open")
        }
        let web = url.scheme == "http" || url.scheme == "https"
        if web, (name ?? "Safari").lowercased() == "safari" {
            // A new tab in the window Evie was pointed at (or Safari's front window), and remember
            // it: every read and click after this goes to THIS tab, whatever else is in front.
            let u = AppleRun.quote(url.absoluteString)
            let win = window.map { "window id \($0)" } ?? "front window"
            let script = """
            tell application "Safari"
              if (count of windows) = 0 then
                make new document with properties {URL:\(u)}
                set w to front window
              else
                set w to \(win)
                if \(newTab) then
                  tell w to set current tab to (make new tab at end of tabs with properties {URL:\(u)})
                else
                  set URL of current tab of w to \(u)
                end if
              end if
              \(front ? "set index of w to 1\nactivate" : "")
              return ((id of w) as text) & "," & ((index of current tab of w) as text)
            end tell
            """
            let r = await AppleRun.run(script)
            let parts = r.out.split(separator: ",").compactMap { Int($0.trimmingCharacters(in: .whitespaces)) }
            guard r.ok, parts.count == 2 else { return HandsOutcome(ok: false, detail: "Safari: \(r.out)") }
            webTab = WebTab(window: parts[0], index: parts[1])
            return HandsOutcome(ok: true, detail: "opened", data: ["window": "\(parts[0])", "index": "\(parts[1])"])
        }
        let cfg = NSWorkspace.OpenConfiguration()
        cfg.activates = front
        if let n = name, let appURL = NSWorkspace.shared.urlForApplication(withBundleIdentifier: Self.bundle(for: n)) {
            _ = try? await NSWorkspace.shared.open([url], withApplicationAt: appURL, configuration: cfg)
        } else {
            _ = try? await NSWorkspace.shared.open(url, configuration: cfg)
        }
        return HandsOutcome(ok: true, detail: "opened")
    }

    /// Point Evie at one of Isaac's tabs ("this page", "my gmail"), bringing it up if asked.
    private func useTab(window: Int, index: Int, front: Bool) async -> HandsOutcome {
        let t = WebTab(window: window, index: index)
        let script = """
        tell application "Safari"
          set w to window id \(window)
          set current tab of w to tab \(index) of w
          \(front ? "set index of w to 1\nactivate" : "")
          return URL of tab \(index) of w
        end tell
        """
        let r = await AppleRun.run(script)
        guard r.ok else { return HandsOutcome(ok: false, detail: "that tab's gone") }
        webTab = t
        return HandsOutcome(ok: true, detail: "using that tab", data: ["url": r.out])
    }

    /// Everything on screen in one go: the app in front, running apps, windows front to back,
    /// every Safari tab, and selected text.
    private func world() async -> HandsOutcome {
        let front = NSWorkspace.shared.frontmostApplication
        let apps = NSWorkspace.shared.runningApplications.filter { $0.activationPolicy == .regular }
            .compactMap(\.localizedName)
        var windows: [[String: Any]] = []
        let list = (CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID)
            as? [[String: Any]]) ?? []
        var titles: [pid_t: [String]] = [:]
        for w in list where (w[kCGWindowLayer as String] as? Int) == 0 {
            guard let owner = w[kCGWindowOwnerName as String] as? String, owner != "Evie", owner != "EvieBar",
                  let pid = w[kCGWindowOwnerPID as String] as? pid_t else { continue }
            if titles[pid] == nil {  // window titles come from Accessibility (no Screen Recording needed)
                let root = AXUIElementCreateApplication(pid)
                let ws: [AXUIElement] = Self.attr(root, kAXWindowsAttribute) ?? []
                titles[pid] = ws.prefix(6).map { (Self.attr($0, kAXTitleAttribute) as String?) ?? "" }
            }
            let seenForApp = windows.filter { ($0["app"] as? String) == owner }.count
            let title = (titles[pid] ?? []).dropFirst(seenForApp).first ?? ""
            windows.append(["app": owner, "title": title])
            if windows.count >= 15 { break }
        }
        var tabs: [[String: Any]] = []
        if apps.contains("Safari") {
            let script = """
            tell application "Safari"
              set out to ""
              set n to 0
              repeat with w in windows
                set n to n + 1
                set ci to index of current tab of w
                repeat with t in tabs of w
                  set out to out & (id of w) & tab & n & tab & (index of t) & tab & (ci = (index of t)) & tab & (name of t) & tab & (URL of t) & linefeed
                end repeat
              end repeat
              return out
            end tell
            """
            let r = await AppleRun.run(script, timeout: 3)
            if r.ok {
                for line in r.out.split(separator: "\n") {
                    let f = line.split(separator: "\t", omittingEmptySubsequences: false).map(String.init)
                    guard f.count >= 6, let w = Int(f[0]), let o = Int(f[1]), let i = Int(f[2]) else { continue }
                    tabs.append(["window": w, "order": o, "index": i, "current": f[3] == "true", "title": f[4], "url": f[5]])
                }
            }
        }
        var selected = ""
        if let f = front {
            let root = AXUIElementCreateApplication(f.processIdentifier)
            if let focused: AXUIElement = Self.attr(root, kAXFocusedUIElementAttribute),
               let sel: String = Self.attr(focused, kAXSelectedTextAttribute) {
                selected = String(sel.prefix(2000))
            }
        }
        let body: [String: Any] = ["front_app": front?.localizedName ?? "", "apps": apps, "windows": windows,
                                   "tabs": tabs, "selected": selected]
        let json = (try? JSONSerialization.data(withJSONObject: body)).flatMap { String(data: $0, encoding: .utf8) } ?? "{}"
        return HandsOutcome(ok: true, detail: "\(windows.count) windows, \(tabs.count) tabs", data: ["world": json])
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

    private func activate(_ name: String) async -> HandsOutcome {
        if let target = app(named: name) {
            target.activate()
            return HandsOutcome(ok: true, detail: "brought \(target.localizedName ?? name) to the front")
        }
        // Not running: launch it, then wait (up to 5 s) until it has a window to work in.
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/open")
        p.arguments = ["-a", name]
        do { try p.run() } catch { return HandsOutcome(ok: false, detail: "couldn't open \(name)") }
        p.waitUntilExit()
        guard p.terminationStatus == 0 else { return HandsOutcome(ok: false, detail: "\(name) isn't on this Mac") }
        for _ in 0..<25 {
            try? await Task.sleep(for: .milliseconds(200))
            if let t = app(named: name) {
                let root = AXUIElementCreateApplication(t.processIdentifier)
                if (Self.attr(root, kAXFocusedWindowAttribute) as AXUIElement?) != nil { return HandsOutcome(ok: true, detail: "opened \(name)") }
            }
        }
        return HandsOutcome(ok: true, detail: "opened \(name)")
    }

    /// The last resort for apps that show little to Accessibility: a picture of the app's window
    /// with a numbered box over every element from the last observe. Needs Screen Recording.
    private func markedShot() async -> HandsOutcome {
        guard pid != 0, !axFrames.isEmpty else { return HandsOutcome(ok: false, detail: "nothing read yet") }
        let list = (CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID)
            as? [[String: Any]]) ?? []
        guard let w = list.first(where: { ($0[kCGWindowOwnerPID as String] as? pid_t) == pid && ($0[kCGWindowLayer as String] as? Int) == 0 }),
              let num = w[kCGWindowNumber as String] as? Int,
              let bd = w[kCGWindowBounds as String] as? NSDictionary,
              let bounds = CGRect(dictionaryRepresentation: bd) else { return HandsOutcome(ok: false, detail: "no window to look at") }
        let file = FileManager.default.temporaryDirectory.appendingPathComponent("evie-shot.png")
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/sbin/screencapture")
        p.arguments = ["-x", "-o", "-l", "\(num)", file.path]
        do { try p.run() } catch { return HandsOutcome(ok: false, detail: "couldn't take a screenshot") }
        p.waitUntilExit()
        guard let img = NSImage(contentsOf: file), let cg = img.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
            return HandsOutcome(ok: false, detail: "I need Screen Recording permission to look")
        }
        let scale = CGFloat(cg.width) / max(bounds.width, 1)
        let out = min(1.0, 1280 / CGFloat(cg.width))  // Qwen doesn't need more than 1280 px
        let W = Int(CGFloat(cg.width) * out), H = Int(CGFloat(cg.height) * out)
        guard let ctx = CGContext(data: nil, width: W, height: H, bitsPerComponent: 8, bytesPerRow: 0,
                                  space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else {
            return HandsOutcome(ok: false, detail: "couldn't draw")
        }
        ctx.draw(cg, in: CGRect(x: 0, y: 0, width: W, height: H))
        var marks: [String: String] = [:]
        let ns = NSGraphicsContext(cgContext: ctx, flipped: false)
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = ns
        var n = 0
        for (id, f) in axFrames.sorted(by: { Int($0.key.dropFirst()) ?? 0 < Int($1.key.dropFirst()) ?? 0 }) where f.intersects(bounds) {
            n += 1
            marks["\(n)"] = id
            let k = scale * out
            let r = CGRect(x: (f.minX - bounds.minX) * k, y: CGFloat(H) - (f.maxY - bounds.minY) * k, width: f.width * k, height: f.height * k)
            NSColor.systemRed.setStroke()
            let path = NSBezierPath(rect: r)
            path.lineWidth = 2
            path.stroke()
            let label = NSAttributedString(string: " \(n) ", attributes: [
                .font: NSFont.boldSystemFont(ofSize: 13), .foregroundColor: NSColor.white, .backgroundColor: NSColor.systemRed])
            label.draw(at: CGPoint(x: r.minX, y: max(0, r.maxY - 16)))
        }
        NSGraphicsContext.restoreGraphicsState()
        guard let marked = ctx.makeImage(),
              let png = NSBitmapImageRep(cgImage: marked).representation(using: .png, properties: [:]) else {
            return HandsOutcome(ok: false, detail: "couldn't draw")
        }
        let json = (try? JSONSerialization.data(withJSONObject: marks)).flatMap { String(data: $0, encoding: .utf8) } ?? "{}"
        return HandsOutcome(ok: true, detail: "\(n) boxes", data: ["png": png.base64EncodedString(), "marks": json])
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
            let r = await safariJS(withPage ? WebReader.pageText : "JSON.stringify({url: location.href, title: document.title, selected: String(getSelection()||'').slice(0,2000)})")
            if r.ok, let obj = try? JSONSerialization.jsonObject(with: Data(r.out.utf8)) as? [String: Any] {
                data["url"] = obj["url"] as? String ?? ""
                if let t = obj["text"] as? String { data["page_text"] = t }
                if let s = obj["selected"] as? String, !s.isEmpty { data["selected"] = s }
            }
        }
        return HandsOutcome(ok: true, detail: "ok", data: data)
    }

    /// Loaded, on the new address (when one was given), and 300 ms without the page changing.
    private func waitPage(from before: String, seconds: Double) async -> HandsOutcome {
        let end = Date().addingTimeInterval(seconds)
        try? await Task.sleep(for: .milliseconds(150))
        while Date() < end {
            let r = await safariJS(WebReader.probe)
            if r.ok, let o = try? JSONSerialization.jsonObject(with: Data(r.out.utf8)) as? [String: Any],
               PageSettle.done(ready: o["ready"] as? String ?? "", url: o["url"] as? String ?? "", before: before,
                               quietMs: (o["quiet"] as? Double) ?? 0) {
                return HandsOutcome(ok: true, detail: "loaded", data: ["url": o["url"] as? String ?? ""])
            }
            try? await Task.sleep(for: .milliseconds(120))
        }
        return HandsOutcome(ok: false, detail: "the page is taking too long")
    }

    // MARK: plumbing

    nonisolated static func attr<T>(_ el: AXUIElement, _ name: String) -> T? {
        var v: CFTypeRef?
        guard AXUIElementCopyAttributeValue(el, name as CFString, &v) == .success, let v else { return nil }
        return v as? T
    }

    nonisolated static func point(_ el: AXUIElement) -> CGPoint? {
        var v: CFTypeRef?
        guard AXUIElementCopyAttributeValue(el, kAXPositionAttribute as CFString, &v) == .success, let v,
              CFGetTypeID(v) == AXValueGetTypeID() else { return nil }
        var p = CGPoint.zero
        AXValueGetValue(v as! AXValue, .cgPoint, &p)
        return p
    }

    nonisolated static func size(_ el: AXUIElement) -> (width: Double, height: Double) {
        var v: CFTypeRef?
        guard AXUIElementCopyAttributeValue(el, kAXSizeAttribute as CFString, &v) == .success, let v,
              CFGetTypeID(v) == AXValueGetTypeID() else { return (0, 0) }
        var s = CGSize.zero
        AXValueGetValue(v as! AXValue, .cgSize, &s)
        return (Double(s.width), Double(s.height))
    }

    /// Runs a page script in Evie's tab (or Safari's front tab if she hasn't picked one).
    func safariJS(_ js: String) async -> (ok: Bool, out: String) {
        let target = webTab?.ref ?? "current tab of front window"
        let r = await AppleRun.run("tell application \"Safari\" to do JavaScript \(AppleRun.quote(js)) in \(target)")
        if !r.ok, webTab != nil, r.out.contains("Invalid index") || r.out.contains("Can’t get") {
            webTab = nil  // the tab was closed: fall back to the front tab
            return await AppleRun.run("tell application \"Safari\" to do JavaScript \(AppleRun.quote(js)) in current tab of front window")
        }
        return r
    }
}
