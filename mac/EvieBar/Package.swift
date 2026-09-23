// swift-tools-version:5.10
import PackageDescription

let package = Package(
    name: "EvieBar",
    platforms: [.macOS("26.0")],  // Liquid Glass (glassEffect) needs macOS 26
    targets: [
        .executableTarget(name: "EvieBar", path: "Sources/EvieBar")
    ]
)
