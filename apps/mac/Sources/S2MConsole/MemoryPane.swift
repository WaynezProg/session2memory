import AppKit
import SwiftUI

struct MemoryPane: View {
    @EnvironmentObject private var app: AppState
    @State private var projects: [Project] = []
    @State private var projectFilter = ""
    @State private var selectedProject: String?
    @State private var status = "active"
    @State private var kind = ""
    @State private var query = ""
    @State private var everywhere = false
    @State private var memories: [Memory] = []
    @State private var selection: Set<String> = []
    @State private var detail: MemoryDetail?
    /// Why the selected entry could not be loaded.
    @State private var detailError: String?
    /// Counts detail requests: only the answer to the latest one is shown.
    @State private var detailRequest = 0
    /// Why the last load failed, if it did.
    @State private var message: String?
    /// What the last change came to.
    @State private var outcome: ActionNote?
    @State private var loading = false
    @State private var working = false
    @State private var sheet: Sheet?
    @State private var reason = ""
    @State private var draft = ""
    @State private var draftKind = "fact"
    /// The workspace is about to change because another pane asked for an entry in it:
    /// that entry stays selected.
    @State private var keepSelection = false
    /// An entry to scroll to once the list holds it.
    @State private var reveal: String?
    /// Workspaces that only have sessions are left out unless asked for.
    @State private var showEmpty = false
    /// The chosen workspace should be scrolled into view.
    @State private var revealProject = false

    private enum Sheet: Identifiable {
        case status(ids: [String], restore: Bool)
        case edit(id: String, text: String)
        case add(project: String)

        var id: String {
            switch self {
            case .status(let ids, let restore): return "status|\(restore)|\(ids.joined(separator: ","))"
            case .edit(let id, _): return "edit|\(id)"
            case .add(let project): return "add|\(project)"
            }
        }
    }

    private let statuses = ["active", "superseded", "retired", "stale", "all"]
    private let kinds = ["decision", "constraint", "pitfall", "fact", "preference", "procedure"]

