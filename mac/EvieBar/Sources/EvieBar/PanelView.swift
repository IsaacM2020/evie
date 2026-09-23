import SwiftUI

struct PanelView: View {
    @ObservedObject var model: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("Evie").font(.headline)
                Spacer()
                Circle().fill(statusColor).frame(width: 8, height: 8)
                Text(statusText).font(.caption).foregroundStyle(.secondary)
            }
            TextField("Type what you'd say to Evie", text: $model.input)
                .textFieldStyle(.roundedBorder)
                .onSubmit { Task { await model.send() } }
                .disabled(!model.online || model.busy)
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
            if model.busy { ProgressView().controlSize(.small) }
            if let err = model.error { Text(err).font(.caption).foregroundStyle(.red) }
            if let o = model.last { OutcomeCard(utterance: model.lastUtterance, outcome: o) }
            if model.calendarDenied {
                HStack {
                    Text("Calendar access off").font(.caption).foregroundStyle(.orange)
                    Spacer()
                    Button("Open Settings") { model.openCalendarSettings() }.controlSize(.small)
                }
            }
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
        !model.online ? "core offline" : (model.jevOk == false ? "Jev unreachable" : "ready (typed input)")
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
