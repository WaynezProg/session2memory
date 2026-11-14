import AppKit
import SwiftUI

struct MemoryPane: View {
    @EnvironmentObject private var app: AppState
    @State private var projects: [Project] = []
    @State private var projectFilter = ""
    @State private var selectedProject: String?
    @State private var status = "active"
    @State private var query = ""
    @State private var everywhere = false
    @State private var memories: [Memory] = []
    @State private var selection: Set<String> = []
    @State private var detail: MemoryDetail?
    @State private var message: String?
    @State private var note: String?
    @State private var loading = false
    @State private var working = false
    @State private var sheet: Sheet?
    @State private var reason = ""
    @State private var draft = ""
    @State private var draftKind = "fact"

    private enum Sheet: Identifiable {
        case status(ids: [String], restore: Bool)
        case edit(MemoryDetail)
        case add(project: String)

        var id: String {
            switch self {
            case .status(let ids, let restore): return "status|\(restore)|\(ids.joined(separator: ","))"
            case .edit(let detail): return "edit|\(detail.id)"
            case .add(let project): return "add|\(project)"
            }
        }
    }

    private let statuses = ["active", "superseded", "retired", "stale", "all"]
    private let kinds = ["decision", "constraint", "pitfall", "fact", "preference", "procedure"]

    var body: some View {
        VStack(spacing: 0) {
            if let message {
                ErrorBanner(message: message).padding(8)
            } else if let note {
                InfoBanner(message: note).padding(8)
            }
            HSplitView {
                projectColumn.frame(minWidth: 200, idealWidth: 230, maxWidth: 320)
                memoryColumn.frame(minWidth: 300, idealWidth: 380)
                detailView.frame(minWidth: 340, maxWidth: .infinity)
            }
        }
        .navigationTitle("Memory")
        .searchable(text: $query, prompt: "Search memory")
        .toolbar {
            ToolbarItem {
                Button {
                    if let project = selectedProject {
                        reason = ""
                        draft = ""
                        draftKind = "fact"
                        sheet = .add(project: project)
                    }
                } label: {
                    Label("New Memory", systemImage: "plus")
                }
                .disabled(selectedProject == nil || working)
                .help("Write a memory for the chosen workspace by hand")
            }
            ToolbarItem {
                Button {
                    Task {
                        await loadProjects()
                        await loadMemories(wait: false)
                    }
                } label: {
                    Label("Reload", systemImage: "arrow.clockwise")
                }
                .disabled(loading)
            }
        }
        .task { await loadProjects() }
        .task(id: listKey) { await loadMemories(wait: !query.isEmpty) }
        .task(id: selection) { await loadDetail() }
        .onAppear { takeTarget() }
        .onChange(of: app.memoryTarget) { _ in takeTarget() }
        .sheet(item: $sheet) { item in
            switch item {
            case .status(let ids, let restore): statusSheet(ids: ids, restore: restore)
            case .edit(let detail): editSheet(detail)
            case .add(let project): addSheet(project: project)
            }
        }
    }

    private var listKey: String {
        [selectedProject ?? "", status, query, everywhere ? "1" : "0"].joined(separator: "|")
    }