    var body: some View {
        VStack(spacing: 0) {
            if let message {
                ErrorBanner(message: message).padding([.horizontal, .top], 8)
            }
            if let outcome {
                NoteBanner(note: outcome) { self.outcome = nil }.padding([.horizontal, .top], 8)
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
                    if let project = selectedProject { startAdd(project: project) }
                } label: {
                    Label("New Memory", systemImage: "plus")
                }
                .keyboardShortcut("n", modifiers: [.command, .shift])
                .disabled(selectedProject == nil || working)
                .help("Write a memory for the chosen workspace by hand (⇧⌘N)")
            }
            ToolbarItem {
                Button {
                    Task { await reloadAll() }
                } label: {
                    Label("Reload", systemImage: "arrow.clockwise")
                }
                .keyboardShortcut("r", modifiers: .command)
                .disabled(loading)
                .help("Reload (⌘R)")
            }
        }
        .task { await loadProjects() }
        .task(id: listKey) { await loadMemories(wait: searching) }
        .task(id: selection) { await loadDetail() }
        .onAppear { takeTarget() }
        .onChange(of: app.memoryTarget) { _ in takeTarget() }
        .onChange(of: selectedProject) { _ in
            if keepSelection {
                keepSelection = false
            } else if !(searching && everywhere) {
                // Another workspace's list: what was selected is no longer in sight.
                selection = []
            }
        }
        .sheet(item: $sheet) { item in
            switch item {
            case .status(let ids, let restore): statusSheet(ids: ids, restore: restore)
            case .edit(let id, let text): editSheet(id: id, original: text)
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

    /// The loaded entries of the chosen kind.
    private var shown: [Memory] {
        kind.isEmpty ? memories : memories.filter { $0.kind == kind }
    }

    // MARK: columns

    private var projectColumn: some View {
        VStack(spacing: 0) {
            TextField("Filter workspaces", text: $projectFilter)
                .textFieldStyle(.roundedBorder)
                .padding([.horizontal, .top], 8)
            Toggle("Without memory too", isOn: $showEmpty)
                .toggleStyle(.checkbox)
                .font(.caption)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, 10)
                .padding(.vertical, 6)
                .help("Also list workspaces that have sessions but no memory yet")
            ScrollViewReader { proxy in
                List(listedProjects, selection: $selectedProject) { project in
                    VStack(alignment: .leading, spacing: 2) {
                        Text(project.name).fontWeight(.medium)
                        Text(projectLine(project))
                            .font(.caption)
                            .foregroundColor(.secondary)
                        // Folder names repeat (and some are ids): show where it is.
                        Text(parentFolder(of: project.project))
                            .font(.caption2)
                            .foregroundColor(.secondary)
                            .lineLimit(1)
                            .truncationMode(.head)
                    }
                    .help(project.project)
                    .tag(project.project)
                }
                .onAppear { scrollToProject(proxy) }
                .onChange(of: revealProject) { _ in scrollToProject(proxy) }
                .onChange(of: projects) { _ in scrollToProject(proxy) }
            }
        }
    }

    private var listedProjects: [Project] {
        workspacesToList(
            projects, filter: projectFilter, withoutMemory: showEmpty, keeping: selectedProject)
    }

    private func scrollToProject(_ proxy: ScrollViewProxy) {
        guard revealProject, let project = selectedProject,
            listedProjects.contains(where: { $0.project == project })
        else { return }
        revealProject = false
        proxy.scrollTo(project, anchor: .center)
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
            HStack(spacing: 10) {
                Picker("Kind", selection: $kind) {
                    Text("All kinds").tag("")
                    ForEach(kinds, id: \.self) { item in
                        Text(item).tag(item)
                    }
                }
                .labelsHidden()
                .frame(maxWidth: 140)
                if searching {
                    Toggle("All workspaces", isOn: $everywhere)
                        .toggleStyle(.checkbox)
                }
                Spacer()
                Text(countLine).font(.caption).foregroundColor(.secondary)
            }
            .padding(.horizontal, 8)
            .padding(.vertical, 6)
            if selectedProject == nil && !(searching && everywhere) {
                Placeholder(text: "Choose a workspace")
            } else if shown.isEmpty {
                Placeholder(text: loading ? "Loading…" : emptyText)
            } else {
                ScrollViewReader { proxy in
                    List(shown, selection: $selection) { memory in
                        row(memory).tag(memory.id)
                    }
                    .contextMenu(forSelectionType: String.self) { ids in
                        rowMenu(Array(ids))
                    }
                    .onDeleteCommand {
                        ask(ids: retirable(Array(selection)), restore: false)
                    }
                    .onAppear { scroll(proxy) }
                    .onChange(of: memories) { _ in scroll(proxy) }
                }
            }
        }
    }

    private func scroll(_ proxy: ScrollViewProxy) {
        guard let id = reveal, shown.contains(where: { $0.id == id }) else { return }
        reveal = nil
        proxy.scrollTo(id, anchor: .center)
    }

    private var countLine: String {
        var line = "\(shown.count) shown"
        if searching && memories.count >= Backend.searchLimit {
            line = "first \(shown.count) shown; add a word to narrow"
        }
        if selection.count > 1 { line += " · \(selection.count) selected" }
        return line
    }

