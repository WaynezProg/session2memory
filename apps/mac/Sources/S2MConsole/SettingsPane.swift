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
        Form {
            Section("session2memory") {
                status(
                    found: app.s2mChoice, missing: "Not found",
                    install: CommandFinder.s2mInstall)
                switch app.setup {
                case .ready:
                    line("checkmark.circle.fill", .green, "Store: \(app.storePath ?? "default")")
                case .checking:
                    line("hourglass", .secondary, "Checking…")
                case .needsStore:
                    line("exclamationmark.triangle.fill", .orange, "No store yet: it has not run.")
                    CopyableCommand(
                        command: (app.s2mChoice?.command ?? "session2memory") + " auto run --dry-run")
                case .failed(let message):
                    line("exclamationmark.triangle.fill", .orange, message)
                case .needsUv:
                    line("exclamationmark.triangle.fill", .orange, "uv is not installed either.")
                    CopyableCommand(command: CommandFinder.uvInstall)
                case .needsCommand:
                    EmptyView()
                }
            }
            Section("HKS (optional, for the HKS Query pane)") {
                status(found: app.hksChoice, missing: "Not found", install: CommandFinder.hksInstall)
                folderField(
                    "Knowledge base folder (KS_ROOT)", text: $ksRoot,
                    prompt: "empty: as the shell or HKS's own config has it")
            }
            Section {
                Button(app.setup == .checking ? "Checking…" : "Check Again") {
                    Task { await app.detect() }
                }
                .disabled(app.setup == .checking)
            }
            Section {
                DisclosureGroup("Set by hand", isExpanded: $byHand) {
                    Text(
                        "Leave these empty to let the app find the commands: first the checkout it was built in, then the commands installed on your PATH."
                    )
                    .font(.callout)
                    .foregroundColor(.secondary)
                    folderField("session2memory folder", text: $s2mDirectory, prompt: "automatic")
                    TextField("session2memory command", text: $s2mCommand, prompt: Text("automatic"))
                    folderField(
                        "Store folder", text: $storeDirectory,
                        prompt: "~/.local/share/session2memory/store")
                    folderField("HKS folder", text: $hksDirectory, prompt: "automatic")
                    TextField("HKS command", text: $hksCommand, prompt: Text("automatic"))
                    Button("Clear All") {
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
                    "Commands run in a login shell (zsh -l), so they see the same PATH as Terminal. Reading never changes the store; the changes the Memory and Sessions panes make are listed in the README."
                )
                .font(.callout)
                .foregroundColor(.secondary)
            }
        }
        .formStyle(.grouped)
        .navigationTitle("Settings")
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
            line("checkmark.circle.fill", .green, "Using \(found.summary)")
        } else if app.setup == .checking {
            line("hourglass", .secondary, "Looking…")
        } else {
            line("xmark.circle.fill", .red, "\(missing). Install it in Terminal:")
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
            Button("Choose…") {
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
