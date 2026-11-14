import SwiftUI

struct SessionsPane: View {
    @EnvironmentObject private var app: AppState
    @State private var projects: [Project] = []
    @State private var project = ""
    @State private var tool = ""
    @State private var scope = ""
    @State private var query = ""
    @State private var pendingOnly = false
    @State private var limit = pageSize
    @State private var sessions: [SessionSummary] = []
    @State private var selection: Set<String> = []
    @State private var detail: SessionDetail?
    /// Why the last load failed, if it did.
    @State private var message: String?
    /// What the last change came to.
    @State private var outcome: ActionNote?
    @State private var loading = false
    @State private var working = false

    private static let pageSize = 300

    private let tools = [
        "", "codex", "claude", "grok", "muse", "antigravity", "cursor", "cursor-cli",
        "openclaw-native",
    ]

    var body: some View {
        VStack(spacing: 0) {
            if let message {
                ErrorBanner(message: message).padding([.horizontal, .top], 8)
            }
            if let outcome {
                NoteBanner(note: outcome) { self.outcome = nil }.padding([.horizontal, .top], 8)
            }
            HSplitView {
                listColumn.frame(minWidth: 360, idealWidth: 430)
                detailView.frame(minWidth: 380, maxWidth: .infinity)
            }
        }
        .navigationTitle("Sessions")
        .searchable(text: $query, prompt: "Search title, summary or key")
        .toolbar {
            ToolbarItem {
                Button {
                    Task {
                        await loadSessions(wait: false)
                        await loadDetail()
                    }
                } label: {
                    Label("Reload", systemImage: "arrow.clockwise")
                }
                .keyboardShortcut("r", modifiers: .command)
                .disabled(loading)
                .help("Reload (⌘R)")
            }
        }
        .task { await loadProjects() }
        .task(id: listKey) { await loadSessions(wait: !query.isEmpty) }
        .task(id: selection) { await loadDetail() }
        .onAppear { takeTarget() }
        .onChange(of: app.sessionTarget) { _ in takeTarget() }
        .onChange(of: filterKey) { _ in
            // Another set of sessions: start again from the first page.
            limit = Self.pageSize
        }
    }

    private var filterKey: String {
        [project, tool, scope, query, pendingOnly ? "1" : "0"].joined(separator: "|")
    }

    private var listKey: String { filterKey + "|" + String(limit) }

    private var listColumn: some View {
        VStack(spacing: 0) {
            HStack {
                Picker("Workspace", selection: $project) {
                    Text("All workspaces").tag("")
                    ForEach(projects) { item in
                        Text(item.name).tag(item.project)
                    }
                }
                Picker("Tool", selection: $tool) {
                    ForEach(tools, id: \.self) { item in
                        Text(item.isEmpty ? "All tools" : item).tag(item)
                    }
                }
                .frame(maxWidth: 150)
                Picker("Scope", selection: $scope) {
                    Text("All scopes").tag("")
                    Text("coding").tag("coding")
                    Text("openclaw").tag("openclaw")
                }
                .frame(maxWidth: 120)
                .help("coding: sessions of coding tools. openclaw: OpenClaw agents' own sessions.")
            }
            .labelsHidden()
            .padding([.horizontal, .top], 8)
            HStack {
                Toggle("Waiting for curation only", isOn: $pendingOnly)
                    .toggleStyle(.checkbox)
                Spacer()
                Text(countLine).font(.caption).foregroundColor(.secondary)
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 6)
            if sessions.isEmpty {
                Placeholder(text: loading ? "Loading…" : "No sessions")
            } else {
                List(selection: $selection) {
                    ForEach(sessions) { session in
                        row(session).tag(session.key)
                    }
                    if sessions.count >= limit {
                        Button("Load \(Self.pageSize) more") { limit += Self.pageSize }
                            .buttonStyle(.link)
                            .disabled(loading)
                    }
                }
                .contextMenu(forSelectionType: String.self) { keys in
                    rowMenu(Array(keys))
                }
            }
        }
    }

