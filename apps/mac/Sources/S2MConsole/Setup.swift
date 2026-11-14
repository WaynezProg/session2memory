import AppKit
import SwiftUI

/// How one of the two command lines is started.
struct CommandChoice: Equatable {
    enum Source: Equatable {
        /// A folder or command typed in Settings.
        case settings
        /// The checkout this app was built in or started from.
        case checkout
        /// The command installed on the login shell's PATH (`uv tool install`).
        case installed
    }

    let command: String
    let directory: String
    let source: Source

    var summary: String {
        switch source {
        case .settings: return "`\(command)` (set by hand)"
        case .checkout: return "`\(command)` in \(directory)"
        case .installed: return "`\(command)` (installed command)"
        }
    }
}

/// Picks how to start a command without asking: what Settings says wins, then the
/// checkout the app belongs to, then the command installed on PATH.
enum CommandFinder {
    static let s2mInstall = "uv tool install git+https://github.com/WaynezProg/session2memory"
    static let hksInstall = "uv tool install git+https://github.com/WaynezProg/Hybrid-Knowledge-System"
    static let uvInstall = "curl -LsSf https://astral.sh/uv/install.sh | sh"

    static func choose(
        savedCommand: String, savedDirectory: String, name: String, checkout: String?,
        installed: Bool, home: String = NSHomeDirectory()
    ) -> CommandChoice? {
        if !savedCommand.isEmpty || !savedDirectory.isEmpty {
            // A folder alone means "that checkout"; a command alone runs from home.
            return CommandChoice(
                command: savedCommand.isEmpty ? "uv run \(name)" : savedCommand,
                directory: savedDirectory.isEmpty ? home : savedDirectory, source: .settings)
        }
        if let checkout {
            return CommandChoice(command: "uv run \(name)", directory: checkout, source: .checkout)
        }
        if installed {
            return CommandChoice(command: name, directory: home, source: .installed)
        }
        return nil
    }

    /// Whether the login shell finds `name`, as Terminal would.
    static func isInstalled(_ name: String) async -> Bool {
        guard name.allSatisfy({ $0.isLetter || $0.isNumber || $0 == "-" || $0 == "_" }) else {
            return false
        }
        let found = try? await Shell.run("command -v \(name)", directory: "")
        return found?.status == 0
    }
}

/// What was found at the last check. Read by `Backend.current()` from any thread.
final class Detected: @unchecked Sendable {
    static let shared = Detected()
    private let lock = NSLock()
    private var s2mChoice: CommandChoice?
    private var hksChoice: CommandChoice?
    private var done = false

    /// False until the first check has finished; nothing is called "not found" before.
    var checked: Bool {
        get { lock.withLock { done } }
        set { lock.withLock { done = newValue } }
    }

    var s2m: CommandChoice? {
        get { lock.withLock { s2mChoice } }
        set { lock.withLock { s2mChoice = newValue } }
    }

    var hks: CommandChoice? {
        get { lock.withLock { hksChoice } }
        set { lock.withLock { hksChoice = newValue } }
    }
}

/// Whether the app can show anything yet, and if not, the one thing to do about it.
enum SetupState: Equatable {
    case checking
    case ready
    /// `uv` itself is missing: nothing can be installed or run.
    case needsUv
    /// session2memory is neither installed nor in a checkout the app knows.
    case needsCommand
    /// The command works but has never run: there is nothing to show.
    case needsStore(String)
    /// The command was found and failed for another reason.
    case failed(String)
}

