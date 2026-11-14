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

/// A memory to show, from another pane.
struct MemoryTarget: Equatable {
    let project: String
    let id: String
}

/// Which pane is shown, and what one pane asked another to show.
@MainActor
final class AppState: ObservableObject {
    @Published var pane: Pane? = .memory
    @Published var memoryTarget: MemoryTarget?
    @Published var sessionTarget: String?
    /// Whether session2memory was found and has something to show (`detect()`).
    @Published var setup: SetupState = .checking
    @Published var s2mChoice: CommandChoice?
    @Published var hksChoice: CommandChoice?
    @Published var storePath: String?

    func show(memory id: String, in project: String) {
        memoryTarget = MemoryTarget(project: project, id: id)
        pane = .memory
    }

    func show(session key: String) {
        sessionTarget = key
        pane = .sessions
    }
}

struct ContentView: View {
    @StateObject private var app = AppState()

    var body: some View {
        NavigationSplitView {
            List(Pane.allCases, selection: $app.pane) { item in
                Label(item.title, systemImage: item.icon).tag(item)
            }
            .navigationSplitViewColumnWidth(min: 150, ideal: 170, max: 220)
        } detail: {
            detail
        }
        .environmentObject(app)
        .task { await app.detect() }
    }

    @ViewBuilder
    private var detail: some View {
        switch app.pane ?? .memory {
        case .memory, .sessions, .runs:
            // These read the store; until it can be read, say what to do instead.
            if app.setup == .ready {
                switch app.pane ?? .memory {
                case .sessions: SessionsPane()
                case .runs: RunsPane()
                default: MemoryPane()
                }
            } else {
                SetupView().navigationTitle((app.pane ?? .memory).title)
            }
        case .query: QueryPane()
        case .settings: SettingsPane()
        }
    }
}
