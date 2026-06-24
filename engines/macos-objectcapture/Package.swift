// swift-tools-version:5.7
import PackageDescription

let package = Package(
    name: "caliber-recon",
    platforms: [.macOS(.v13)],
    targets: [
        .executableTarget(
            name: "caliber-recon",
            path: "Sources/CaliberRecon"
        )
    ]
)
