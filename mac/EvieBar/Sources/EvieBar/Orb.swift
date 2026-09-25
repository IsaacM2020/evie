import AppKit
import SwiftUI

// Evie on screen: a small glass capsule on the screen edge with one line in it (her mark). When
// there's something to show, it grows into a glass card at the same edge: what she heard and said,
// "Which one?" rows, a follow-up, a job with Stop, a Cancel window, or a box to type to her.
//
// The look (Isaac, 2026-09-24): C's liquid glass (clear, the wallpaper shows through, light on the
// edge) with A's calm (white type, small mono labels, ONE warm ember that only shows when she's
// doing something). No circle.
//
// Why it's built like this: the old pill crashed the app twice, both times inside SwiftUI's own
// mouse handling. So AppKit owns every mouse event: the panels handle clicks, drags and right-clicks
// themselves, SwiftUI never receives one (InertHostingView), and SwiftUI never sizes a window. What's
// tappable is decided by CardLayout, the same pure layout the view draws from, so a tap always
// lands on what's drawn there. The type box is a real AppKit text field.

enum OrbSide: String { case left, right }

// MARK: - Tokens

enum Ember {
    /// Light or dark glass (Isaac, 2026-09-24: follow the Mac, plus a toggle). Set by AppModel before
    /// it publishes the change, so every view that redraws reads the new colours.
    nonisolated(unsafe) static var dark = true
    static var ink: Color { tint(dark ? 0.95 : 0.88) }
    static var ink2: Color { tint(dark ? 0.58 : 0.55) }
    static let accent = Color(red: 1.0, green: 0.56, blue: 0.26)
    static var hair: Color { tint(0.12) }
    /// White on dark glass, near-black on light glass.
    static func tint(_ a: Double) -> Color { (dark ? Color.white : Color(red: 0.08, green: 0.08, blue: 0.1)).opacity(a) }
    static var onTint: Color { dark ? .black : .white }
    static var body: Color { dark ? Color.black.opacity(0.3) : Color.white.opacity(0.42) }

    /// "auto" follows the Mac's appearance; "light" / "dark" force one.
    static func resolve(_ setting: String, systemDark: Bool) -> Bool {
        setting == "dark" ? true : setting == "light" ? false : systemDark
    }
    static let live = Color(red: 0.2, green: 0.85, blue: 0.4)
    static let tagFont = Font.system(size: 9.5, weight: .medium, design: .monospaced)
}

// MARK: - Geometry (pure: the selftest checks it)

enum OrbGeometry {
    static let capW: CGFloat = 58, capH: CGFloat = 30  // the capsule
    static let pad: CGFloat = 12  // room for the glass edge and shadow
    static let markPanel = NSSize(width: capW + 2 * pad, height: capH + 2 * pad)
    static let edge: CGFloat = 12  // from the screen edge

    static func markFrame(center c: CGPoint) -> NSRect {
        NSRect(x: c.x - markPanel.width / 2, y: c.y - markPanel.height / 2, width: markPanel.width, height: markPanel.height)
    }

    /// The card grows out of the capsule: same outer edge, same middle (kept on screen).
    static func cardFrame(markCenter c: CGPoint, side: OrbSide, glass: CGSize, in screen: NSRect) -> NSRect {
        let w = glass.width + 2 * pad, h = glass.height + 2 * pad
        let x = side == .right ? c.x + capW / 2 + pad - w : c.x - capW / 2 - pad
        let y = min(max(c.y - h / 2, screen.minY), screen.maxY - h)
        return NSRect(x: x, y: y, width: w, height: h)
    }

    /// A rect laid out top-down inside the card's glass, in the card panel's AppKit coordinates.
    static func panelRect(_ r: CGRect, glassHeight h: CGFloat) -> NSRect {
        NSRect(x: pad + r.minX, y: pad + h - r.maxY, width: r.width, height: r.height)
    }
}

// MARK: - Motion (pure)

enum OrbSnap {
    /// Where momentum carries a thrown object (Apple's deceleration projection).
    static func project(_ v: CGFloat, rate: CGFloat = 0.998) -> CGFloat { v / 1000 * rate / (1 - rate) }

    /// Where the capsule comes to rest after a drag: the side edge nearer to where the throw was
    /// heading, height kept (clamped on screen).
    static func rest(center: CGPoint, velocity: CGVector, in frame: NSRect,
                     margin: CGFloat = OrbGeometry.edge) -> (side: OrbSide, center: CGPoint) {
        let rx = OrbGeometry.capW / 2, ry = OrbGeometry.capH / 2
        let px = center.x + project(velocity.dx), py = center.y + project(velocity.dy)
        let side: OrbSide = px < frame.midX ? .left : .right
        let x = side == .left ? frame.minX + margin + rx : frame.maxX - margin - rx
        let y = min(max(py, frame.minY + margin + ry), frame.maxY - margin - ry)
        return (side, CGPoint(x: x, y: y))
    }
}

/// A critically damped spring (damping 1.0): fast, and it never overshoots.
struct Spring {
    var response: Double = 0.35
    var damping: Double = 1.0

    func step(x: CGFloat, v: CGFloat, target: CGFloat, dt: Double) -> (CGFloat, CGFloat) {
        let w = 2 * Double.pi / response, k = w * w, c = 2 * damping * w
        var x = Double(x), v = Double(v)
        let n = 8, h = dt / Double(n)
        for _ in 0..<n {  // semi-implicit Euler in small steps: stable at 60-120 Hz
            v += (-k * (x - Double(target)) - c * v) * h
            x += v * h
        }
        return (CGFloat(x), CGFloat(v))
    }
}

// MARK: - What the card shows (pure)

struct OptionRow: Equatable, Decodable {
    let id: String
    var label: String = ""
    var meta: String? = nil
}

struct FollowCard: Equatable, Decodable {
    let id: String
    var about: String = ""
    var line: String = ""
    var ask: Bool = false
    var yes: Bool = false
}

enum OrbCard: Equatable {
    case none
    case chip(String)  // at rest: "SAX · 12M", "TEXT ONLY"
    case talk(tag: String, text: String)
    case typing(tag: String, reply: String?)  // text mode: her reply and a box to type to her
    case options(asked: String, rows: [OptionRow])
    case followup(FollowCard)
    case countdown(line: String)
    case job(goal: String, step: String?, count: String?)
    case list(tag: String, say: String, items: [String], first: Int)  // say it short, list it all

