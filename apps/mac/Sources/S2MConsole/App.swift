import AppKit
import SwiftUI

@main
struct S2MConsoleApp: App {
    init() {
        UserDefaults.standard.register(defaults: SettingKey.defaults)
    }

    var body: some Scene {
        WindowGroup("S2M") {
            ContentView()
                .frame(minWidth: 1120, minHeight: 700)
                // The console is drawn in its own light colours; menus and sheets follow.
                .preferredColorScheme(.light)
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
    case overview, memory, sessions, runs, query, settings

    var id: Pane { self }

    var title: String {
        switch self {
        case .overview: return "總覽"
        case .memory: return "記憶"
        case .sessions: return "對話"
        case .runs: return "整理紀錄"
        case .query: return "問知識庫"
        case .settings: return "設定"
        }
    }

    var icon: String {
        switch self {
        case .overview: return "square.grid.2x2"
        case .memory: return "square.stack.3d.up"
        case .sessions: return "bubble.left"
        case .runs: return "clock"
        case .query: return "magnifyingglass"
        case .settings: return "slider.horizontal.3"
        }
    }

    /// The panes of the main list; Settings sits apart at the bottom.
    static let main: [Pane] = [.overview, .memory, .sessions, .runs, .query]
}

/// A memory to show, from another pane.
struct MemoryTarget: Equatable {
    let project: String
    let id: String
}

/// Which pane is shown, and what one pane asked another to show.
@MainActor
final class AppState: ObservableObject {
    @Published var pane: Pane? = .overview
    @Published var memoryTarget: MemoryTarget?
    @Published var sessionTarget: String?
    /// The Sessions pane should open on the sessions that wait.
    @Published var sessionsPendingOnly = false
    /// Whether session2memory was found and has something to show (`detect()`).
    @Published var setup: SetupState = .checking
    @Published var s2mChoice: CommandChoice?
    @Published var hksChoice: CommandChoice?
    @Published var storePath: String?
    /// When the last unattended run finished, as the store has it.
    @Published var lastRunAt: String?

    func show(memory id: String, in project: String) {
        memoryTarget = MemoryTarget(project: project, id: id)
        pane = .memory
    }

    func show(session key: String) {
        sessionTarget = key
        pane = .sessions
    }

    func showPendingSessions() {
        sessionsPendingOnly = true
        pane = .sessions
    }
}

struct ContentView: View {
    @StateObject private var app = AppState()

    var body: some View {
        HStack(spacing: 0) {
            Sidebar()
            Rectangle().fill(Theme.hairline).frame(width: 1)
            detail
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .background(Theme.surface)
        }
        .foregroundColor(Theme.ink)
        .tint(Theme.accent)
        .environmentObject(app)
        .task { await app.detect() }
    }

    @ViewBuilder
    private var detail: some View {
        switch app.pane ?? .overview {
        case .overview, .memory, .sessions, .runs:
            // These read the store; until it can be read, say what to do instead.
            if app.setup == .ready {
                switch app.pane ?? .overview {
                case .memory: MemoryPane()
                case .sessions: SessionsPane()
                case .runs: RunsPane()
                default: OverviewPane()
                }
            } else {
                SetupView()
            }
        case .query: QueryPane()
        case .settings: SettingsPane()
        }
    }
}

/// The list of panes on the left, with when the store was last put in order.
private struct Sidebar: View {
    @EnvironmentObject private var app: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 22) {
            HStack(spacing: 10) {
                RoundedRectangle(cornerRadius: 9)
                    .fill(Theme.accent)
                    .frame(width: 30, height: 30)
                    .overlay(
                        Image(systemName: "square.stack.3d.up.fill")
                            .font(.system(size: 14, weight: .semibold))
                            .foregroundColor(.white)
                    )
                VStack(alignment: .leading, spacing: 0) {
                    Text("S2M").font(.system(size: 15, weight: .bold))
                    Text("代理人的記憶").font(.system(size: 12)).foregroundColor(Theme.secondary)
                }
            }
            .padding(.horizontal, 8)
            VStack(spacing: 2) {
                ForEach(Pane.main) { item in
                    row(item)
                }
            }
            Spacer(minLength: 0)
            VStack(alignment: .leading, spacing: 10) {
                if let last = app.lastRunAt {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("上次自動整理").font(.system(size: 12)).foregroundColor(Theme.secondary)
                        Text(localTime(last)).font(.system(size: 13, weight: .medium))
                    }
                    .padding(12)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(Theme.surface)
                    .overlay(RoundedRectangle(cornerRadius: 12).stroke(Theme.hairline))
                    .cornerRadius(12)
                }
                row(.settings)
            }
        }
        .padding(.horizontal, 14)
        .padding(.top, 20)
        .padding(.bottom, 16)
        .frame(width: 212)
        .frame(maxHeight: .infinity)
        .background(Theme.sidebar)
    }

    private func row(_ item: Pane) -> some View {
        let chosen = (app.pane ?? .overview) == item
        return Button {
            app.pane = item
        } label: {
            HStack(spacing: 10) {
                Image(systemName: item.icon)
                    .font(.system(size: 15))
                    .frame(width: 20)
                Text(item.title).font(.system(size: 14, weight: chosen ? .semibold : .regular))
                Spacer(minLength: 0)
            }
            .foregroundColor(chosen ? Theme.accent : Theme.ink)
            .padding(.horizontal, 10)
            .frame(height: 40)
            .background(chosen ? Theme.accentTint : Color.clear)
            .cornerRadius(10)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(chosen ? .isSelected : [])
    }
}