extension AppState {
    /// Find the two commands and see whether session2memory has a store to show.
    func detect() async {
        setup = .checking
        let defaults = UserDefaults.standard
        func saved(_ key: String) -> String {
            (defaults.string(forKey: key) ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        }
        let checkout = RepositoryFinder.session2memory()
        async let s2mInstalled = CommandFinder.isInstalled("session2memory")
        async let hksInstalled = CommandFinder.isInstalled("ks")
        async let uvInstalled = CommandFinder.isInstalled("uv")

        let s2m = CommandFinder.choose(
            savedCommand: saved(SettingKey.s2mCommand),
            savedDirectory: saved(SettingKey.s2mDirectory), name: "session2memory",
            checkout: checkout, installed: await s2mInstalled)
        let hksCheckout =
            (s2m?.source == .settings ? s2m?.directory : checkout).flatMap(
                RepositoryFinder.hks(beside:))
        Detected.shared.hks = CommandFinder.choose(
            savedCommand: saved(SettingKey.hksCommand),
            savedDirectory: saved(SettingKey.hksDirectory), name: "ks", checkout: hksCheckout,
            installed: await hksInstalled)
        Detected.shared.s2m = s2m
        Detected.shared.checked = true
        s2mChoice = s2m
        hksChoice = Detected.shared.hks

        guard s2m != nil else {
            setup = await uvInstalled ? .needsCommand : .needsUv
            return
        }
        do {
            let overview = try await Backend.current().overview()
            storePath = overview.store
            lastRunAt = overview.lastRun?.finishedAt
            setup = .ready
        } catch {
            let text = error.localizedDescription
            setup = text.contains("no store at") ? .needsStore(text) : .failed(text)
        }
    }
}

/// Shown instead of the panes until session2memory can be read: says the one thing to
/// do next, with the command to copy.
struct SetupView: View {
    @EnvironmentObject private var app: AppState

    /// The command as it has to be typed in Terminal (from the checkout, with uv).
    private var run: String { app.s2mChoice?.command ?? "session2memory" }

    var body: some View {
        VStack(spacing: 18) {
            switch app.setup {
            case .checking:
                ProgressView("正在尋找 session2memory…")
            case .ready:
                EmptyView()
            case .needsUv:
                step(
                    title: "先安裝 uv",
                    text: "session2memory 用 uv 安裝與執行。把這兩行貼到終端機：",
                    commands: [CommandFinder.uvInstall, CommandFinder.s2mInstall])
            case .needsCommand:
                step(
                    title: "安裝 session2memory",
                    text: "這台 Mac 上找不到它。把這行貼到終端機：",
                    commands: [CommandFinder.s2mInstall])
            case .needsStore:
                step(
                    title: "還沒有東西可以看",
                    text:
                        app.s2mChoice?.source == .checkout
                        ? "session2memory 還沒跑過。在它的資料夾裡先看它會讀到什麼，再實際跑一次："
                        : "session2memory 已安裝但還沒跑過。先看它會讀到什麼，再實際跑一次：",
                    commands: [run + " auto run --dry-run", run + " auto run"],
                    note:
                        "實際執行會壓縮並歸檔舊對話，也會呼叫模型寫記憶；README 有寫它會改動哪些東西。"
                )
            case .failed(let message):
                step(
                    title: "讀不到 session2memory",
                    text: message, commands: [],
                    note: "如果它裝在不尋常的位置，到「設定」指定它。")
            }
            if app.setup != .checking && app.setup != .ready {
                HStack(spacing: 10) {
                    Button("重新檢查") { Task { await app.detect() } }
                        .buttonStyle(PrimaryButtonStyle())
                        .keyboardShortcut(.defaultAction)
                    Button("設定…") { app.pane = .settings }
                        .buttonStyle(OutlineButtonStyle())
                }
            }
        }
        .frame(maxWidth: 560)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(30)
    }

    private func step(title: String, text: String, commands: [String], note: String? = nil)
        -> some View
    {
        VStack(alignment: .leading, spacing: 10) {
            Text(title).font(.system(size: 20, weight: .bold)).foregroundColor(Theme.ink)
            Text(text).foregroundColor(Theme.secondary).textSelection(.enabled)
            ForEach(commands, id: \.self) { command in
                CopyableCommand(command: command)
            }
            if let note {
                Text(note).font(.callout).foregroundColor(Theme.secondary)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// A command line with a button that copies it.
struct CopyableCommand: View {
    let command: String
    @State private var copied = false

    var body: some View {
        HStack(spacing: 8) {
            Text(command)
                .font(.system(.callout, design: .monospaced))
                .textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)
            Button(copied ? "已複製" : "複製") {
                NSPasteboard.general.clearContents()
                NSPasteboard.general.setString(command, forType: .string)
                copied = true
            }
        }
        .padding(10)
        .background(Theme.sidebar)
        .cornerRadius(10)
    }
}
