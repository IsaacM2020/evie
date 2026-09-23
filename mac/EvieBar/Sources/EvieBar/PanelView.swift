import SwiftUI

// Liquid Glass lives on the controls layer (orb, pills, buttons, input), all inside one
// GlassEffectContainer so neighbouring glass blends. Content (what you said, what she said,
// job steps) is plain text on quiet fills, never glass on glass.
struct PanelView: View {
    @ObservedObject var model: AppModel
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    private var shownState: String { model.online ? model.state : "offline" }

    var body: some View {
        GlassEffectContainer(spacing: 14) {
            VStack(alignment: .leading, spacing: 14) {
                header
                warnings
                conversation
                if let job = model.job {
                    JobCard(job: job) { Task { await model.stopJob() } }
                        .transition(.opacity.combined(with: .scale(scale: 0.97, anchor: .top)))
                } else if !model.lastJobSummary.isEmpty {
                    Bubble(who: "Last job", text: model.lastJobSummary, tint: .purple)
                }
                input
                testTools
                footer
            }
            .padding(16)
        }
        .frame(width: 360)
        .animation(reduceMotion ? nil : .spring(response: 0.35, dampingFraction: 1.0), value: model.state)
        .animation(reduceMotion ? nil : .spring(response: 0.35, dampingFraction: 1.0), value: model.job)
        .animation(reduceMotion ? nil : .spring(response: 0.35, dampingFraction: 1.0), value: model.said)
    }

    // MARK: header: the orb shows what Evie is doing right now

    private var header: some View {
        HStack(spacing: 12) {
            Orb(state: shownState, animate: !reduceMotion)
            VStack(alignment: .leading, spacing: 2) {
                Text("Evie").font(.system(.title3, design: .rounded).weight(.semibold)).tracking(-0.2)
                Text(stateLine).font(.callout).foregroundStyle(.secondary).contentTransition(.opacity)
            }
            Spacer()
            HealthDot(online: model.online, jevOk: model.jevOk)
        }
    }

    private var stateLine: String {
        switch shownState {
        case "listening": return "Listening…"
        case "thinking": return "Thinking…"
        case "speaking": return "Talking"
        case "working": return "Working on a job"
        case "offline": return "Core offline"
        default: return model.jevOk == false ? "Can't reach Jev" : "Hold 🌐 to talk"
        }
    }

    @ViewBuilder private var warnings: some View {
        if !model.axTrusted {
            Warning(text: "Allow Accessibility for 🌐 to work") { model.openSettings("Privacy_Accessibility") }
        }
        if model.micDenied {
            Warning(text: "Microphone is off") { model.openSettings("Privacy_Microphone") }
        }
        if model.calendarDenied {
            Warning(text: "Calendar is off") { model.openCalendarSettings() }
        }
    }

    // MARK: conversation: content, not chrome

    @ViewBuilder private var conversation: some View {
        if model.heard.isEmpty && model.said.isEmpty {
            Text("Ask anything, or hand her real work: “Evie, fix the chase bug in my cricket model.”")
                .font(.callout).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        } else {
            VStack(alignment: .leading, spacing: 8) {
                if !model.heard.isEmpty { Bubble(who: "You", text: model.heard, tint: .secondary) }
                if !model.verdict.isEmpty {
                    Text(model.verdict).font(.caption2).foregroundStyle(.tertiary).padding(.leading, 4)
                }
                if !model.said.isEmpty {
                    Bubble(who: "Evie", text: model.said, tint: .blue)
                        .transition(.opacity.combined(with: .offset(y: 6)))
                }
            }
        }
    }

    private var input: some View {
        TextField("Or type to Evie", text: $model.input)
            .textFieldStyle(.plain)
            .padding(.horizontal, 14).padding(.vertical, 9)
            .glassEffect(.regular.interactive(), in: Capsule())
            .onSubmit { Task { await model.send() } }
            .disabled(!model.online || model.busy)
            .overlay(alignment: .bottomLeading) {
                if let err = model.error {
                    Text(err).font(.caption).foregroundStyle(.red).offset(y: 18).padding(.leading, 14)
                }
            }
            .padding(.bottom, model.error == nil ? 0 : 14)
    }

    private var testTools: some View {
        DisclosureGroup("Test tools") {
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    Picker("", selection: $model.speaker) {
                        Text("Me").tag("isaac")
                        Text("Someone else").tag("other")
                        Text("Unknown").tag("unknown")
                    }
                    .pickerStyle(.segmented)
                    .labelsHidden()
                    Toggle("In a call", isOn: $model.inCall).toggleStyle(.checkbox)
                }
                Button("Dry run: show the verdict only") { Task { await model.dryRun() } }
                    .buttonStyle(.glass)
                    .controlSize(.small)
                    .disabled(!model.online || model.busy)
                if let o = model.last { OutcomeCard(utterance: model.lastUtterance, outcome: o) }
            }
            .padding(.top, 6)
        }
        .font(.caption)
        .foregroundStyle(.secondary)
    }

    private var footer: some View {
        HStack {
            Toggle("Open at login", isOn: Binding(
                get: { model.launchAtLogin },
                set: { model.setLaunchAtLogin($0) }
            ))
            .toggleStyle(.switch)
            .controlSize(.mini)
            .font(.caption)
            Spacer()
            Button("Quit") { NSApplication.shared.terminate(nil) }
                .buttonStyle(.glass)
                .controlSize(.small)
        }
    }
}

