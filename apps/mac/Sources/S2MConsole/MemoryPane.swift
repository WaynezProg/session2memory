import SwiftUI

struct MemoryPane: View {
    @State private var projects: [Project] = []
    @State private var selectedProject: String?
    @State private var status = "active"
    @State private var memories: [Memory] = []
    @State private var selectedMemory: String?
    @State private var detail: MemoryDetail?
    @State private var message: String?
    @State private var loading = false
    @State private var pendingAction: PendingAction?
    @State private var reason = ""

    private struct PendingAction: Identifiable {
        let id: String
        let restore: Bool
    }

    private let statuses = ["active", "superseded", "retired", "stale", "all"]

    var body: some View {
        VStack(spacing: 0) {
            if let message {
                ErrorBanner(message: message).padding(8)
            }
            HSplitView {
                projectList.frame(minWidth: 200, idealWidth: 230, maxWidth: 320)
                memoryList.frame(minWidth: 280, idealWidth: 360)
                detailView.frame(minWidth: 340, maxWidth: .infinity)
            }
        }
        .navigationTitle("Memory")
        .toolbar {
            ToolbarItem {
                Button {
                    Task { await loadProjects() }
                } label: {
                    Label("Reload", systemImage: "arrow.clockwise")
                }
                .disabled(loading)
            }
        }
        .task { await loadProjects() }
        .task(id: listKey) { await loadMemories() }
        .task(id: selectedMemory) { await loadDetail() }
        .sheet(item: $pendingAction) { action in
            reasonSheet(action)
        }
    }

    private var listKey: String { (selectedProject ?? "") + "|" + status }

    private var projectList: some View {
        List(projects, selection: $selectedProject) { project in
            VStack(alignment: .leading, spacing: 2) {
                Text(project.name).fontWeight(.medium)
                Text("\(project.active) active · \(project.sessions) sessions")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
            .help(project.project)
            .tag(project.project)
        }
    }

    private var memoryList: some View {
        VStack(spacing: 0) {
            Picker("Status", selection: $status) {
                ForEach(statuses, id: \.self) { item in
                    Text(item).tag(item)
                }
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .padding(8)
            if selectedProject == nil {
                Placeholder(text: "Choose a workspace")
            } else if memories.isEmpty {
                Placeholder(text: loading ? "Loading…" : "No memory with this status")
            } else {
                List(memories, selection: $selectedMemory) { memory in
                    VStack(alignment: .leading, spacing: 4) {
                        HStack(spacing: 6) {
                            Text(memory.kind).font(.caption).foregroundColor(.secondary)
                            if status == "all" {
                                StatusBadge(status: memory.status)
                            }
                            Spacer()
                            Text(shortTime(memory.lastConfirmedAt))
                                .font(.caption)
                                .foregroundColor(.secondary)
                        }
                        Text(memory.text).lineLimit(3)
                    }
                    .padding(.vertical, 2)
                    .tag(memory.id)
                }
            }
        }
    }

    @ViewBuilder
    private var detailView: some View {
        if let detail {
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

    private func header(_ detail: MemoryDetail) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                StatusBadge(status: detail.status)
                Text(detail.kind).foregroundColor(.secondary)
                Text(detail.scope).foregroundColor(.secondary)
                Spacer()
                if detail.status == "active" {
                    Button("Retire…") {
                        reason = ""
                        pendingAction = PendingAction(id: detail.id, restore: false)
                    }
                } else if detail.restorable {
                    Button("Restore…") {
                        reason = ""
                        pendingAction = PendingAction(id: detail.id, restore: true)
                    }
                }
            }
            Text(detail.text).font(.title3).textSelection(.enabled)
            Text(
                "\(detail.id) · created \(shortTime(detail.createdAt)) · last confirmed \(shortTime(detail.lastConfirmedAt))"
            )
            .font(.caption)
            .foregroundColor(.secondary)
            .textSelection(.enabled)
        }
    }

    @ViewBuilder
    private func links(_ detail: MemoryDetail) -> some View {
        if detail.supersedesId != nil || !detail.replacedBy.isEmpty {
            Block("Related") {
                if let older = detail.supersedesId {
                    Button("Replaced \(older)") { selectedMemory = older }
                        .buttonStyle(.link)
                }
                ForEach(detail.replacedBy, id: \.self) { newer in
                    Button("Replaced by \(newer)") { selectedMemory = newer }
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
                    Text(session.title ?? session.key).fontWeight(.medium)
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

    private func reasonSheet(_ action: PendingAction) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(action.restore ? "Restore this memory" : "Retire this memory").font(.headline)
            Text(
                action.restore
                    ? "It becomes active again and is used in new sessions."
                    : "It is kept, but no longer searched, exported or given to new sessions."
            )
            .foregroundColor(.secondary)
            TextField("Reason (recorded in the history)", text: $reason)
                .frame(width: 380)
            HStack {
                Spacer()
                Button("Cancel") { pendingAction = nil }
                    .keyboardShortcut(.cancelAction)
                Button(action.restore ? "Restore" : "Retire") {
                    let why = reason.trimmingCharacters(in: .whitespacesAndNewlines)
                    pendingAction = nil
                    Task { await apply(action, reason: why) }
                }
                .keyboardShortcut(.defaultAction)
                .disabled(reason.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
        .padding(20)
    }

    // MARK: loading

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
    private func loadMemories() async {
        guard let project = selectedProject else {
            memories = []
            return
        }
        loading = true
        defer { loading = false }
        do {
            memories = try await Backend.current().memories(project: project, status: status)
            message = nil
        } catch {
            memories = []
            message = error.localizedDescription
        }
    }

    @MainActor
    private func loadDetail() async {
        guard let id = selectedMemory else {
            detail = nil
            return
        }
        do {
            detail = try await Backend.current().memory(id: id)
            message = nil
        } catch {
            detail = nil
            message = error.localizedDescription
        }
    }

    @MainActor
    private func apply(_ action: PendingAction, reason: String) async {
        do {
            let backend = Backend.current()
            if action.restore {
                detail = try await backend.restore(id: action.id, reason: reason)
            } else {
                detail = try await backend.retire(id: action.id, reason: reason)
            }
            message = nil
            await loadMemories()
            await loadProjects()
        } catch {
            message = error.localizedDescription
        }
    }
}
