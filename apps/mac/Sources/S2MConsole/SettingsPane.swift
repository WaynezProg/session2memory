import SwiftUI

struct SettingsPane: View {
    @AppStorage(SettingKey.s2mDirectory) private var s2mDirectory = ""
    @AppStorage(SettingKey.s2mCommand) private var s2mCommand = ""
    @AppStorage(SettingKey.storeDirectory) private var storeDirectory = ""
    @AppStorage(SettingKey.hksDirectory) private var hksDirectory = ""
    @AppStorage(SettingKey.hksCommand) private var hksCommand = ""
    @AppStorage(SettingKey.ksRoot) private var ksRoot = ""

    @State private var checking = false
    @State private var s2mCheck = ""
    @State private var hksCheck = ""

    var body: some View {
        Form {
            Section("session2memory") {
                TextField("Repository folder", text: $s2mDirectory)
                TextField("Command", text: $s2mCommand)
                TextField("Store folder (empty: the default store)", text: $storeDirectory)
                if !s2mCheck.isEmpty {
                    Text(s2mCheck).font(.callout).textSelection(.enabled)
                }
            }
            Section("HKS") {
                TextField("Repository folder", text: $hksDirectory)
                TextField("Command", text: $hksCommand)
                TextField("KS_ROOT (empty: as the shell has it)", text: $ksRoot)
                if !hksCheck.isEmpty {
                    Text(hksCheck).font(.callout).textSelection(.enabled)
                }
            }
            Section {
                Button(checking ? "Checking…" : "Check both commands") {
                    Task { await check() }
                }
                .disabled(checking)
                Text(
                    "Commands run in a login shell (zsh -l) from the folder above, so they see the same PATH as Terminal. Reading never changes the store; Retire and Restore in Memory do."
                )
                .font(.callout)
                .foregroundColor(.secondary)
            }
        }
        .formStyle(.grouped)
        .navigationTitle("Settings")
    }

    @MainActor
    private func check() async {
        checking = true
        defer { checking = false }
        let backend = Backend.current()
        do {
            let overview = try await backend.overview()
            let active = overview.counts["memories_active"] ?? 0
            s2mCheck = "OK — store \(overview.store), \(active) active memories"
        } catch {
            s2mCheck = "Failed — \(error.localizedDescription)"
        }
        do {
            let result = try await Shell.run(
                backend.hksCommand + " --help", directory: backend.hksDirectory)
            hksCheck =
                result.status == 0
                ? "OK — ks answers"
                : "Failed — exit \(result.status): \(String(result.errors.suffix(300)))"
        } catch {
            hksCheck = "Failed — \(error.localizedDescription)"
        }
    }
}
