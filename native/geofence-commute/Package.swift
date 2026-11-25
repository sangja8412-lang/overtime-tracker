// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "GeofenceCommute",
    platforms: [.iOS(.v14)],
    products: [
        .library(
            name: "GeofenceCommute",
            targets: ["GeofenceCommute"])
    ],
    dependencies: [
        .package(url: "https://github.com/ionic-team/capacitor-swift-pm.git", from: "7.0.0")
    ],
    targets: [
        .target(
            name: "GeofenceCommute",
            dependencies: [
                .product(name: "Capacitor", package: "capacitor-swift-pm")
            ],
            path: "ios/Sources",
            exclude: [],
            swiftSettings: [
                .enableUpcomingFeature("BareSlashRegexLiterals")
            ])
    ]
)