    var isChip: Bool { if case .chip = self { return true }; return false }

    /// Everything the card depends on, so the choice is a pure function the selftest can check.
    struct Inputs {
        var online = true
        var state = "idle"
        var heard = ""
        var said = ""
        var textMode = false
        var quietWhy = ""
        var typing = false
        var clickTalking = false
        var options: [OptionRow] = []
        var asked = ""
        var items: [String] = []  // a long answer's list (T19)
        var listFirst = 0
        var cardHover = false  // the pointer is on the card itself
        var countdown = false
        var followup: FollowCard? = nil
        var followupFresh = false
        var job: (goal: String, step: String?, count: String?)? = nil
        var hover = false
        var linger = false
        var note = ""
        var nextEvent: String? = nil
    }

    static func pick(_ s: Inputs) -> OrbCard {
        let textTag = s.quietWhy.isEmpty ? "Text only" : "\(s.quietWhy) · text only"
        if !s.online { return s.hover ? .talk(tag: "Offline", text: "Evie's core isn't running.") : .none }
        if s.typing { return .typing(tag: s.textMode ? textTag : "Type to Evie", reply: s.said.isEmpty ? nil : s.said) }
        if !s.options.isEmpty { return .options(asked: s.asked, rows: Array(s.options.prefix(3))) }
        if s.countdown, !s.said.isEmpty { return .countdown(line: s.said) }
        if s.state == "listening" { return .talk(tag: "Listening", text: s.clickTalking ? "Click me again to send." : "Go ahead…") }
        if s.state == "thinking" { return .talk(tag: "Thinking", text: s.heard.isEmpty ? "…" : s.heard) }
        if !s.items.isEmpty && (s.state == "speaking" || s.linger || s.cardHover) {
            return .list(tag: s.textMode ? textTag : (s.heard.isEmpty ? "Evie" : s.heard), say: s.said, items: s.items, first: s.listFirst)
        }
        if (s.state == "speaking" || s.linger) && !s.said.isEmpty {
            return s.textMode ? .typing(tag: textTag, reply: s.said) : .talk(tag: s.heard.isEmpty ? "Evie" : s.heard, text: s.said)
        }
        if !s.note.isEmpty { return .talk(tag: "Evie", text: s.note) }
        if let f = s.followup, s.followupFresh || s.hover { return .followup(f) }
        if let j = s.job, s.hover || s.linger { return .job(goal: j.goal, step: j.step, count: j.count) }
        if s.hover { return .talk(tag: s.textMode ? textTag : "Evie", text: s.textMode ? "Click to type to me." : "Click to talk, or hold ⌃⌥.") }
        if let e = s.nextEvent { return .chip(e) }
        if s.textMode { return .chip("Text only") }
        return .none
    }
}

enum Hit: Equatable { case row(Int), yes, later, no, stop, cancel, field, mark }

/// One layout for drawing AND tapping. Rects are top-down inside the card's glass.
enum CardLayout {
    static let width: CGFloat = 372
    static let column: CGFloat = 52  // her line, at the screen-edge end of the card
    static let chipColumn: CGFloat = 40
    static let top: CGFloat = 13, inset: CGFloat = 16, bottom: CGFloat = 13
    static let tagH: CGFloat = 12, titleH: CGFloat = 18, bodyH: CGFloat = 38, replyH: CGFloat = 56
    static let rowH: CGFloat = 31, rowGap: CGFloat = 5, fieldH: CGFloat = 28, pillH: CGFloat = 26
    static let pillW: CGFloat = 78, pillGap: CGFloat = 6
    static let itemH: CGFloat = 32, itemGap: CGFloat = 3, listVisible = 7, listMaxH: CGFloat = 360

    /// The first item a list can start at (the last page still shows `listVisible` items).
    static func listStart(_ first: Int, count: Int) -> Int { max(0, min(first, count - listVisible)) }

    enum Role: Equatable {
        case tag(String), count(String), title(String), body(String), reply(String), hint(String), chip(String)
        case row(Int, OptionRow), pill(Hit, String, primary: Bool), field(String), bar
        case item(Int, String)  // a list line, numbered from 1
    }

    struct Part: Equatable {
        let role: Role
        let rect: CGRect
    }

    static func chipWidth(_ text: String) -> CGFloat { min(240, 26 + CGFloat(text.count) * 7.2) + chipColumn }

    static func glass(_ c: OrbCard) -> CGSize {
        switch c {
        case .none: return .zero
        case .chip(let t): return CGSize(width: chipWidth(t), height: OrbGeometry.capH)
        default: return CGSize(width: width, height: (parts(c, side: .right).map(\.rect.maxY).max() ?? 0) + bottom)
        }
    }

    /// Where her line sits in the card: the end nearest the screen edge.
    static func columnRect(_ c: OrbCard, side: OrbSide) -> CGRect {
        let g = glass(c)
        let w = c.isChip ? chipColumn : column
        return CGRect(x: side == .right ? g.width - w : 0, y: 0, width: w, height: c.isChip ? g.height : min(g.height, 44))
    }