    private var countLine: String {
        var line = sessions.count >= limit ? "first \(sessions.count) shown" : "\(sessions.count) shown"
        if selection.count > 1 { line += " · \(selection.count) selected" }
        return line
    }

    private func row(_ session: SessionSummary) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack(spacing: 6) {
                Text(session.tool ?? "?").font(.caption).foregroundColor(.secondary)
                if session.scope == "openclaw" {
                    Text("openclaw").font(.caption).foregroundColor(.secondary)
                }
                if session.pending == true {
                    PendingBadge()
                }
                Spacer()
                Text(shortTime(session.lastActivity)).font(.caption).foregroundColor(.secondary)
            }
            Text(session.title ?? session.sessionId ?? session.key).lineLimit(2)
            if let project = session.project {
                Text((project as NSString).lastPathComponent)
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
        }
        .padding(.vertical, 2)
    }

    @ViewBuilder
    private func rowMenu(_ keys: [String]) -> some View {
        let waiting = keys.filter { isPending($0) == true }
        let done = keys.filter { isPending($0) == false }
        if !waiting.isEmpty {
            Button(waiting.count == 1 ? "Skip Curation" : "Skip Curation for \(waiting.count)") {
                Task { await change(keys: waiting, requeue: false) }
            }
            .disabled(working)
        }
        if !done.isEmpty {
            Button(done.count == 1 ? "Curate Again" : "Curate \(done.count) Again") {
                Task { await change(keys: done, requeue: true) }
            }
            .disabled(working)
        }
    }

    @ViewBuilder
    private var detailView: some View {
        if selection.count > 1 {
            batchView
        } else if let detail, selection.contains(detail.key) {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    VStack(alignment: .leading, spacing: 6) {
                        queueControls(detail)
                        Text(detail.title ?? detail.key).font(.title3).textSelection(.enabled)
                        Text(
                            "\(detail.key) · \(detail.scope ?? "–") · \(shortTime(detail.lastActivity))"
                        )
                        .font(.caption)
                        .foregroundColor(.secondary)
                        .textSelection(.enabled)
                        if let project = detail.project {
                            Text(project).font(.caption).foregroundColor(.secondary)
                                .textSelection(.enabled)
                        }
                    }
                    Block("Summary" + (detail.summaryOrigin.map { " (\($0))" } ?? "")) {
                        Text(detail.summary ?? "Not summarised yet")
                            .foregroundColor(detail.summary == nil ? .secondary : .primary)
                            .textSelection(.enabled)
                    }
                    Block("Memory this session is evidence for") {
                        if detail.memories.isEmpty {
                            Text("None").foregroundColor(.secondary)
                        }
                        ForEach(detail.memories) { memory in
                            HStack(alignment: .top, spacing: 6) {
                                StatusBadge(status: memory.status)
                                Button {
                                    app.show(memory: memory.id, in: memory.project)
                                } label: {
                                    Text(memory.text).multilineTextAlignment(.leading)
                                }
                                .buttonStyle(.link)
                                .help("Show this memory")
                            }
                        }
                    }
                    Block("Digest (what the model was shown)") {
                        CodeText(text: detail.digest ?? "–")
                    }
                }
                .padding(16)
            }
        } else {
            Placeholder(text: selection.isEmpty ? "Choose a session" : "Loading…")
        }
    }

    /// The queue state of one session, the button that changes it, and, when the run
    /// would pass over the session anyway, why.
    private func queueControls(_ detail: SessionDetail) -> some View {
        let blocked = detail.curation?.blocked ?? []
        let waiting = detail.pending == true
        return VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 8) {
                if waiting {
                    PendingBadge()
                    Text(blocked.isEmpty ? "The next run reads it." : "The next run passes over it.")
                        .font(.caption)
                        .foregroundColor(.secondary)
                } else {
                    Text("Not in the curation queue").font(.caption).foregroundColor(.secondary)
                }
                Spacer()
                if working { ProgressView().controlSize(.small) }
                if waiting {
                    Button("Skip Curation") {
                        Task { await change(keys: [detail.key], requeue: false) }
                    }
                    .help("Take it out of the queue; the run leaves it alone")
                } else {
                    Button("Curate Again") {
                        Task { await change(keys: [detail.key], requeue: true) }
                    }
                    .disabled(!blocked.isEmpty)
                    .help(
                        blocked.isEmpty
                            ? "The next run reads this session again"
                            : "The run would pass over it; see below")
                }
            }
            .disabled(working)
            ForEach(blocked, id: \.self) { why in
                Text("Would not be curated: \(why).")
                    .font(.caption)
                    .foregroundColor(.orange)
            }
            if let note = detail.curation?.note {
                Text(note).font(.caption).foregroundColor(.secondary)
            }
        }
    }

    private var batchView: some View {
        let keys = Array(selection)
        let waiting = keys.filter { isPending($0) == true }
        let done = keys.filter { isPending($0) == false }
        return VStack(spacing: 14) {
            Text("\(keys.count) sessions selected").font(.title3)
            Text("Memory a session already led to is kept either way.")
                .foregroundColor(.secondary)
            HStack(spacing: 10) {
                Button("Skip Curation for \(waiting.count)") {
                    Task { await change(keys: waiting, requeue: false) }
                }
                .disabled(waiting.isEmpty || working)
                Button("Curate \(done.count) Again") {
                    Task { await change(keys: done, requeue: true) }
                }
                .disabled(done.isEmpty || working)
            }
            if working { ProgressView().controlSize(.small) }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(20)
    }

    private func isPending(_ key: String) -> Bool? {
        if let detail, detail.key == key { return detail.pending }
        return sessions.first(where: { $0.key == key })?.pending
    }

    private func takeTarget() {
        guard let key = app.sessionTarget else { return }
        app.sessionTarget = nil
        project = ""
        tool = ""
        scope = ""
        query = ""
        pendingOnly = false
        selection = [key]
    }

    @MainActor
    private func loadProjects() async {
        do {
            projects = try await Backend.current().projects()
        } catch {
            message = error.localizedDescription
        }
    }

    @MainActor
    private func loadSessions(wait: Bool) async {
        if wait {
            // Typing: wait for a pause before asking.
            try? await Task.sleep(nanoseconds: 300_000_000)
            if Task.isCancelled { return }
        }
        loading = true
        defer { loading = false }
        do {
            let found = try await Backend.current().sessions(
                project: project, tool: tool, scope: scope, query: query,
                pendingOnly: pendingOnly, limit: limit)
            if Task.isCancelled { return }
            sessions = found
            message = nil
        } catch {
            if Task.isCancelled { return }
            sessions = []
            message = error.localizedDescription
        }
    }

    @MainActor
    private func loadDetail() async {
        guard selection.count == 1, let key = selection.first else {
            detail = nil
            return
        }
        do {
            let loaded = try await Backend.current().session(key: key)
            // The selection may have moved on while the command ran.
            guard !Task.isCancelled, selection == [key] else { return }
            detail = loaded
            message = nil
        } catch {
            guard !Task.isCancelled, selection == [key] else { return }
            detail = nil
            message = error.localizedDescription
        }
    }

    @MainActor
    private func change(keys: [String], requeue: Bool) async {
        guard !working, !keys.isEmpty else { return }
        working = true
        defer { working = false }
        outcome = nil
        let backend = Backend.current()
        var result = BatchOutcome()
        for key in keys {
            do {
                _ = requeue ? try await backend.requeue(key: key) : try await backend.skip(key: key)
                result.done += 1
            } catch {
                result.failures.append(error.localizedDescription)
            }
        }
        outcome = ActionNote(result, verb: requeue ? "Queued for curation:" : "Skipped:")
        await loadSessions(wait: false)
        // What is selected now, which need not be what was changed.
        await loadDetail()
    }
}

private struct PendingBadge: View {
    var body: some View {
        Text("pending")
            .font(.caption)
            .padding(.horizontal, 5)
            .background(Color.yellow.opacity(0.25))
            .cornerRadius(4)
            .help("Waiting for curation")
    }
}
