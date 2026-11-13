// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "S2MConsole",
    platforms: [.macOS(.v13)],
    targets: [
        .executableTarget(
            name: "S2MConsole",
            path: "Sources/S2MConsole"
        ),
        .testTarget(
            name: "S2MConsoleTests",
            dependencies: ["S2MConsole"],
            path: "Tests/S2MConsoleTests"
        ),
    ]
)