    static func parts(_ c: OrbCard, side: OrbSide) -> [Part] {
        let shift: CGFloat = c.isChip ? (side == .left ? chipColumn : 0) : (side == .left ? column : 0)
        let right = width - column - 10 + shift  // content's right edge
        let x = inset + shift, w = right - x
        var out: [Part] = []
        var y = top
        func add(_ r: Role, _ h: CGFloat, gap: CGFloat, x rx: CGFloat? = nil, w rw: CGFloat? = nil) {
            out.append(Part(role: r, rect: CGRect(x: rx ?? x, y: y, width: rw ?? w, height: h)))
            y += h + gap
        }
        switch c {
        case .none:
            return []
        case .chip(let t):
            out.append(Part(role: .chip(t), rect: CGRect(x: 12 + shift, y: 0, width: chipWidth(t) - chipColumn - 12, height: OrbGeometry.capH)))
        case .talk(let tag, let text):
            add(.tag(tag), tagH, gap: 6)
            add(.body(text), bodyH, gap: 0)
        case .typing(let tag, let reply):
            add(.tag(tag), tagH, gap: 8)
            if let r = reply { add(.reply(r), replyH, gap: 8) }
            add(.field("Ask Evie…"), fieldH, gap: 0)
        case .options(let asked, let rows):
            add(.tag("Which one?"), tagH, gap: 8)
            add(.title(asked), titleH, gap: 8)
            for (i, r) in rows.enumerated() {
                add(.row(i, r), rowH, gap: i == rows.count - 1 ? 8 : rowGap, x: x - 5, w: w + 5)
            }
            add(.hint("Say it or tap one"), 15, gap: 0)
        case .followup(let f):
            add(.tag(f.about == "overheard" ? "Heard earlier" : f.about == "stuck" ? "Stuck?" : "Evie"), tagH, gap: 7)
            add(.body(f.line), bodyH, gap: 8)
            if f.ask {
                out.append(Part(role: .field("Type the answer…"), rect: CGRect(x: x, y: y, width: w - pillW - pillGap, height: fieldH)))
                out.append(Part(role: .pill(.no, "Not now", primary: false), rect: CGRect(x: right - pillW, y: y + 1, width: pillW, height: pillH)))
                y += fieldH
            } else {
                let labels: [(Hit, String, Bool)] = f.yes ? [(.yes, "Yes", true), (.later, "Later", false), (.no, "No", false)]
                                                          : [(.no, "OK", true)]
                for (i, (h, l, p)) in labels.enumerated() {
                    out.append(Part(role: .pill(h, l, primary: p),
                                    rect: CGRect(x: x + CGFloat(i) * (pillW + pillGap), y: y, width: pillW, height: pillH)))
                }
                y += pillH
            }
        case .countdown(let line):
            add(.tag("Going ahead in a moment"), tagH, gap: 7)
            add(.body(line), bodyH, gap: 8)
            out.append(Part(role: .bar, rect: CGRect(x: x, y: y + pillH / 2 - 1.5, width: w - pillW - 12, height: 3)))
            out.append(Part(role: .pill(.cancel, "Cancel", primary: true), rect: CGRect(x: right - pillW, y: y, width: pillW, height: pillH)))
            y += pillH
        case .job(let goal, let step, let count):
            add(.tag("Claude Code"), tagH, gap: 7)
            if let n = count { out.append(Part(role: .count(n), rect: CGRect(x: right - 120, y: top, width: 120, height: tagH))) }
            add(.title(goal), titleH, gap: 7)
            out.append(Part(role: .body(step ?? "Working on it…"), rect: CGRect(x: x, y: y, width: w - pillW - 10, height: pillH)))
            out.append(Part(role: .pill(.stop, "Stop", primary: false), rect: CGRect(x: right - pillW, y: y, width: pillW, height: pillH)))
            y += pillH
        case .list(let tag, let say, let items, let first):
            add(.tag(tag), tagH, gap: 6)
            add(.body(say), bodyH, gap: 8)
            let start = listStart(first, count: items.count)
            let shown = items.dropFirst(start).prefix(listVisible)
            for (i, t) in zip(shown.indices, shown) {
                add(.item(i + 1, t), itemH, gap: i == shown.indices.last ? 8 : itemGap)
            }
            let more = items.count - start - shown.count
            if more > 0 { add(.hint("Scroll for \(more) more"), 15, gap: 0) }
            else if start > 0 { add(.hint("That's all \(items.count)"), 15, gap: 0) }
            else { y -= 8 }
        }
        return out
    }

    /// A choice (Yes, a row) only counts once the card has been up this long: a click meant for the
    /// capsule can't land on a button that just appeared under it (2026-09-24 18:25:36, a job he
    /// never asked for). Stopping things always counts at once.
    static let settleS: TimeInterval = 0.6
    static func counts(_ hit: Hit, shownFor s: TimeInterval) -> Bool {
        switch hit {
        case .yes, .later, .no, .row: return s >= settleS
        case .stop, .cancel, .field, .mark: return true
        }
    }

    /// What's tappable, in card-glass coordinates (her line counts as the mark: click to talk).
    static func hits(_ c: OrbCard, side: OrbSide) -> [(Hit, CGRect)] {
        var out: [(Hit, CGRect)] = parts(c, side: side).compactMap { p in
            switch p.role {
            case .row(let i, _): return (.row(i), p.rect)
            case .pill(let h, _, _): return (h, p.rect)
            case .field: return (.field, p.rect)
            default: return nil
            }
        }
        if c != .none { out.append((.mark, columnRect(c, side: side))) }
        return out
    }
}

// MARK: - Views