    private var searching: Bool {
        !query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    // MARK: columns

    private var projectColumn: some View {
        VStack(spacing: 0) {
            TextField("Filter workspaces", text: $projectFilter)
                .textFieldStyle(.roundedBorder)
                .padding(8)
            List(filterProjects(projects, by: projectFilter), selection: $selectedProject) {
                project in
                VStack(alignment: .leading, spacing: 2) {
                    Text(project.name).fontWeight(.medium)
                    Text(projectLine(project))
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
                .help(project.project)
                .tag(project.project)
            }
        }
    }

    private func projectLine(_ project: Project) -> String {
        var parts = ["\(project.active) active", "\(project.sessions) sessions"]
        if project.sessionsPending > 0 {
            parts.append("\(project.sessionsPending) pending")
        }
        return parts.joined(separator: " · ")
    }

    private var memoryColumn: some View {
        VStack(spacing: 0) {
            Picker("Status", selection: $status) {
                ForEach(statuses, id: \.self) { item in
                    Text(item).tag(item)
                }
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .padding([.horizontal, .top], 8)
            HStack {
                if searching {
                    Toggle("All workspaces", isOn: $everywhere)
                        .toggleStyle(.checkbox)
                }
                Spacer()
                Text(countLine).font(.caption).foregroundColor(.secondary)
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 6)
            if selectedProject == nil && !(searching && everywhere) {
                Placeholder(text: "Choose a workspace")
            } else if memories.isEmpty {
                Placeholder(text: loading ? "Loading…" : emptyText)
            } else {
                List(memories, selection: $selection) { memory in
                    row(memory).tag(memory.id)
                }
                .contextMenu(forSelectionType: String.self) { ids in
                    rowMenu(Array(ids))
                }
                .onDeleteCommand {
                    let ids = retirable(Array(selection))
                    if !ids.isEmpty { ask(ids: ids, restore: false) }
                }
            }
        }
    }

    private var countLine: String {
        var line = "\(memories.count) shown"
        if selection.count > 1 { line += " · \(selection.count) selected" }
        return line
    }

    private var emptyText: String {
        searching ? "Nothing matches" : "No memory with this status"
    }

    private func row(_ memory: Memory) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 6) {
                Text(memory.kind).font(.caption).foregroundColor(.secondary)
                if status == "all" {
                    StatusBadge(status: memory.status)
                }
                if searching && everywhere {
                    Text((memory.project as NSString).lastPathComponent)
                        .font(.caption)
                        .foregroundColor(.accentColor)
                }
                Spacer()
                Text(shortTime(memory.lastConfirmedAt))
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
            Text(memory.text).lineLimit(3)
        }
        .padding(.vertical, 2)
    }

    @ViewBuilder
    private func rowMenu(_ ids: [String]) -> some View {
        let toRetire = retirable(ids)
        let toRestore = restorable(ids)
        if !toRetire.isEmpty {
            Button(toRetire.count == 1 ? "Retire…" : "Retire \(toRetire.count)…") {
                ask(ids: toRetire, restore: false)
            }
        }
        if !toRestore.isEmpty {
            Button(toRestore.count == 1 ? "Restore…" : "Restore \(toRestore.count)…") {
                ask(ids: toRestore, restore: true)
            }
        }
        if ids.count == 1, let detail, detail.id == ids[0], detail.status == "active" {
            Button("Edit…") { startEdit(detail) }
        }
        if !ids.isEmpty {
            Divider()
            Button("Copy Text") { copy(ids) }
        }
    }

    // MARK: detail

    @ViewBuilder
    private var detailView: some View {
        if selection.count > 1 {
            batchView
        } else if let detail {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    header(detail)
                    links(detail)
                    evidence(detail)
                    history(detail)
                }
                .padding(16)
            }
        } else {
            Placeholder(text: "Choose a memory")
        }
    }

    private var batchView: some View {
        let ids = Array(selection)
        let toRetire = retirable(ids)
        let toRestore = restorable(ids)
        return VStack(spacing: 14) {
            Text("\(ids.count) memories selected").font(.title3)
            Text("Changes are recorded in each entry's history; nothing is deleted.")
                .foregroundColor(.secondary)
            HStack(spacing: 10) {
                Button("Retire \(toRetire.count)…") { ask(ids: toRetire, restore: false) }
                    .disabled(toRetire.isEmpty || working)
                Button("Restore \(toRestore.count)…") { ask(ids: toRestore, restore: true) }
                    .disabled(toRestore.isEmpty || working)
                Button("Copy Text") { copy(ids) }
            }
            if working { ProgressView().controlSize(.small) }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(20)
    }

    private func header(_ detail: MemoryDetail) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                StatusBadge(status: detail.status)
                Text(detail.kind).foregroundColor(.secondary)
                Text(detail.scope).foregroundColor(.secondary)
                Spacer()
                if detail.status == "active" {
                    Button("Edit…") { startEdit(detail) }
                    Button("Retire…") { ask(ids: [detail.id], restore: false) }
                } else if detail.restorable {
                    Button("Restore…") { ask(ids: [detail.id], restore: true) }
                }
            }
            .disabled(working)
            Text(detail.text).font(.title3).textSelection(.enabled)
            Text(
                "\(detail.id) · created \(shortTime(detail.createdAt)) · last confirmed \(shortTime(detail.lastConfirmedAt))"
            )
            .font(.caption)
            .foregroundColor(.secondary)
            .textSelection(.enabled)
            Text(detail.project).font(.caption).foregroundColor(.secondary)
                .textSelection(.enabled)
        }
    }

    @ViewBuilder
    private func links(_ detail: MemoryDetail) -> some View {
        if detail.supersedesId != nil || !detail.replacedBy.isEmpty {
            Block("Related") {
                if let older = detail.supersedesId {
                    Button("Replaced \(older)") { open(older) }
                        .buttonStyle(.link)
                }
                ForEach(detail.replacedBy, id: \.self) { newer in
                    Button("Replaced by \(newer)") { open(newer) }
                        .buttonStyle(.link)
                }
            }
        }
    }

    private func evidence(_ detail: MemoryDetail) -> some View {
        Block("Evidence sessions") {
            if detail.evidenceSessions.isEmpty {
                Text("None recorded").foregroundColor(.secondary)
            }
            ForEach(detail.evidenceSessions) { session in
                VStack(alignment: .leading, spacing: 2) {
                    Button(session.title ?? session.key) { app.show(session: session.key) }
                        .buttonStyle(.link)
                        .help("Show this session")
                    Text("\(session.key) · \(shortTime(session.lastActivity))")
                        .font(.caption)
                        .foregroundColor(.secondary)
                        .textSelection(.enabled)
                    if let summary = session.summary, !summary.isEmpty {
                        Text(summary).font(.callout).lineLimit(4)
                    }
                }
                .padding(.vertical, 2)
            }
        }
    }

    private func history(_ detail: MemoryDetail) -> some View {
        Block("History") {
            ForEach(Array(detail.events.enumerated()), id: \.offset) { _, event in
                VStack(alignment: .leading, spacing: 2) {
                    HStack {
                        Text(event.op).fontWeight(.medium)
                        Text(shortTime(event.at)).font(.caption).foregroundColor(.secondary)
                    }
                    if let why = event.detail["reason"]?.stringValue, !why.isEmpty {
                        Text(why).font(.callout)
                    }
                }
                .padding(.vertical, 2)
            }
        }
    }

    // MARK: sheets

    private var trimmedReason: String {
        reason.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private var trimmedDraft: String {
        draft.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private func statusSheet(ids: [String], restore: Bool) -> some View {
        let many = ids.count > 1
        return VStack(alignment: .leading, spacing: 12) {
            Text(
                restore
                    ? (many ? "Restore \(ids.count) memories" : "Restore this memory")
                    : (many ? "Retire \(ids.count) memories" : "Retire this memory")
            )
            .font(.headline)
            Text(
                restore
                    ? "Active again, and used in new sessions."
                    : "Kept, but no longer searched, exported or given to new sessions."
            )
            .foregroundColor(.secondary)
            QuickReasons(
                choices: restore
                    ? ["Still true", "Retired by mistake"]
                    : ["Outdated", "Wrong", "Duplicate", "Too specific"],
                reason: $reason)
            TextField("Reason (recorded in the history)", text: $reason)
                .frame(width: 420)
            HStack {
                Spacer()
                Button("Cancel") { sheet = nil }
                    .keyboardShortcut(.cancelAction)
                Button(restore ? "Restore" : "Retire") {
                    let why = trimmedReason
                    sheet = nil
                    Task { await change(ids: ids, restore: restore, reason: why) }
                }
                .keyboardShortcut(.defaultAction)
                .disabled(trimmedReason.isEmpty)
            }
        }
        .padding(20)
    }

    private func editSheet(_ detail: MemoryDetail) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Edit this memory").font(.headline)
            Text("The old wording is kept as superseded and linked from the new one.")
                .foregroundColor(.secondary)
            TextEditor(text: $draft)
                .font(.body)
                .frame(width: 480, height: 130)
                .border(Color.secondary.opacity(0.3))
            QuickReasons(
                choices: ["Clearer wording", "Corrected a detail", "Out of date"], reason: $reason)
            TextField("Reason (recorded in the history)", text: $reason)
                .frame(width: 480)
            HStack {
                Spacer()
                Button("Cancel") { sheet = nil }
                    .keyboardShortcut(.cancelAction)
                Button("Save") {
                    let text = trimmedDraft
                    let why = trimmedReason
                    sheet = nil
                    Task { await saveEdit(id: detail.id, text: text, reason: why) }
                }
                .keyboardShortcut(.defaultAction)
                .disabled(
                    trimmedReason.isEmpty || trimmedDraft.isEmpty || trimmedDraft == detail.text)
            }
        }
        .padding(20)
    }

    private func addSheet(project: String) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("New memory").font(.headline)
            Text("For \((project as NSString).lastPathComponent). It has no session behind it.")
                .foregroundColor(.secondary)
            Picker("Kind", selection: $draftKind) {
                ForEach(kinds, id: \.self) { kind in
                    Text(kind).tag(kind)
                }
            }
            .frame(width: 240)
            TextEditor(text: $draft)
                .font(.body)
                .frame(width: 480, height: 110)
                .border(Color.secondary.opacity(0.3))
            QuickReasons(choices: ["Team rule", "Decided outside a session"], reason: $reason)
            TextField("Reason (recorded in the history)", text: $reason)
                .frame(width: 480)
            HStack {
                Spacer()
                Button("Cancel") { sheet = nil }
                    .keyboardShortcut(.cancelAction)
                Button("Add") {
                    let text = trimmedDraft
                    let why = trimmedReason
                    let kind = draftKind
                    sheet = nil
                    Task { await saveNew(project: project, kind: kind, text: text, reason: why) }
                }
                .keyboardShortcut(.defaultAction)
                .disabled(trimmedReason.isEmpty || trimmedDraft.isEmpty)
            }
        }
        .padding(20)
    }

    // MARK: helpers

    private func known(_ id: String) -> String? {
        if let detail, detail.id == id { return detail.status }
        return memories.first(where: { $0.id == id })?.status
    }

    private func retirable(_ ids: [String]) -> [String] {
        ids.filter { known($0) == "active" }
    }

    private func restorable(_ ids: [String]) -> [String] {
        ids.filter { ["retired", "stale"].contains(known($0) ?? "") }
    }

    private func ask(ids: [String], restore: Bool) {
        guard !ids.isEmpty else { return }
        reason = ""
        sheet = .status(ids: ids, restore: restore)
    }

    private func startEdit(_ detail: MemoryDetail) {
        reason = ""
        draft = detail.text
        sheet = .edit(detail)
    }

    private func copy(_ ids: [String]) {
        let wanted = Set(ids)
        var texts = memories.filter { wanted.contains($0.id) }.map(\.text)
        if texts.isEmpty, let detail, wanted.contains(detail.id) { texts = [detail.text] }
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(texts.joined(separator: "\n"), forType: .string)
    }

    /// Show an entry that may not be in the list (another status).
    private func open(_ id: String) {
        status = "all"
        selection = [id]
    }

    private func takeTarget() {
        guard let target = app.memoryTarget else { return }
        app.memoryTarget = nil
        query = ""
        everywhere = false
        projectFilter = ""
        selectedProject = target.project
        status = "all"
        selection = [target.id]
    }

    // MARK: loading and changing

    @MainActor
    private func loadProjects() async {
        loading = true
        defer { loading = false }
        do {
            projects = try await Backend.current().projects()
            message = nil
            if selectedProject == nil {
                selectedProject = projects.first?.project
            }
        } catch {
            message = error.localizedDescription
        }
    }

    @MainActor
    private func loadMemories(wait: Bool) async {
        if wait {
            // Typing: wait for a pause before asking.
            try? await Task.sleep(nanoseconds: 300_000_000)
            if Task.isCancelled { return }
        }
        let words = query.trimmingCharacters(in: .whitespacesAndNewlines)
        if words.isEmpty || !everywhere, selectedProject == nil {
            memories = []
            return
        }
        loading = true
        defer { loading = false }
        do {
            let backend = Backend.current()
            let found: [Memory]
            if words.isEmpty {
                found = try await backend.memories(project: selectedProject ?? "", status: status)
            } else {
                found = try await backend.search(
                    query: words, project: everywhere ? nil : selectedProject, status: status)
            }
            if Task.isCancelled { return }
            memories = found
            message = nil
        } catch {
            if Task.isCancelled { return }
            memories = []
            message = error.localizedDescription
        }
    }

    @MainActor
    private func loadDetail() async {
        guard selection.count == 1, let id = selection.first else {
            detail = nil
            return
        }
        do {
            let loaded = try await Backend.current().memory(id: id)
            if Task.isCancelled { return }
            detail = loaded
            message = nil
        } catch {
            if Task.isCancelled { return }
            detail = nil
            message = error.localizedDescription
        }
    }

    @MainActor
    private func change(ids: [String], restore: Bool, reason: String) async {
        working = true
        defer { working = false }
        let backend = Backend.current()
        var outcome = BatchOutcome()
        var last: MemoryDetail?
        for id in ids {
            do {
                last =
                    restore
                    ? try await backend.restore(id: id, reason: reason)
                    : try await backend.retire(id: id, reason: reason)
                outcome.done += 1
            } catch {
                outcome.failures.append(error.localizedDescription)
            }
        }
        report(outcome, verb: restore ? "Restored" : "Retired")
        if ids.count == 1, let last { detail = last }
        if ids.count > 1 { selection = [] }
        await loadMemories(wait: false)
        await loadProjects()
    }

    @MainActor
    private func saveEdit(id: String, text: String, reason: String) async {
        working = true
        defer { working = false }
        do {
            let updated = try await Backend.current().edit(id: id, text: text, reason: reason)
            message = nil
            note = "Saved. The old wording is kept as superseded."
            await loadMemories(wait: false)
            selection = [updated.id]
            detail = updated
        } catch {
            note = nil
            message = error.localizedDescription
        }
    }

    @MainActor
    private func saveNew(project: String, kind: String, text: String, reason: String) async {
        working = true
        defer { working = false }
        do {
            let added = try await Backend.current().add(
                project: project, kind: kind, text: text, reason: reason)
            message = nil
            note = "Added."
            if status != "active" && status != "all" { status = "active" }
            await loadMemories(wait: false)
            await loadProjects()
            selection = [added.id]
            detail = added
        } catch {
            note = nil
            message = error.localizedDescription
        }
    }

    private func report(_ outcome: BatchOutcome, verb: String) {
        if outcome.failures.isEmpty {
            message = nil
            note = outcome.summary(verb: verb)
        } else {
            note = nil
            message = outcome.summary(verb: verb)
        }
    }
}
