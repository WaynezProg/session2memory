import AppKit
import SwiftUI

/// Shows what was found without asking. Nothing here has to be filled in: the fields
/// under "Set by hand" are for a command or folder the app cannot find itself.
struct SettingsPane: View {
    @EnvironmentObject private var app: AppState
    @AppStorage(SettingKey.s2mDirectory) private var s2mDirectory = ""
    @AppStorage(SettingKey.s2mCommand) private var s2mCommand = ""
    @AppStorage(SettingKey.storeDirectory) private var storeDirectory = ""
    @AppStorage(SettingKey.hksDirectory) private var hksDirectory = ""
    @AppStorage(SettingKey.hksCommand) private var hksCommand = ""
    @AppStorage(SettingKey.ksRoot) private var ksRoot = ""
    @State private var byHand = false

    var body: some View {
        VStack(spacing: 0) {
            PageHeader("設定", subtitle: "通常不用動：需要的東西會自動找到。")
            form
        }
    }

    private var form: some View {
        Form {
            Section("session2memory") {
                status(
                    found: app.s2mChoice, missing: "找不到",
                    install: CommandFinder.s2mInstall)
                switch app.setup {
                case .ready:
                    line("checkmark.circle.fill", Theme.accent, "記憶存放在：\(app.storePath ?? "預設位置")")
                case .checking:
                    line("hourglass", Theme.secondary, "檢查中…")
                case .needsStore:
                    line("exclamationmark.triangle.fill", Theme.amberText, "還沒有資料：它還沒跑過。")
                    CopyableCommand(
                        command: (app.s2mChoice?.command ?? "session2memory") + " auto run --dry-run")
                case .failed(let message):
                    line("exclamationmark.triangle.fill", Theme.amberText, message)
                case .needsUv:
                    line("exclamationmark.triangle.fill", Theme.amberText, "uv 也還沒安裝。")
                    CopyableCommand(command: CommandFinder.uvInstall)
                case .needsCommand:
                    EmptyView()
                }
            }
            Section("HKS 知識庫（選用，「問知識庫」才需要）") {
                status(found: app.hksChoice, missing: "找不到", install: CommandFinder.hksInstall)
                folderField(
                    "知識庫資料夾（KS_ROOT）", text: $ksRoot,
                    prompt: "留空：照終端機或 HKS 自己的設定")
            }
            Section {
                Button(app.setup == .checking ? "檢查中…" : "重新檢查") {
                    Task { await app.detect() }
                }
                .disabled(app.setup == .checking)
            }
            Section {
                DisclosureGroup("手動指定（給工程用）", isExpanded: $byHand) {
                    Text(
                        "留空就會自動尋找：先找這個 app 是從哪個資料夾 build 的，再找 PATH 上安裝的指令。"
                    )
                    .font(.callout)
                    .foregroundColor(Theme.secondary)
                    folderField("session2memory 資料夾", text: $s2mDirectory, prompt: "自動")
                    TextField("session2memory 指令", text: $s2mCommand, prompt: Text("自動"))
                    folderField(
                        "記憶存放的資料夾", text: $storeDirectory,
                        prompt: "~/.local/share/session2memory/store")
                    folderField("HKS 資料夾", text: $hksDirectory, prompt: "自動")
                    TextField("HKS 指令", text: $hksCommand, prompt: Text("自動"))
                    Button("全部清空") {
                        s2mDirectory = ""
                        s2mCommand = ""
                        storeDirectory = ""
                        hksDirectory = ""
                        hksCommand = ""
                        Task { await app.detect() }
                    }
                    .disabled(!anyByHand)
                }
            }
            Section {
                Text(
                    "指令在 login shell（zsh -l）裡執行，所以看到的 PATH 和終端機相同。讀取不會改動任何資料；「記憶」與「對話」會做的改動列在 README。"
                )
                .font(.callout)
                .foregroundColor(Theme.secondary)
            }
        }
        .formStyle(.grouped)
        .scrollContentBackground(.hidden)
        .onAppear { byHand = anyByHand }
        .onSubmit { Task { await app.detect() } }
    }

    private var anyByHand: Bool {
        ![s2mDirectory, s2mCommand, storeDirectory, hksDirectory, hksCommand].allSatisfy {
            $0.trimmingCharacters(in: .whitespaces).isEmpty
        }
    }

    @ViewBuilder
    private func status(found: CommandChoice?, missing: String, install: String) -> some View {
        if let found {
            line("checkmark.circle.fill", Theme.accent, "使用 \(found.summary)")
        } else if app.setup == .checking {
            line("hourglass", Theme.secondary, "尋找中…")
        } else {
            line("xmark.circle.fill", Theme.dangerText, "\(missing)。在終端機安裝：")
            CopyableCommand(command: install)
        }
    }

    private func line(_ icon: String, _ color: Color, _ text: String) -> some View {
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: icon).foregroundColor(color)
            Text(text).textSelection(.enabled)
        }
    }

    private func folderField(_ title: String, text: Binding<String>, prompt: String) -> some View {
        HStack {
            TextField(title, text: text, prompt: Text(prompt))
            Button("選擇…") {
                if let folder = chooseFolder() {
                    text.wrappedValue = folder
                    Task { await app.detect() }
                }
            }
        }
    }

    private func chooseFolder() -> String? {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        panel.showsHiddenFiles = true
        return panel.runModal() == .OK ? panel.url?.path : nil
    }
}
