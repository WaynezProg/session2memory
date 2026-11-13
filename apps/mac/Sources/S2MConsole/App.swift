import AppKit
import SwiftUI

@main
struct S2MConsoleApp: App {
    init() {
        UserDefaults.standard.register(defaults: SettingKey.defaults)
    }

    var body: some Scene {
        WindowGroup("session2memory Console") {
            ContentView()
                .frame(minWidth: 1040, minHeight: 640)
                .onAppear {
                    // Started with `swift run` there is no app bundle: ask for a Dock icon
                    // and bring the window forward.
                    NSApplication.shared.setActivationPolicy(.regular)
                    NSApplication.shared.activate(ignoringOtherApps: true)
                }
        }
    }
}

enum Pane: String, CaseIterable, Identifiable {
    case memory, sessions, runs, query, settings

    var id: Pane { self }

    var title: String {
        switch self {
        case .memory: return "Memory"
        case .sessions: return "Sessions"
        case .runs: return "Runs"
        case .query: return "HKS Query"
        case .settings: return "Settings"
        }
    }

    var icon: String {
        switch self {
        case .memory: return "brain"
        case .sessions: return "bubble.left.and.bubble.right"
        case .runs: return "clock.arrow.circlepath"
        case .query: return "magnifyingglass"
        case .settings: return "gearshape"
        }
    }
}

struct ContentView: View {
    @State private var pane: Pane? = .memory

    var body: some View {
        NavigationSplitView {
            List(Pane.allCases, selection: $pane) { item in
                Label(item.title, systemImage: item.icon).tag(item)
            }
            .navigationSplitViewColumnWidth(min: 150, ideal: 170, max: 220)
        } detail: {
            detail
        }
    }

    @ViewBuilder
    private var detail: some View {
        switch pane ?? .memory {
        case .memory: MemoryPane()
        case .sessions: SessionsPane()
        case .runs: RunsPane()
        case .query: QueryPane()
        case .settings: SettingsPane()
        }
    }
}
