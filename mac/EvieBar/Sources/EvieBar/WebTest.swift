import AppKit
import WebKit

// `EvieBar --webtest page.html [js-file]`: runs the page reader (or a script) in an invisible
// WebKit view, the same engine Safari uses, and prints what it returns. For the selftest and the
// Phase 3b evals: no window, nothing on Isaac's screen.
@MainActor
enum WebTest {
    final class Waiter: NSObject, WKNavigationDelegate {
        var loaded = false
        func webView(_ w: WKWebView, didFinish n: WKNavigation!) { loaded = true }
    }

    /// Steps: "read", "click:w5", "set:w1:some text", "submit:w1:some text", "eval:<js>". Prints each result.
    static func steps(html: URL, _ steps: [String]) -> [String] {
        steps.map { step -> String in
            let parts = step.split(separator: ":", maxSplits: 2).map(String.init)
            switch parts[0] {
            case "read": return WebReader.read
            case "click": return WebReader.click(parts[1])
            case "set": return WebReader.setText(parts[1], parts.count > 2 ? parts[2] : "", submit: false)
            case "submit": return WebReader.setText(parts[1], parts.count > 2 ? parts[2] : "", submit: true)
            default: return parts.dropFirst().joined(separator: ":")
            }
        }
    }

    static func run(html: URL, script: String) -> String {
        run(html: html, scripts: [script]).joined(separator: "\n")
    }

    static func run(html: URL, scripts: [String]) -> [String] {
        let web = WKWebView(frame: NSRect(x: 0, y: 0, width: 1280, height: 800))
        let waiter = Waiter()
        web.navigationDelegate = waiter
        web.loadFileURL(html, allowingReadAccessTo: html.deletingLastPathComponent())
        let end = Date().addingTimeInterval(10)
        while !waiter.loaded && Date() < end { RunLoop.main.run(until: Date().addingTimeInterval(0.05)) }
        var outs: [String] = []
        for script in scripts {
            var out = "error: page didn't load"
            var done = !waiter.loaded
            web.evaluateJavaScript(script) { result, error in
                out = result.map { "\($0)" } ?? "error: \(String(describing: error))"
                done = true
            }
            while !done && Date() < end { RunLoop.main.run(until: Date().addingTimeInterval(0.05)) }
            outs.append(out)
        }
        return outs
    }
}