/// Her mark: one line. White and still at rest; ember while she's doing something.
struct EvieLine: View {
    let state: String
    let online: Bool
    let level: CGFloat
    let progress: Double?
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        let moving = online && ["listening", "speaking", "thinking", "working"].contains(state) && !reduceMotion
        TimelineView(.animation(minimumInterval: 1.0 / 30, paused: !moving)) { tl in
            let t = moving ? tl.date.timeIntervalSinceReferenceDate : 0
            Canvas { ctx, size in EvieLine.draw(ctx: ctx, size: size, state: online ? state : "offline", t: t,
                                                 level: level, progress: progress) }
        }
        .frame(width: 34, height: 22)
        .shadow(color: online && state != "idle" ? Ember.accent.opacity(0.8) : Ember.tint(online ? 0.45 : 0), radius: 3)
        .accessibilityLabel("Evie, \(online ? state : "offline")")
    }

    static func draw(ctx: GraphicsContext, size: CGSize, state: String, t: Double, level: CGFloat, progress: Double?) {
        let mid = size.height / 2, len: CGFloat = 24, x0 = (size.width - len) / 2
        func line(_ from: CGFloat, _ to: CGFloat, _ color: Color, dash: [CGFloat] = []) {
            var p = Path()
            p.move(to: CGPoint(x: x0 + from, y: mid))
            p.addLine(to: CGPoint(x: x0 + to, y: mid))
            ctx.stroke(p, with: .color(color), style: StrokeStyle(lineWidth: 2, lineCap: .round, dash: dash))
        }
        switch state {
        case "listening", "speaking":
            let amp = 2 + 7 * min(1, max(0.15, level))
            var p = Path()
            for i in 0...40 {
                let f = CGFloat(i) / 40
                let env = sin(.pi * f)  // still at both ends, alive in the middle
                let y = mid - env * amp * CGFloat(sin(Double(f) * 13 + t * 9) * 0.7 + sin(Double(f) * 23 - t * 6) * 0.3)
                let pt = CGPoint(x: x0 + f * len, y: y)
                if i == 0 { p.move(to: pt) } else { p.addLine(to: pt) }
            }
            ctx.stroke(p, with: .color(Ember.accent), style: StrokeStyle(lineWidth: 1.8, lineCap: .round, lineJoin: .round))
        case "thinking":  // a short ember dash travelling along the line
            line(0, len, Ember.tint(0.22))
            let a = CGFloat((sin(t * 3.2) + 1) / 2) * (len - 8)
            line(a, a + 8, Ember.accent)
        case "working":
            line(0, len, Ember.tint(0.22))
            if let p = progress {
                line(0, max(2, len * CGFloat(p)), Ember.accent)
            } else {
                let a = CGFloat((t * 0.8).truncatingRemainder(dividingBy: 1)) * (len + 8) - 8
                line(max(0, a), min(len, a + 8), Ember.accent)
            }
        case "offline":
            line(0, len, Ember.tint(0.35), dash: [2.5, 2.5])
        default:
            line(2, len - 2, Ember.tint(0.9))
        }
    }
}

/// Liquid glass: the real glass material, a clear dark body so white type always reads, and light
/// catching the edge.
struct GlassBody: View {
    let size: CGSize
    let radius: CGFloat
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency

    var body: some View {
        let shape = RoundedRectangle(cornerRadius: radius, style: .continuous)
        ZStack {
            shape.fill(reduceTransparency ? Color(white: Ember.dark ? 0.12 : 0.94) : Ember.body)
            shape.fill(LinearGradient(colors: [.white.opacity(0.10), .clear], startPoint: .top, endPoint: UnitPoint(x: 0.5, y: 0.55)))
            shape.strokeBorder(LinearGradient(colors: [.white.opacity(0.45), .white.opacity(0.07), .white.opacity(0.18)],
                                              startPoint: .topLeading, endPoint: .bottomTrailing), lineWidth: 1)
        }
        .frame(width: size.width, height: size.height)
        .glassEffect(.clear, in: shape)
        // The shadow is its own blurred copy of the shape, behind the glass. `.shadow` on the glass
        // itself took the glass layer's rectangle: a grey box the size of the whole panel, always
        // on (Isaac's screenshot, 2026-09-24).
        .background(shape.fill(Color.black.opacity(reduceTransparency ? 0 : 0.28)).blur(radius: 7).offset(y: 4))
    }
}

/// The capsule at rest.
struct OrbMarkView: View {
    @ObservedObject var model: AppModel

    var body: some View {
        let size = CGSize(width: OrbGeometry.capW, height: OrbGeometry.capH)
        ZStack {
            GlassBody(size: size, radius: OrbGeometry.capH / 2)
            EvieLine(state: model.orbState, online: model.online, level: model.lineLevel, progress: model.jobProgress)
        }
        .overlay(alignment: .topTrailing) {
            if !model.followups.isEmpty {  // something waiting for him
                Circle().fill(Ember.accent).frame(width: 7, height: 7).offset(x: -6, y: 4)
            } else if model.earsMode == "live" {  // Live mic on: a small green light, like the Mac's own
                Circle().fill(Ember.live).frame(width: 6, height: 6).offset(x: -7, y: 5)
            }
        }
        .frame(width: OrbGeometry.markPanel.width, height: OrbGeometry.markPanel.height)
        .environment(\.colorScheme, model.darkUI ? .dark : .light)  // the glass follows the Theme menu too
    }
}

