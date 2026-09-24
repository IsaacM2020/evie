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
                if model.earsMode != nil { OpenMicCard(model: model) }
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
        default: return model.jevOk == false ? "Can't reach Jev" : "Hold ⌃⌥ to talk · ⌃⌥⌘ Live"
        }
    }

    @ViewBuilder private var warnings: some View {
        if !model.axTrusted {
            Warning(text: "Allow Accessibility for ⌃⌥ to work") { model.openSettings("Privacy_Accessibility") }
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
            // Open at login is launchd's job now (ops/com.isaac.evie.app.plist: starts at login,
            // comes back after a crash). This switch keeps sentences to tune the ears instead.
            if let rec = model.recording {
                Toggle("Record for tuning", isOn: Binding(get: { rec }, set: { on in Task { await model.setRecording(on) } }))
                    .toggleStyle(.switch)
                    .controlSize(.mini)
                    .font(.caption)
                    .help("Keeps your open-mic sentences on this Mac for 7 days so Claude can measure the ears. Never other people's.")
            }
            Toggle("Orb", isOn: Binding(get: { model.showPill }, set: { model.setShowPill($0) }))
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
    var size: CGFloat = 44

    var body: some View {
        Image(systemName: symbol)
            .font(.system(size: size * 0.41, weight: .semibold))
            .foregroundStyle(.white)
            .symbolEffect(.variableColor.iterative.dimInactiveLayers, isActive: animate && active)
            .contentTransition(.symbolEffect(.replace))
            .frame(width: size, height: size)
            // Solid colour under the glass: a glass tint alone goes grey in windows that never
            // become key (the menu bar panel, the pill), and the colour IS the state.
            .background(color.opacity(0.85).gradient, in: Circle())
            .glassEffect(.regular.interactive(), in: Circle())
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

// Open mic controls: mode, how well Evie knows Isaac's voice, enrollment, and the shadow log
// (what she WOULD have done on the open mic, while it's on trial).
struct OpenMicCard: View {
    @ObservedObject var model: AppModel

    static let sentences = [
        "Evie, what's on my calendar tomorrow morning?",
        "Play some lofi and turn the volume down a little.",
        "Remind me to send the iGEM slides to Mr Tan on Friday.",
        "The quick brown fox jumps over the lazy dog by the river.",
        "I think the chase model is off by one in the second innings.",
        "Set a timer for twenty minutes, then remind me to stretch.",
    ]

    private var vp: VoicePrintDTO { model.voiceprint }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Label("Open mic", systemImage: "ear").font(.callout.weight(.semibold))
                Spacer()
                Picker("", selection: Binding(get: { model.earsMode ?? "off" },
                                              set: { m in Task { await model.setEarsMode(m) } })) {
                    Text("Off").tag("off")
                    Text("Shadow").tag("shadow")
                    Text("Live").tag("live")
                }
                .pickerStyle(.segmented)
                .labelsHidden()
                .frame(width: 180)
            }
            Text(modeLine).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            HStack {
                Image(systemName: vp.ready ? "person.wave.2.fill" : "person.wave.2")
                    .foregroundStyle(vp.ready ? Color.green : Color.secondary)
                Text(voiceLine).font(.caption).foregroundStyle(.secondary)
                Spacer()
                Button(model.enrolling ? "Done" : "Teach my voice") { Task { await model.toggleEnroll() } }
                    .buttonStyle(.glass)
                    .controlSize(.small)
            }
            if model.enrolling { enrollCard }
            if !model.shadowLog.isEmpty {
                DisclosureGroup("Would have done (\(model.shadowLog.count))") {
                    VStack(alignment: .leading, spacing: 6) {
                        ForEach(model.shadowLog) { row in
                            VStack(alignment: .leading, spacing: 1) {
                                Text(row.would).font(.caption.weight(.semibold))
                                Text("\u{201C}\(row.text)\u{201D}").font(.caption).foregroundStyle(.secondary).lineLimit(2)
                            }
                        }
                    }
                    .padding(.top, 4)
                }
                .font(.caption)
            }
        }
        .padding(12)
        .background(Color.secondary.opacity(0.07), in: RoundedRectangle(cornerRadius: 16, style: .continuous))
    }

    private var modeLine: String {
        switch model.earsMode {
        case "shadow": return "Listening on trial: she only notes what she would have done."
        case "live": return "Listening for you. Other voices are ignored on the Mac."
        default: return vp.ready ? "Hold ⌃⌥ to talk, ⌃⌥⌘ flips Live on or off." : "Hold ⌃⌥ to talk. Teach her your voice to unlock Live."
        }
    }

    private var voiceLine: String {
        vp.ready ? "Knows your voice (\(vp.clips) clips)" : "Learning your voice \(min(vp.clips, 8))/8"
    }

    private var enrollCard: some View {
        let done = max(0, vp.clips - model.enrollStartClips)
        let i = min(done, Self.sentences.count - 1)
        return VStack(alignment: .leading, spacing: 6) {
            Text(done >= Self.sentences.count ? "All done. Tap Done." : "Hold ⌃⌥ and read this out loud (\(done + 1) of \(Self.sentences.count))")
                .font(.caption).foregroundStyle(.secondary)
            if done < Self.sentences.count {
                Text(Self.sentences[i]).font(.body.weight(.medium)).fixedSize(horizontal: false, vertical: true)
            }
            ProgressView(value: Double(min(done, Self.sentences.count)), total: Double(Self.sentences.count))
        }
        .padding(10)
        .background(Color.blue.opacity(0.08), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
    }
}
