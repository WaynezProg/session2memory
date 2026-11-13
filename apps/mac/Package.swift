// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "S2MConsole",
    platforms: [.macOS(.v13)],
    targets: [
        .executableTarget(
            name: "S2MConsole",
            path: "Sources/S2MConsole"
        )
    ]
)
