import ServiceManagement
import SwiftUI

@main
struct EvieBarApp: App {
    @StateObject private var model = AppModel()

    init() {
        if CommandLine.arguments.contains("--selftest") {
            exit(SelfTest.run() ? 0 : 1)
        }
        if let i = CommandLine.arguments.firstIndex(of: "--webtest"), i + 1 < CommandLine.arguments.count {
            let args = CommandLine.arguments
            let url = URL(fileURLWithPath: args[i + 1])
            let steps = Array(args.dropFirst(i + 2))
            MainActor.assumeIsolated {
                let scripts = steps.isEmpty ? [WebReader.read] : WebTest.steps(html: url, steps)
                WebTest.run(html: url, scripts: scripts).forEach { print($0) }
            }
            exit(0)
        }
        if let i = CommandLine.arguments.firstIndex(of: "--snapshot"), i + 1 < CommandLine.arguments.count {
            MainActor.assumeIsolated { Snapshot.run(to: CommandLine.arguments[i + 1]) }
            exit(0)
        }
        // launchd starts Evie at login and after a crash. A second copy (the old login item, a
        // double-click) would fight over the mic and keys, so it bows out quietly.
        let me = NSRunningApplication.current
        let others = NSRunningApplication.runningApplications(withBundleIdentifier: Bundle.main.bundleIdentifier ?? "")
            .filter { $0.processIdentifier != me.processIdentifier }
        if !others.isEmpty { exit(0) }
        if SMAppService.mainApp.status == .enabled { try? SMAppService.mainApp.unregister() }
    }

    var body: some Scene {
        MenuBarExtra {
            PanelView(model: model)
        } label: {
            Image(nsImage: MenuIcon.image(model.iconState))
        }
        .menuBarExtraStyle(.window)
    }
}