/// The card: laid out by CardLayout, so what's drawn is exactly what's tappable.
struct OrbCardView: View {
    @ObservedObject var model: AppModel
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        let card = model.card, side = model.orbSide
        let g = CardLayout.glass(card)
        let col = CardLayout.columnRect(card, side: side)
        ZStack(alignment: .topLeading) {
            if card != .none {
                GlassBody(size: g, radius: card.isChip ? OrbGeometry.capH / 2 : 22)
                if !card.isChip && g.height <= 90 {
                    Rectangle().fill(Ember.hair).frame(width: 1, height: g.height - 24)
                        .offset(x: side == .right ? col.minX : col.maxX, y: 12)
                } else if card.isChip {
                    Rectangle().fill(Ember.hair).frame(width: 1, height: 14)
                        .offset(x: side == .right ? col.minX : col.maxX, y: (g.height - 14) / 2)
                }
                EvieLine(state: model.orbState, online: model.online, level: model.lineLevel, progress: model.jobProgress)
                    .frame(width: col.width, height: col.height)
                    .offset(x: col.minX, y: col.minY)
                ForEach(Array(CardLayout.parts(card, side: side).enumerated()), id: \.offset) { _, p in
                    part(p.role).frame(width: p.rect.width, height: p.rect.height, alignment: .leading)
                        .offset(x: p.rect.minX, y: p.rect.minY)
                }
            }
        }
        .frame(width: g.width, height: g.height, alignment: .topLeading)
        .frame(width: g.width + 2 * OrbGeometry.pad, height: g.height + 2 * OrbGeometry.pad)
        .animation(reduceMotion ? nil : .spring(response: 0.35, dampingFraction: 1.0), value: model.said)
        .environment(\.colorScheme, model.darkUI ? .dark : .light)
    }

    @ViewBuilder private func part(_ r: CardLayout.Role) -> some View {
        switch r {
        case .tag(let t):
            Text(t.uppercased()).font(Ember.tagFont).tracking(0.9).foregroundStyle(Ember.ink2).lineLimit(1)
        case .count(let t):
            Text(t).font(Ember.tagFont).foregroundStyle(Ember.accent).frame(maxWidth: .infinity, alignment: .trailing)
        case .title(let t):
            Text(t).font(.system(size: 13.5, weight: .semibold)).foregroundStyle(Ember.ink).lineLimit(1)
        case .body(let t):
            Text(t).font(.system(size: 14)).foregroundStyle(Ember.ink).lineLimit(2).truncationMode(.tail)
                .frame(maxHeight: .infinity, alignment: .topLeading)
        case .reply(let t):
            Text(t).font(.system(size: 14)).foregroundStyle(Ember.ink).lineSpacing(2).lineLimit(3)
                .frame(maxHeight: .infinity, alignment: .topLeading)
        case .hint(let t):
            Text(t).font(.system(size: 11)).foregroundStyle(Ember.ink2)
        case .chip(let t):
            Text(t.uppercased()).font(.system(size: 10.5, weight: .medium, design: .monospaced)).tracking(0.6)
                .foregroundStyle(Ember.ink).lineLimit(1)
        case .row(let i, let o):
            HStack(spacing: 10) {
                Text("\(i + 1)").font(.system(size: 10.5, weight: .semibold, design: .monospaced))
                    .foregroundStyle(i == 0 ? Color.black : Ember.ink2).frame(width: 19, height: 19)
                    .background(Circle().fill(i == 0 ? Ember.accent : Ember.tint(0.08)))
                VStack(alignment: .leading, spacing: 1) {
                    Text(o.label).font(.system(size: 12.5, weight: .medium)).foregroundStyle(Ember.ink).lineLimit(1)
                    if let m = o.meta, !m.isEmpty {
                        Text(m).font(.system(size: 11)).foregroundStyle(Ember.ink2).lineLimit(1)
                    }
                }
                Spacer(minLength: 0)
            }
            .padding(.horizontal, 5)
            .frame(maxHeight: .infinity)
            .background(RoundedRectangle(cornerRadius: 10, style: .continuous).fill(i == 0 ? Ember.tint(0.07) : .clear))
        case .pill(_, let label, let primary):
            Text(label).font(.system(size: 11.5, weight: .semibold))
                .foregroundStyle(primary ? Color.black : Ember.ink)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .background(Capsule().fill(primary ? Ember.accent : Ember.tint(0.09)))
                .overlay(Capsule().strokeBorder(Ember.tint(primary ? 0 : 0.14), lineWidth: 1))
        case .field(let placeholder):
            HStack {
                Text(model.typing ? "" : placeholder).font(.system(size: 12.5)).foregroundStyle(Ember.ink2)
                Spacer()
                Image(systemName: "arrow.up").font(.system(size: 10, weight: .bold)).foregroundStyle(Ember.onTint)
                    .frame(width: 20, height: 20).background(Circle().fill(Ember.tint(0.9)))
            }
            .padding(.leading, 11).padding(.trailing, 4)
            .frame(maxHeight: .infinity)
            .background(Capsule().fill(Ember.tint(0.07)))
            .overlay(Capsule().strokeBorder(Ember.tint(0.12), lineWidth: 1))
        case .bar:
            CountdownBar(until: model.countdownUntil, total: model.countdownTotal)
        case .item(let n, let t):
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text("\(n)").font(.system(size: 10.5, weight: .semibold, design: .monospaced)).foregroundStyle(Ember.ink2)
                    .frame(width: 16, alignment: .trailing)
                Text(t).font(.system(size: 12.5)).foregroundStyle(Ember.ink).lineLimit(2).truncationMode(.tail)
                Spacer(minLength: 0)
            }
            .frame(maxHeight: .infinity, alignment: .topLeading)
        }
    }
}

/// The say-stop window as a draining ember line (Cancel is next to it).
struct CountdownBar: View {
    let until: Date?
    let total: Double

    var body: some View {
        TimelineView(.animation(minimumInterval: 1.0 / 20, paused: until == nil)) { tl in
            let left = max(0, (until ?? tl.date).timeIntervalSince(tl.date))
            GeometryReader { g in
                ZStack(alignment: .leading) {
                    Capsule().fill(Ember.tint(0.12))
                    Capsule().fill(Ember.accent).frame(width: g.size.width * CGFloat(total > 0 ? left / total : 0))
                        .shadow(color: Ember.accent.opacity(0.8), radius: 3)
                }
            }
        }
    }
}

// MARK: - AppKit: panels that own the mouse

/// A hosting view that never takes part in mouse handling: no hit testing, no tracking areas, no
/// hover dispatch. That's the code path both crashes came from.
final class InertHostingView<Content: View>: NSHostingView<Content> {
    override func hitTest(_ point: NSPoint) -> NSView? { nil }
    override func updateTrackingAreas() {
        super.updateTrackingAreas()
        trackingAreas.forEach(removeTrackingArea)
    }
    override func mouseMoved(with event: NSEvent) {}
    override func mouseEntered(with event: NSEvent) {}
    override func mouseExited(with event: NSEvent) {}
    override var acceptsFirstResponder: Bool { false }
}

/// Holds the SwiftUI content and reports hover over its whole area (AppKit tracking only).
final class HoverContainer: NSView {
    var onHover: ((Bool) -> Void)?
    private var area: NSTrackingArea?

    override func updateTrackingAreas() {
        super.updateTrackingAreas()
        if let a = area { removeTrackingArea(a) }
        let a = NSTrackingArea(rect: bounds.insetBy(dx: OrbGeometry.pad - 2, dy: OrbGeometry.pad - 2),
                               options: [.mouseEnteredAndExited, .activeAlways, .inVisibleRect], owner: self, userInfo: nil)
        addTrackingArea(a)
        area = a
    }
    override func mouseEntered(with event: NSEvent) { onHover?(true) }
    override func mouseExited(with event: NSEvent) { onHover?(false) }
}

protocol OrbMouse: AnyObject {
    func down(_ e: NSEvent, in panel: OrbPanel)
    func dragged(_ e: NSEvent)
    func up(_ e: NSEvent, in panel: OrbPanel)
    func menu(_ e: NSEvent, in panel: OrbPanel)
    func scroll(_ e: NSEvent)
}

final class OrbPanel: NSPanel {
    weak var mouse: OrbMouse?
    var allowKey = false  // only while he's typing to her
    var typingField: NSTextField?

    override var canBecomeKey: Bool { allowKey }
    override var canBecomeMain: Bool { false }