// The state orb: a tinted glass circle whose symbol moves only while she's listening or talking.
struct Orb: View {
    let state: String
    let animate: Bool

    var body: some View {
        Image(systemName: symbol)
            .font(.system(size: 18, weight: .semibold))
            .foregroundStyle(.white)
            .symbolEffect(.variableColor.iterative.dimInactiveLayers, isActive: animate && active)
            .contentTransition(.symbolEffect(.replace))
            .frame(width: 44, height: 44)
            .glassEffect(.regular.tint(color.opacity(0.85)).interactive(), in: Circle())
    }

    private var active: Bool { state == "listening" || state == "speaking" || state == "thinking" }

    private var symbol: String {
        switch state {
        case "listening": return "mic.fill"
        case "thinking": return "ellipsis"
        case "speaking": return "waveform"
        case "working": return "gearshape.2.fill"
        case "offline": return "bolt.horizontal.circle"
        default: return "waveform"
        }
    }

    private var color: Color {
        switch state {
        case "listening": return .red
        case "thinking": return .orange
        case "speaking": return .blue
        case "working": return .purple
        case "offline": return .gray
        default: return .indigo
        }
    }
}

struct HealthDot: View {
    let online: Bool
    let jevOk: Bool?

    var body: some View {
        Circle()
            .fill(!online ? Color.red : (jevOk == false ? .orange : .green))
            .frame(width: 8, height: 8)
            .help(!online ? "Core offline" : (jevOk == false ? "Jev unreachable" : "All good"))
    }
}

struct Bubble: View {
    let who: String
    let text: String
    let tint: Color

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(who).font(.caption2.weight(.semibold)).foregroundStyle(.secondary).textCase(.uppercase)
            Text(text).font(.body).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 12).padding(.vertical, 9)
        .background(tint.opacity(0.08), in: RoundedRectangle(cornerRadius: 14, style: .continuous))
    }
}

struct Warning: View {
    let text: String
    let action: () -> Void

    var body: some View {
        HStack {
            Image(systemName: "exclamationmark.circle.fill").foregroundStyle(.orange)
            Text(text).font(.caption)
            Spacer()
            Button("Settings", action: action).buttonStyle(.glass).controlSize(.small)
        }
        .padding(.horizontal, 12).padding(.vertical, 6)
        .glassEffect(.regular.tint(.orange.opacity(0.18)), in: Capsule())
    }
}

struct JobCard: View {
    let job: JobView
    let stop: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .firstTextBaseline) {
                Image(systemName: "gearshape.2.fill").foregroundStyle(.purple)
                Text(job.goal).font(.callout.weight(.semibold)).lineLimit(2)
                Spacer()
                Text(job.started, style: .timer).font(.caption.monospacedDigit()).foregroundStyle(.secondary)
            }
            ForEach(Array(job.lines.enumerated()), id: \.offset) { _, line in
                Text(line).font(.caption).foregroundStyle(.secondary).lineLimit(1)
            }
            HStack {
                Spacer()
                Button(role: .destructive, action: stop) { Label("Stop", systemImage: "stop.fill") }
                    .buttonStyle(.glassProminent)
                    .tint(.red)
                    .controlSize(.small)
            }
        }
        .padding(12)
        .background(Color.purple.opacity(0.08), in: RoundedRectangle(cornerRadius: 16, style: .continuous))
    }
}

struct OutcomeCard: View {
    let utterance: String
    let outcome: OutcomeDTO

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text("\u{201C}\(utterance)\u{201D}").font(.caption).foregroundStyle(.secondary).lineLimit(2)
            HStack {
                Text(outcome.action.uppercased())
                    .font(.system(.title3, design: .rounded).bold())
                    .foregroundStyle(color)
                Text(outcome.reason).font(.caption).foregroundStyle(.secondary)
                if outcome.followup {
                    Text("follow-up")
                        .font(.caption2)
                        .padding(.horizontal, 6).padding(.vertical, 2)
                        .background(Color.blue.opacity(0.15), in: Capsule())
                }
            }
            if let d = outcome.decision {
                row("for Evie", pct(d.forEvie))
                row("route", "\(d.route) (\(pct(d.routeConfidence)))")
                row("complete", pct(d.complete))
                row("has event", pct(d.hasEvent))
                row("Jev", "\(Int(d.latencyMs)) ms")
            }
        }
        .padding(10)
        .background(Color.secondary.opacity(0.08), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
    }

    private var color: Color {
        outcome.action == "act" ? .green : (outcome.action == "clarify" ? .orange : .secondary)
    }

    private func pct(_ x: Double) -> String { "\(Int((x * 100).rounded()))%" }

    private func row(_ k: String, _ v: String) -> some View {
        HStack {
            Text(k).foregroundStyle(.secondary)
            Spacer()
            Text(v).monospacedDigit()
        }
        .font(.caption)
    }
}
