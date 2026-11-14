import SwiftUI

struct SessionsPane: View {
    @EnvironmentObject private var app: AppState
    @State private var projects: [Project] = []
    @State private var project = ""
    @State private var tool = ""
    @State private var query = ""
    @State private var pendingOnly = false
    @State private var limit = pageSize
    @State private var sessions: [SessionSummary] = []
    @State private var selection: Set<String> = []
    @State private var detail: SessionDetail?
    @State private var message: String?
    @State private var note: String?
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
                ErrorBanner(message: message).padding(8)
            } else if let note {
                InfoBanner(message: note).padding(8)
            }
            HSplitView {
                listColumn.frame(minWidth: 340, idealWidth: 420)
                detailView.frame(minWidth: 380, maxWidth: .infinity)
            }
        }
        .navigationTitle("Sessions")
        .searchable(text: $query, prompt: "Search title, summary or key")
        .toolbar {
            ToolbarItem {
                Button {
                    Task { await loadSessions(wait: false) }
                } label: {
                    Label("Reload", systemImage: "arrow.clockwise")
                }
                .disabled(loading)
            }
        }
        .task { await loadProjects() }
        .task(id: listKey) { await loadSessions(wait: !query.isEmpty) }
        .task(id: selection) { await loadDetail() }
        .onAppear { takeTarget() }
        .onChange(of: app.sessionTarget) { _ in takeTarget() }
    }

    private var listKey: String {
        [project, tool, query, pendingOnly ? "1" : "0", String(limit)].joined(separator: "|")
    }

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
                .frame(maxWidth: 170)
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
        var line = "\(sessions.count) shown"
        if selection.count > 1 { line += " · \(selection.count) selected" }
        return line
    }

    private func row(_ session: SessionSummary) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack(spacing: 6) {
                Text(session.tool ?? "?").font(.caption).foregroundColor(.secondary)
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
        }
        if !done.isEmpty {
            Button(done.count == 1 ? "Curate Again" : "Curate \(done.count) Again") {
                Task { await change(keys: done, requeue: true) }
            }
        }
    }

    @ViewBuilder
    private var detailView: some View {
        if selection.count > 1 {
            batchView
        } else if let detail {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    VStack(alignment: .leading, spacing: 6) {
                        HStack(spacing: 8) {
                            if detail.pending == true {
                                PendingBadge()
                            }
                            Spacer()
                            if detail.pending == true {
                                Button("Skip Curation") {
                                    Task { await change(keys: [detail.key], requeue: false) }
                                }
                                .help("Take it out of the queue; the next run leaves it alone")
                            } else {
                                Button("Curate Again") {
                                    Task { await change(keys: [detail.key], requeue: true) }
                                }
                                .help("The next run reads this session again")
                            }
                        }
                        .disabled(working)
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
            Placeholder(text: "Choose a session")
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
                project: project, tool: tool, query: query, pendingOnly: pendingOnly, limit: limit)
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
    private func change(keys: [String], requeue: Bool) async {
        guard !keys.isEmpty else { return }
        working = true
        defer { working = false }
        let backend = Backend.current()
        var outcome = BatchOutcome()
        var last: SessionDetail?
        for key in keys {
            do {
                last =
                    requeue
                    ? try await backend.requeue(key: key) : try await backend.skip(key: key)
                outcome.done += 1
            } catch {
                outcome.failures.append(error.localizedDescription)
            }
        }
        let text = outcome.summary(verb: requeue ? "Queued for curation:" : "Skipped:")
        if outcome.failures.isEmpty {
            message = nil
            note = text
        } else {
            note = nil
            message = text
        }
        if keys.count == 1, let last { detail = last }
        await loadSessions(wait: false)
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