    override func sendEvent(_ e: NSEvent) {
        if let f = typingField, !f.isHidden, e.type == .leftMouseDown || e.type == .leftMouseUp || e.type == .leftMouseDragged,
           f.frame.contains(e.locationInWindow) {
            super.sendEvent(e)  // clicks inside the type box are the text field's own
            return
        }
        switch e.type {
        case .leftMouseDown: mouse?.down(e, in: self)
        case .leftMouseDragged: mouse?.dragged(e)
        case .leftMouseUp: mouse?.up(e, in: self)
        case .rightMouseDown: mouse?.menu(e, in: self)
        case .scrollWheel: mouse?.scroll(e)  // pages a long list (the hosting view never sees the mouse)
        default: super.sendEvent(e)
        }
    }

    static func make(size: NSSize) -> OrbPanel {
        let p = OrbPanel(contentRect: NSRect(origin: .zero, size: size), styleMask: [.borderless, .nonactivatingPanel],
                         backing: .buffered, defer: false)
        p.isFloatingPanel = true
        p.level = .floating
        p.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary, .ignoresCycle]
        p.backgroundColor = .clear
        p.isOpaque = false
        p.hasShadow = false
        p.hidesOnDeactivate = false
        p.acceptsMouseMovedEvents = false
        p.becomesKeyOnlyIfNeeded = true
        return p
    }
}

/// The type box: plain AppKit, styled to sit inside the drawn field.
final class TypeField: NSTextField {
    var onSubmit: ((String) -> Void)?
    var onCancel: (() -> Void)?

    override func cancelOperation(_ sender: Any?) { onCancel?() }

    static func make() -> TypeField {
        let f = TypeField()
        f.isBordered = false
        f.drawsBackground = false
        f.focusRingType = .none
        f.font = .systemFont(ofSize: 12.5)
        f.recolor()
        f.cell?.usesSingleLineMode = true
        f.cell?.isScrollable = true
        f.target = f
        f.action = #selector(submit)
        f.isHidden = true
        return f
    }

    /// Ink on the frosted light glass, white on the dark one (follows Ember.dark).
    func recolor() {
        let ink: NSColor = Ember.dark ? .white : NSColor(red: 0.08, green: 0.08, blue: 0.1, alpha: 1)
        guard textColor != ink else { return }
        textColor = ink
        placeholderAttributedString = NSAttributedString(string: "Ask Evie…", attributes: [
            .foregroundColor: ink.withAlphaComponent(0.5), .font: NSFont.systemFont(ofSize: 12.5)])
    }

    @objc private func submit() {
        let t = stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        stringValue = ""
        if !t.isEmpty { onSubmit?(t) }
    }
}

@MainActor
final class OrbController: NSObject, OrbMouse {
    private var markPanel: OrbPanel?
    private var cardPanel: OrbPanel?
    private var field: TypeField?
    private weak var model: AppModel?
    private var center = CGPoint.zero
    private var shown: OrbCard = .none
    private var shownAt: TimeInterval = 0  // uptime when `shown` changed (NSEvent.timestamp's clock)
    private var observers: [Any] = []
    // drag state
    private var downAt: CGPoint?
    private var centerAtDown = CGPoint.zero
    private var dragging = false
    private var downHit: Hit?
    private var samples: [(t: TimeInterval, p: CGPoint)] = []
    // snap animation
    private var anim: Timer?
    private var vel = CGVector.zero
    private var target = CGPoint.zero

    private static let key = "orbCenter"

    func show(_ model: AppModel) {
        self.model = model
        if markPanel == nil { build(model) }
        markPanel?.orderFrontRegardless()
        refresh()
    }

    func hide() {
        markPanel?.orderOut(nil)
        cardPanel?.orderOut(nil)
        shown = .none
    }

    private func build(_ model: AppModel) {
        let mark = OrbPanel.make(size: OrbGeometry.markPanel)
        mark.mouse = self
        let mc = HoverContainer(frame: NSRect(origin: .zero, size: OrbGeometry.markPanel))
        let mh = InertHostingView(rootView: OrbMarkView(model: model))
        mh.sizingOptions = []
        mh.frame = mc.bounds
        mc.addSubview(mh)
        mc.onHover = { [weak model] h in model?.orbHover = h }
        mark.contentView = mc

        let big = NSSize(width: CardLayout.width + 2 * OrbGeometry.pad, height: 260)
        let card = OrbPanel.make(size: big)
        card.mouse = self
        let cc = HoverContainer(frame: NSRect(origin: .zero, size: big))
        cc.autoresizesSubviews = true
        let ch = InertHostingView(rootView: OrbCardView(model: model))
        ch.sizingOptions = []
        ch.frame = cc.bounds
        ch.autoresizingMask = [.width, .height]
        cc.addSubview(ch)
        let f = TypeField.make()
        f.onSubmit = { [weak model] t in model?.submitTyped(t) }
        f.onCancel = { [weak model] in model?.closeTyping() }
        cc.addSubview(f)
        cc.onHover = { [weak model] h in model?.bubbleHover = h }
        card.contentView = cc
        card.typingField = f
        card.alphaValue = 0

        markPanel = mark
        cardPanel = card
        field = f
        center = savedCenter()
        model.orbSide = OrbSnap.rest(center: center, velocity: .zero, in: screenFrame(for: center)).side
        place(center)
        observers.append(model.objectWillChange.sink { [weak self] _ in
            DispatchQueue.main.async { MainActor.assumeIsolated { self?.refresh() } }
        })
        let n = NotificationCenter.default.addObserver(forName: NSApplication.didChangeScreenParametersNotification,
                                                       object: nil, queue: .main) { [weak self] _ in
            MainActor.assumeIsolated { self?.settle(velocity: .zero) }
        }
        observers.append(n)
    }

