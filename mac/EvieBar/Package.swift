// swift-tools-version:5.10
import PackageDescription

let package = Package(
    name: "EvieBar",
    platforms: [.macOS(.v14)],
    targets: [
        .executableTarget(name: "EvieBar", path: "Sources/EvieBar")
    ]
)
