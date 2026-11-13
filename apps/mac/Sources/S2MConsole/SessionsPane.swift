import SwiftUI

struct SessionsPane: View {
    @State private var projects: [Project] = []
    @State private var project = ""
    @State private var tool = ""
    @State private var sessions: [SessionSummary] = []
    @State private var selected: String?
    @State private var detail: SessionDetail?
    @State private var message: String?
    @State private var loading = false

    private let tools = [
        "", "codex", "claude", "grok", "muse", "antigravity", "cursor", "cursor-cli",
        "openclaw-native",
    ]

    var body: some View {
        VStack(spacing: 0) {
            if let message {
                ErrorBanner(message: message).padding(8)
            }
            HSplitView {
                listColumn.frame(minWidth: 320, idealWidth: 400)
                detailView.frame(minWidth: 380, maxWidth: .infinity)
            }
        }
        .navigationTitle("Sessions")
        .toolbar {
            ToolbarItem {
                Button {
                    Task { await loadSessions() }
                } label: {
                    Label("Reload", systemImage: "arrow.clockwise")
                }
                .disabled(loading)
            }
        }
        .task { await loadProjects() }
        .task(id: project + "|" + tool) { await loadSessions() }
        .task(id: selected) { await loadDetail() }
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
            .padding(8)
            if sessions.isEmpty {
                Placeholder(text: loading ? "Loading…" : "No sessions")
            } else {
                List(sessions, selection: $selected) { session in
                    row(session).tag(session.key)
                }
            }
        }
    }

    private func row(_ session: SessionSummary) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack(spacing: 6) {
                Text(session.tool ?? "?").font(.caption).foregroundColor(.secondary)
                if session.pending == true {
                    Text("pending")
                        .font(.caption)
                        .padding(.horizontal, 5)
                        .background(Color.yellow.opacity(0.25))
                        .cornerRadius(4)
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
    private var detailView: some View {
        if let detail {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    VStack(alignment: .leading, spacing: 6) {
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
                                Text(memory.text).textSelection(.enabled)
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

    @MainActor
    private func loadProjects() async {
        do {
            projects = try await Backend.current().projects()
        } catch {
            message = error.localizedDescription
        }
    }

    @MainActor
    private func loadSessions() async {
        loading = true
        defer { loading = false }
        do {
            sessions = try await Backend.current().sessions(
                project: project, tool: tool, limit: 300)
            message = nil
        } catch {
            sessions = []
            message = error.localizedDescription
        }
    }

    @MainActor
    private func loadDetail() async {
        guard let key = selected else {
            detail = nil
            return
        }
        do {
            detail = try await Backend.current().session(key: key)
            message = nil
        } catch {
            detail = nil
            message = error.localizedDescription
        }
    }
}