    /// Show the card the model wants (or the capsule at rest). The panel is sized here, by AppKit,
    /// never by SwiftUI.
    private func refresh() {
        guard let model, let card = cardPanel, let mark = markPanel, mark.isVisible || card.isVisible else { return }
        let want = model.card
        field?.recolor()
        placeField(want)
        guard want != shown else { return }
        let was = shown
        shown = want
        shownAt = ProcessInfo.processInfo.systemUptime
        let reduce = NSWorkspace.shared.accessibilityDisplayShouldReduceMotion
        if want != .none {
            card.setFrame(cardFrame(want), display: true)
            card.orderFrontRegardless()
        }
        NSAnimationContext.runAnimationGroup({ ctx in
            ctx.duration = reduce ? 0.12 : (was == .none || want == .none ? 0.2 : 0.12)
            ctx.timingFunction = CAMediaTimingFunction(name: want == .none ? .easeIn : .easeOut)
            card.animator().alphaValue = want == .none ? 0 : 1
            mark.animator().alphaValue = want == .none ? 1 : 0  // the capsule grows into the card
        }, completionHandler: { [weak self] in
            MainActor.assumeIsolated {
                guard let self else { return }
                if self.shown == .none { self.cardPanel?.orderOut(nil) }
                self.markPanel?.ignoresMouseEvents = self.shown != .none
            }
        })
    }

    private func cardFrame(_ c: OrbCard) -> NSRect {
        OrbGeometry.cardFrame(markCenter: center, side: model?.orbSide ?? .right, glass: CardLayout.glass(c),
                              in: screenFrame(for: center))
    }

    /// The real text field sits exactly on the drawn field while he's typing.
    private func placeField(_ c: OrbCard) {
        guard let model, let f = field, let panel = cardPanel else { return }
        let rect = CardLayout.hits(c, side: model.orbSide).first { $0.0 == .field }?.1
        guard model.typing, let r = rect else {
            if !f.isHidden {
                f.isHidden = true
                panel.allowKey = false
                panel.resignKey()
            }
            return
        }
        let pr = OrbGeometry.panelRect(r, glassHeight: CardLayout.glass(c).height)
        f.frame = NSRect(x: pr.minX + 11, y: pr.minY + (pr.height - 18) / 2, width: pr.width - 11 - 28, height: 18)
        if f.isHidden {
            f.isHidden = false
            panel.allowKey = true
            panel.makeKeyAndOrderFront(nil)
            panel.makeFirstResponder(f)
        }
    }

    private func place(_ c: CGPoint) {
        center = c
        markPanel?.setFrame(OrbGeometry.markFrame(center: c), display: false)
        if shown != .none { cardPanel?.setFrame(cardFrame(shown), display: false) }
    }

    // MARK: mouse

    func down(_ e: NSEvent, in panel: OrbPanel) {
        anim?.invalidate()
        let p = e.locationInWindow
        downHit = nil
        if panel === markPanel {
            let cap = NSRect(x: OrbGeometry.pad, y: OrbGeometry.pad, width: OrbGeometry.capW, height: OrbGeometry.capH)
            guard cap.insetBy(dx: -4, dy: -4).contains(p) else { downAt = nil; return }
            downHit = .mark
        } else if let model {
            let h = CardLayout.glass(shown).height
            downHit = CardLayout.hits(shown, side: model.orbSide)
                .first { OrbGeometry.panelRect($0.1, glassHeight: h).insetBy(dx: -3, dy: -3).contains(p) }?.0
        }
        downAt = NSEvent.mouseLocation
        centerAtDown = center
        dragging = false
        samples = [(e.timestamp, NSEvent.mouseLocation)]
    }

    func dragged(_ e: NSEvent) {
        guard let start = downAt else { return }
        let now = NSEvent.mouseLocation
        if !dragging, hypot(now.x - start.x, now.y - start.y) > 3 { dragging = true }
        guard dragging else { return }
        place(CGPoint(x: centerAtDown.x + now.x - start.x, y: centerAtDown.y + now.y - start.y))  // 1:1, keeps the grab offset
        samples.append((e.timestamp, now))
        samples = samples.filter { e.timestamp - $0.t < 0.1 }
    }

    func up(_ e: NSEvent, in panel: OrbPanel) {
        defer { downAt = nil; dragging = false; downHit = nil }
        guard downAt != nil, let model else { return }
        if dragging {
            var v = CGVector.zero
            if let a = samples.first, let b = samples.last, b.t - a.t > 0.005 {
                v = CGVector(dx: (b.p.x - a.p.x) / (b.t - a.t), dy: (b.p.y - a.p.y) / (b.t - a.t))
            }
            settle(velocity: v)
            return
        }
        guard let hit = downHit, CardLayout.counts(hit, shownFor: e.timestamp - shownAt) else { return }
        model.tap(hit, on: shown)
    }

    private var wheel: CGFloat = 0

    /// Wheel or two-finger scroll over a list card: one item per 24 points of travel.
    func scroll(_ e: NSEvent) {
        guard let model, case .list(_, _, let items, _) = shown else { return }
        wheel += e.hasPreciseScrollingDeltas ? e.scrollingDeltaY : e.scrollingDeltaY * 12
        let step = Int(wheel / 24)
        guard step != 0 else { return }
        wheel -= CGFloat(step) * 24
        model.listFirst = CardLayout.listStart(model.listFirst - step, count: items.count)
    }

