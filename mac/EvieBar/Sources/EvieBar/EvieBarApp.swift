import SwiftUI

@main
struct EvieBarApp: App {
    @StateObject private var model = AppModel()

    init() {
        if CommandLine.arguments.contains("--selftest") {
            exit(SelfTest.run() ? 0 : 1)
        }
    }

    var body: some Scene {
        MenuBarExtra {
            PanelView(model: model).frame(width: 340)
        } label: {
            Image(systemName: model.iconName)
        }
        .menuBarExtraStyle(.window)
    }
}