    private var emptyText: String {
        if searching { return "Nothing matches" }
        return kind.isEmpty ? "No memory with this status" : "No \(kind) with this status"
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
            .disabled(working)
        }
        if !toRestore.isEmpty {
            Button(toRestore.count == 1 ? "Restore…" : "Restore \(toRestore.count)…") {
                ask(ids: toRestore, restore: true)
            }
            .disabled(working)
        }
        if ids.count == 1, toRetire.count == 1, let text = text(of: ids[0]) {
            Button("Edit…") { startEdit(id: ids[0], text: text) }
                .disabled(working)
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
        } else if let detail, selection.contains(detail.id) {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    header(detail)
                    links(detail)
                    evidence(detail)
                    history(detail)
                }
                .padding(16)
            }
        } else if let detailError, !selection.isEmpty {
            LoadFailed(message: detailError) { Task { await loadDetail() } }
        } else {
            Placeholder(text: selection.isEmpty ? "Choose a memory" : "Loading…")
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
                if working { ProgressView().controlSize(.small) }
                if detail.status == "active" {
                    Button("Edit…") { startEdit(id: detail.id, text: detail.text) }
                        .keyboardShortcut("e", modifiers: .command)
                        .help("Reword this memory (⌘E)")
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

    /// The draft as it would be stored, and whether that can be saved.
    private var tidyDraft: String { tidyText(draft) }

    private var draftFits: Bool {
        !tidyDraft.isEmpty && storedLength(draft) <= memoryTextLimit
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

    private func editSheet(id: String, original: String) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Edit this memory").font(.headline)
            Text("The old wording is kept as superseded and linked from the new one.")
                .foregroundColor(.secondary)
            draftEditor(height: 130)
            QuickReasons(
                choices: ["Clearer wording", "Corrected a detail", "Out of date"], reason: $reason)
            TextField("Reason (recorded in the history)", text: $reason)
                .frame(width: 480)
            HStack {
                Spacer()
                Button("Cancel") { sheet = nil }
                    .keyboardShortcut(.cancelAction)
                Button("Save") {
                    let text = tidyDraft
                    let why = trimmedReason
                    sheet = nil
                    Task { await saveEdit(id: id, text: text, reason: why) }
                }
                .keyboardShortcut(.defaultAction)
                .disabled(trimmedReason.isEmpty || !draftFits || tidyDraft == tidyText(original))
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
            draftEditor(height: 110)
            QuickReasons(choices: ["Team rule", "Decided outside a session"], reason: $reason)
            TextField("Reason (recorded in the history)", text: $reason)
                .frame(width: 480)
            HStack {
                Spacer()
                Button("Cancel") { sheet = nil }
                    .keyboardShortcut(.cancelAction)
                Button("Add") {
                    let text = tidyDraft
                    let why = trimmedReason
                    let kind = draftKind
                    sheet = nil
                    Task { await saveNew(project: project, kind: kind, text: text, reason: why) }
                }
                .keyboardShortcut(.defaultAction)
                .disabled(trimmedReason.isEmpty || !draftFits)
            }
        }
        .padding(20)
    }

    private func draftEditor(height: CGFloat) -> some View {
        VStack(alignment: .trailing, spacing: 4) {
            TextEditor(text: $draft)
                .font(.body)
                .frame(width: 480, height: height)
                .border(Color.secondary.opacity(0.3))
            HStack {
                Text("One line is stored: line breaks become spaces.")
                    .font(.caption)
                    .foregroundColor(.secondary)
                Spacer()
                LengthCount(text: draft)
            }
            .frame(width: 480)
        }
    }

    // MARK: helpers

    private func known(_ id: String) -> String? {
        if let detail, detail.id == id { return detail.status }
        return memories.first(where: { $0.id == id })?.status
    }

    private func text(of id: String) -> String? {
        if let detail, detail.id == id { return detail.text }
        return memories.first(where: { $0.id == id })?.text
    }

    private func retirable(_ ids: [String]) -> [String] {
        ids.filter { known($0) == "active" }
    }

    private func restorable(_ ids: [String]) -> [String] {
        ids.filter { ["retired", "stale"].contains(known($0) ?? "") }
    }

    private func ask(ids: [String], restore: Bool) {
        guard !ids.isEmpty, !working else { return }
        reason = ""
        sheet = .status(ids: ids, restore: restore)
    }

    private func startEdit(id: String, text: String) {
        guard !working else { return }
        reason = ""
        draft = text
        sheet = .edit(id: id, text: text)
    }

    private func startAdd(project: String) {
        guard !working else { return }
        reason = ""
        draft = ""
        draftKind = kind.isEmpty ? "fact" : kind
        sheet = .add(project: project)
    }

    private func copy(_ ids: [String]) {
        let wanted = Set(ids)
        var texts = memories.filter { wanted.contains($0.id) }.map(\.text)
        if texts.isEmpty, let detail, wanted.contains(detail.id) { texts = [detail.text] }
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(texts.joined(separator: "\n"), forType: .string)
    }

    /// Show an entry that may not be in the list (another status or kind).
    private func open(_ id: String) {
        status = "all"
        kind = ""
        reveal = id
        selection = [id]
    }

    private func takeTarget() {
        guard let target = app.memoryTarget else { return }
        app.memoryTarget = nil
        query = ""
        everywhere = false
        projectFilter = ""
        kind = ""
        status = "all"
        keepSelection = target.project != selectedProject
        selectedProject = target.project
        revealProject = true
        reveal = target.id
        selection = [target.id]
    }

    // MARK: loading and changing

    @MainActor
    private func reloadAll() async {
        await loadProjects()
        await loadMemories(wait: false)
        await loadDetail()
    }

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
        let project = selectedProject
        let allWorkspaces = !words.isEmpty && everywhere
        guard allWorkspaces || project != nil else {
            memories = []
            return
        }
        loading = true
        defer { loading = false }
        do {
            let backend = Backend.current()
            let found: [Memory]
            if words.isEmpty {
                found = try await backend.memories(project: project ?? "", status: status)
            } else {
                found = try await backend.search(
                    query: words, project: allWorkspaces ? nil : project, status: status)
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
        // A later request, or a change made meanwhile, makes this one's answer stale.
        detailRequest += 1
        let request = detailRequest
        guard selection.count == 1, let id = selection.first else {
            detail = nil
            detailError = nil
            return
        }
        do {
            let loaded = try await Backend.current().memory(id: id)
            guard request == detailRequest, selection == [id] else { return }
            detail = loaded
            detailError = nil
        } catch {
            guard request == detailRequest, selection == [id] else { return }
            detail = nil
            detailError = error.localizedDescription
        }
    }

    @MainActor
    private func change(ids: [String], restore: Bool, reason: String) async {
        guard !working, !ids.isEmpty else { return }
        working = true
        detailRequest += 1  // what is being loaded now predates this change
        defer { working = false }
        outcome = nil
        let backend = Backend.current()
        var result = BatchOutcome()
        for id in ids {
            do {
                _ =
                    restore
                    ? try await backend.restore(id: id, reason: reason)
                    : try await backend.retire(id: id, reason: reason)
                result.done += 1
            } catch {
                result.failures.append(error.localizedDescription)
            }
        }
        outcome = ActionNote(result, verb: restore ? "Restored" : "Retired")
        if ids.count > 1 { selection = [] }
        await loadMemories(wait: false)
        await loadProjects()
        // What is selected now, which need not be what was changed.
        await loadDetail()
    }

    @MainActor
    private func saveEdit(id: String, text: String, reason: String) async {
        guard !working else { return }
        working = true
        detailRequest += 1  // what is being loaded now predates this change
        defer { working = false }
        outcome = nil
        do {
            let updated = try await Backend.current().edit(id: id, text: text, reason: reason)
            outcome = ActionNote(text: "Saved. The old wording is kept as superseded.")
            await loadMemories(wait: false)
            // Follow the entry to its new wording only if it is still the one in view.
            if selection == [id] {
                reveal = updated.id
                selection = [updated.id]
            }
        } catch {
            outcome = ActionNote(text: error.localizedDescription, failed: true)
        }
    }

    @MainActor
    private func saveNew(project: String, kind: String, text: String, reason: String) async {
        guard !working else { return }
        working = true
        defer { working = false }
        outcome = nil
        do {
            let added = try await Backend.current().add(
                project: project, kind: kind, text: text, reason: reason)
            outcome = ActionNote(text: "Added.")
            // Make sure the new entry is in the list that is reloaded.
            if status != "active" && status != "all" { status = "active" }
            if !self.kind.isEmpty && self.kind != kind { self.kind = "" }
            // Also while searching: the new entry may match what is being looked for.
            await loadMemories(wait: false)
            await loadProjects()
            reveal = added.id
            selection = [added.id]
        } catch {
            outcome = ActionNote(text: error.localizedDescription, failed: true)
        }
    }
}
