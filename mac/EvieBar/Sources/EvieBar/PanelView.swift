import SwiftUI

struct PanelView: View {
    @ObservedObject var model: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("Evie").font(.headline)
                StatePill(state: model.online ? model.state : "offline")
                Spacer()
                Circle().fill(statusColor).frame(width: 8, height: 8)
                Text(statusText).font(.caption).foregroundStyle(.secondary)
            }
            if !model.axTrusted {
                Warning(text: "Turn on Accessibility for Evie so holding 🌐 works",
                        button: "Open Settings") { model.openSettings("Privacy_Accessibility") }
            }
            if model.micDenied {
                Warning(text: "Microphone access is off", button: "Open Settings") {
                    model.openSettings("Privacy_Microphone")
                }
            }
            if model.calendarDenied {
                Warning(text: "Calendar access off", button: "Open Settings") { model.openCalendarSettings() }
            }

            if model.heard.isEmpty && model.said.isEmpty {
                Text("Hold 🌐 and talk. Let go when you're done.").font(.callout).foregroundStyle(.secondary)
            } else {
                VStack(alignment: .leading, spacing: 6) {
                    if !model.heard.isEmpty { Line(who: "You", text: model.heard) }
                    if !model.verdict.isEmpty {
                        Text(model.verdict).font(.caption2).foregroundStyle(.tertiary)
                    }
                    if !model.said.isEmpty { Line(who: "Evie", text: model.said) }
                }
            }

            if let job = model.job {
                JobCard(job: job) { Task { await model.stopJob() } }
            } else if !model.lastJobSummary.isEmpty {
                Line(who: "Last job", text: model.lastJobSummary)
            }

            TextField("Or type to Evie", text: $model.input)
                .textFieldStyle(.roundedBorder)
                .onSubmit { Task { await model.send() } }
                .disabled(!model.online || model.busy)
            if let err = model.error { Text(err).font(.caption).foregroundStyle(.red) }

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
                        .controlSize(.small)
                        .disabled(!model.online || model.busy)
                    if let o = model.last { OutcomeCard(utterance: model.lastUtterance, outcome: o) }
                }
                .padding(.top, 6)
            }
            .font(.caption)

            Divider()
            Toggle("Open at login", isOn: Binding(
                get: { model.launchAtLogin },
                set: { model.setLaunchAtLogin($0) }
            ))
            Button("Quit Evie") { NSApplication.shared.terminate(nil) }
        }
        .padding(14)
    }

    private var statusColor: Color {
        !model.online ? .red : (model.jevOk == false ? .orange : .green)
    }

    private var statusText: String {
        !model.online ? "core offline" : (model.jevOk == false ? "Jev unreachable" : "ready")
    }
}

struct StatePill: View {
    let state: String

    var body: some View {
        Text(state.capitalized)
            .font(.caption2.bold())
            .padding(.horizontal, 7).padding(.vertical, 2)
            .background(color.opacity(0.18), in: Capsule())
            .foregroundStyle(color)
    }

    private var color: Color {
        switch state {
        case "listening": return .red
        case "thinking": return .orange
        case "speaking": return .blue
        case "working": return .purple
        case "offline": return .gray
        default: return .secondary
        }
    }
}

struct Line: View {
    let who: String
    let text: String

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(who).font(.caption2).foregroundStyle(.secondary)
            Text(text).font(.callout).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
        }
    }
}

struct Warning: View {
    let text: String
    let button: String
    let action: () -> Void

    var body: some View {
        HStack {
            Text(text).font(.caption).foregroundStyle(.orange)
            Spacer()
            Button(button, action: action).controlSize(.small)
        }
    }
}

struct JobCard: View {
    let job: JobView
    let stop: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Image(systemName: "gearshape.2").foregroundStyle(.purple)
                Text(job.goal).font(.callout.bold()).lineLimit(2)
                Spacer()
                Text(job.started, style: .timer).font(.caption).monospacedDigit().foregroundStyle(.secondary)
            }
            ForEach(Array(job.lines.enumerated()), id: \.offset) { _, line in
                Text("· \(line)").font(.caption).foregroundStyle(.secondary).lineLimit(1)
            }
            Button("Stop", role: .destructive, action: stop).controlSize(.small)
        }
        .padding(10)
        .background(Color.purple.opacity(0.08), in: RoundedRectangle(cornerRadius: 10))
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
        .background(Color.secondary.opacity(0.1), in: RoundedRectangle(cornerRadius: 10))
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
