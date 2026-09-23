import SwiftUI

@main
struct EvieBarApp: App {
    @StateObject private var model = AppModel()

    init() {
        if CommandLine.arguments.contains("--selftest") {
            exit(SelfTest.run() ? 0 : 1)
        }
        if let i = CommandLine.arguments.firstIndex(of: "--snapshot"), i + 1 < CommandLine.arguments.count {
            MainActor.assumeIsolated { Snapshot.run(to: CommandLine.arguments[i + 1]) }
            exit(0)
        }
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