    func menu(_ e: NSEvent, in panel: OrbPanel) {
        guard let model, let view = panel.contentView else { return }
        let m = NSMenu()
        func item(_ title: String, on: Bool = false, in menu: NSMenu? = nil, _ action: @escaping () -> Void) {
            let i = NSMenuItem(title: title, action: #selector(MenuAction.fire), keyEquivalent: "")
            let a = MenuAction(action)
            i.target = a
            i.representedObject = a  // the item keeps its action alive
            i.state = on ? .on : .off
            (menu ?? m).addItem(i)
        }
        for (mode, title) in [("auto", "Answers: by my calendar"), ("voice", "Answers: out loud"), ("text", "Answers: text only (⌃⌥T)")] {
            item(title, on: model.quietSetting == mode) { model.setOutput(mode) }
        }
        m.addItem(.separator())
        if model.earsMode != nil {
            for (mode, title) in [("live", "Live mic"), ("shadow", "Shadow (decide, don't act)"), ("off", "Mic off")] {
                item(title, on: model.earsMode == mode) { Task { await model.setEarsMode(mode) } }
            }
            m.addItem(.separator())
        }
        let pro = NSMenu()
        for (name, title) in AppModel.proactiveSources {
            item(title, on: model.proactiveOn(name), in: pro) { model.setProactive(name, !model.proactiveOn(name)) }
        }
        let proItem = NSMenuItem(title: "Bring things up", action: nil, keyEquivalent: "")
        proItem.submenu = pro
        m.addItem(proItem)
        let theme = NSMenu()
        for (mode, title) in [("auto", "Auto (follow the Mac)"), ("light", "Light"), ("dark", "Dark")] {
            item(title, on: model.themeSetting == mode, in: theme) { model.setTheme(mode) }
        }
        let themeItem = NSMenuItem(title: "Theme", action: nil, keyEquivalent: "")
        themeItem.submenu = theme
        m.addItem(themeItem)
        item("Show her work on screen", on: model.showWork) { model.setShowWork(!model.showWork) }
        if let rec = model.recording {
            item("Record for tuning", on: rec) { Task { await model.setRecording(!rec) } }
        }
        m.addItem(.separator())
        item("Hide Evie") { model.setShowPill(false) }
        item("Quit Evie") { NSApp.terminate(nil) }
        m.popUp(positioning: nil, at: e.locationInWindow, in: view)
    }

    // MARK: snapping

    /// Throw the capsule to the edge its momentum points at, carrying the drag's velocity into the spring.
    private func settle(velocity: CGVector) {
        guard let model else { return }
        let frame = screenFrame(for: center)
        let rest = OrbSnap.rest(center: center, velocity: velocity, in: frame)
        if rest.side != model.orbSide { model.orbSide = rest.side }
        target = rest.center
        vel = velocity
        UserDefaults.standard.set([rest.center.x, rest.center.y], forKey: Self.key)
        if NSWorkspace.shared.accessibilityDisplayShouldReduceMotion {
            place(target)
            return
        }
        anim?.invalidate()
        let spring = Spring()
        anim = Timer.scheduledTimer(withTimeInterval: 1.0 / 120, repeats: true) { [weak self] t in
            MainActor.assumeIsolated {
                guard let self else { t.invalidate(); return }
                // X and Y are separate springs, so a diagonal throw stays smooth.
                let (x, vx) = spring.step(x: self.center.x, v: self.vel.dx, target: self.target.x, dt: 1.0 / 120)
                let (y, vy) = spring.step(x: self.center.y, v: self.vel.dy, target: self.target.y, dt: 1.0 / 120)
                self.vel = CGVector(dx: vx, dy: vy)
                self.place(CGPoint(x: x, y: y))
                if hypot(x - self.target.x, y - self.target.y) < 0.5, hypot(vx, vy) < 5 {
                    self.place(self.target)
                    t.invalidate()
                }
            }
        }
    }

    private func screenFrame(for p: CGPoint) -> NSRect {
        (NSScreen.screens.first { $0.frame.contains(p) } ?? NSScreen.main)?.visibleFrame
            ?? NSRect(x: 0, y: 0, width: 1440, height: 900)
    }

    private func savedCenter() -> CGPoint {
        if let xy = UserDefaults.standard.array(forKey: Self.key) as? [Double], xy.count == 2 {
            let p = CGPoint(x: xy[0], y: xy[1])
            return OrbSnap.rest(center: p, velocity: .zero, in: screenFrame(for: p)).center
        }
        let f = screenFrame(for: CGPoint(x: 1, y: 1))
        return CGPoint(x: f.maxX - OrbGeometry.edge - OrbGeometry.capW / 2, y: f.maxY - 120)
    }

    // MARK: selftest hooks

    var panelsForTest: [OrbPanel] { [markPanel, cardPanel].compactMap { $0 } }
}

final class MenuAction: NSObject {
    private let run: () -> Void
    init(_ run: @escaping () -> Void) { self.run = run }
    @objc func fire() { run() }
}

// MARK: - Stress (selftest)

/// Builds the real panels and hammers them: mouse moves, drags and clicks straight into the panels
/// while the state and the card flip at 60 Hz. The old pill died in exactly this situation.
@MainActor
enum OrbStress {
    static func run(seconds: Double) -> Bool {
        _ = NSApplication.shared
        let model = AppModel(preview: true)
        model.online = true
        model.clickTalkEnabled = false  // never start the mic in a test
        model.tapsEnabled = false  // nor call the core
        let c = OrbController()
        c.show(model)
        let states = ["idle", "listening", "thinking", "speaking", "working"]
        let end = Date().addingTimeInterval(seconds)
        var i = 0
        while Date() < end {
            i += 1
            model.state = states[i % states.count]
            model.heard = i % 3 == 0 ? "" : "open the newest networkchuck video \(i)"
            model.said = i % 2 == 0 ? "Playing it now." : ""
            model.options = i % 11 < 3 ? [OptionRow(id: "a", label: "One", meta: "2 days ago"), OptionRow(id: "b", label: "Two")] : []
            model.followups = i % 13 < 4 ? [FollowCard(id: "f", about: "tasks", line: "Bio email due today. Want help?", yes: true)] : []
            model.quietMode = i % 17 < 5 ? "text" : "voice"
            model.micLevel = Float(i % 10) / 10
            model.orbHover = i % 7 < 3
            for p in c.panelsForTest {
                let loc = NSPoint(x: CGFloat((i * 13) % 380), y: CGFloat((i * 7) % 200))
                for type in [NSEvent.EventType.mouseMoved, .leftMouseDown, .leftMouseDragged, .leftMouseUp] {
                    if let e = NSEvent.mouseEvent(with: type, location: loc, modifierFlags: [], timestamp: Date().timeIntervalSince1970,
                                                  windowNumber: p.windowNumber, context: nil, eventNumber: i, clickCount: 1,
                                                  pressure: 1) {
                        p.sendEvent(e)
                    }
                }
                p.contentView?.subviews.first?.mouseMoved(with: NSEvent.mouseEvent(
                    with: .mouseMoved, location: loc, modifierFlags: [], timestamp: 0, windowNumber: p.windowNumber,
                    context: nil, eventNumber: i, clickCount: 0, pressure: 0)!)
            }
            RunLoop.main.run(until: Date().addingTimeInterval(1.0 / 60))
        }
        c.hide()
        return true
    }
}
