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
    /// Why the selected session could not be loaded.
    @State private var detailError: String?
    /// Counts detail requests: only the answer to the latest one is shown.
    @State private var detailRequest = 0
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
            PageHeader("對話", subtitle: "各個代理人留下的對話。整理過的對話會變成記憶。") {
                SearchField(text: $query, prompt: "搜尋標題或摘要")
                Button {
                    Task {
                        await loadSessions(wait: false)
                        await loadDetail()
                    }
                } label: {
                    Image(systemName: "arrow.clockwise")
                }
                .buttonStyle(OutlineButtonStyle())
                .keyboardShortcut("r", modifiers: .command)
                .disabled(loading)
                .help("重新讀取（⌘R）")
            }
            if let message {
                ErrorBanner(message: message).padding(.horizontal, 32).padding(.bottom, 10)
            }
            if let outcome {
                NoteBanner(note: outcome) { self.outcome = nil }
                    .padding(.horizontal, 32)
                    .padding(.bottom, 10)
            }
            filterBar
            Rectangle().fill(Theme.hairline).frame(height: 1)
            HStack(spacing: 0) {
                listColumn
                Rectangle().fill(Theme.hairline).frame(width: 1)
                detailView.detailPanel(width: 430)
            }
        }
        .task { await loadProjects() }
        .task(id: listKey) { await loadSessions(wait: !query.isEmpty) }
        .task(id: selection) { await loadDetail() }
        .onAppear { takeTarget() }
        .onChange(of: app.sessionTarget) { _ in takeTarget() }
        .onChange(of: app.sessionsPendingOnly) { _ in takeTarget() }
        .onChange(of: filterKey) { _ in
            // Another set of sessions: start again from the first page.
            limit = Self.pageSize
        }
    }

    private var filterKey: String {
        [project, tool, scope, query, pendingOnly ? "1" : "0"].joined(separator: "|")
    }

    private var listKey: String { filterKey + "|" + String(limit) }

    private var filterBar: some View {
        HStack(spacing: 10) {
            Chip(title: "全部", selected: !pendingOnly) { pendingOnly = false }
            Chip(title: "等待整理", selected: pendingOnly) { pendingOnly = true }
            Text(countLine).font(.system(size: 12)).foregroundColor(Theme.secondary)
            Spacer()
            Picker("專案", selection: $project) {
                Text("所有專案").tag("")
                ForEach(projects) { item in
                    Text(item.name).tag(item.project)
                }
            }
            .frame(maxWidth: 200)
            Picker("工具", selection: $tool) {
                ForEach(tools, id: \.self) { item in
                    Text(item.isEmpty ? "所有工具" : Lexicon.tool(item)).tag(item)
                }
            }
            .fixedSize()
            Picker("範圍", selection: $scope) {
                Text("所有範圍").tag("")
                Text("寫程式的對話").tag("coding")
                Text("OpenClaw 代理人自己的對話").tag("openclaw")
            }
            .fixedSize()
        }
        .labelsHidden()
        .padding(.horizontal, 32)
        .padding(.bottom, 14)
    }

    private var countLine: String {
        var line = sessions.count >= limit ? "先顯示 \(sessions.count) 段" : "\(sessions.count) 段"
        if selection.count > 1 { line += " · 已選 \(selection.count) 段" }
        return line
    }

    private var listColumn: some View {
        Group {
            if sessions.isEmpty {
                Placeholder(text: loading ? "讀取中…" : "沒有符合的對話")
            } else {
                List(selection: $selection) {
                    ForEach(sessions) { session in
                        row(session)
                            .listCell(gap: 2)
                            .tag(session.key)
                    }
                    if sessions.count >= limit {
                        Button("再載入 \(Self.pageSize) 段") { limit += Self.pageSize }
                            .buttonStyle(.plain)
                            .font(.system(size: 13, weight: .semibold))
                            .foregroundColor(Theme.accent)
                            .disabled(loading)
                            .listCell(gap: 8)
                    }
                }
                .listStyle(.plain)
                .scrollContentBackground(.hidden)
                .contextMenu(forSelectionType: String.self) { keys in
                    rowMenu(Array(keys))
                }
                .padding(.horizontal, 16)
                .padding(.top, 8)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private func row(_ session: SessionSummary) -> some View {
        let chosen = selection.contains(session.key)
        return HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 2) {
                Text(session.title ?? "尚未命名的對話")
                    .font(.system(size: 15, weight: chosen ? .semibold : .medium))
                    .foregroundColor(session.title == nil ? Theme.secondary : Theme.ink)
                    .lineLimit(2)
                Text(metaLine(session))
                    .font(.system(size: 12))
                    .foregroundColor(Theme.secondary)
                    .lineLimit(1)
            }
            Spacer(minLength: 8)
            StateTag(pending: session.pending, summary: session.summary)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
        .background(chosen ? Theme.accentSoft : Color.clear)
        .overlay(
            RoundedRectangle(cornerRadius: 12).stroke(chosen ? Theme.accent : Color.clear, lineWidth: 2)
        )
        .cornerRadius(12)
    }

    private func metaLine(_ session: SessionSummary) -> String {
        var parts = [Lexicon.tool(session.tool)]
        if let project = session.project {
            parts.append((project as NSString).lastPathComponent)
        } else {
            parts.append("不知道屬於哪個專案")
        }
        parts.append(localTime(session.lastActivity))
        return parts.joined(separator: " · ")
    }

    @ViewBuilder
    private func rowMenu(_ keys: [String]) -> some View {
        let waiting = keys.filter { isPending($0) == true }
        let done = keys.filter { isPending($0) == false }
        if !waiting.isEmpty {
            Button(waiting.count == 1 ? "略過，不整理" : "略過 \(waiting.count) 段") {
                Task { await change(keys: waiting, requeue: false) }
            }
            .disabled(working)
        }
        if !done.isEmpty {
            Button(done.count == 1 ? "再整理一次" : "再整理 \(done.count) 段") {
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
                VStack(alignment: .leading, spacing: 22) {
                    VStack(alignment: .leading, spacing: 10) {
                        HStack(spacing: 8) {
                            StateTag(pending: detail.pending, summary: detail.summary)
                            Text("\(Lexicon.tool(detail.tool)) · \(localTime(detail.lastActivity))")
                                .font(.system(size: 12))
                                .foregroundColor(Theme.secondary)
                            Spacer()
                            if working { ProgressView().controlSize(.small) }
                        }
                        Text(detail.title ?? "尚未命名的對話")
                            .font(.system(size: 20, weight: .bold))
                            .textSelection(.enabled)
                            .fixedSize(horizontal: false, vertical: true)
                        if let project = detail.project {
                            Text("專案：\((project as NSString).lastPathComponent)")
                                .font(.system(size: 12))
                                .foregroundColor(Theme.secondary)
                                .help(project)
                        }
                        queueControls(detail)
                    }
                    Block("這段對話在做什麼") {
                        Text(detail.summary ?? "還沒有摘要。")
                            .font(.system(size: 14))
                            .lineSpacing(4)
                            .foregroundColor(detail.summary == nil ? Theme.secondary : Theme.ink)
                            .textSelection(.enabled)
                    }
                    Block("從這段對話學到的記憶") {
                        if detail.memories.isEmpty {
                            Text("還沒有。").font(.system(size: 13)).foregroundColor(Theme.secondary)
                        }
                        ForEach(detail.memories) { memory in
                            Button {
                                app.show(memory: memory.id, in: memory.project)
                            } label: {
                                VStack(alignment: .leading, spacing: 4) {
                                    if memory.status != "active" {
                                        StatusBadge(status: memory.status)
                                    }
                                    Text(memory.text)
                                        .font(.system(size: 13))
                                        .lineSpacing(3)
                                        .foregroundColor(Theme.ink)
                                        .multilineTextAlignment(.leading)
                                }
                                .card(padding: 10)
                            }
                            .buttonStyle(.plain)
                            .help("到「記憶」看這條記憶")
                        }
                    }
                    EngineeringDetails("模型讀到的內容（給工程用）") {
                        Text(detail.key).font(.system(size: 12)).textSelection(.enabled)
                        ForEach(detail.curation?.notes ?? [], id: \.self) { note in
                            Text(note).font(.system(size: 12)).foregroundColor(Theme.secondary)
                        }
                        CodeText(text: detail.digest ?? "–")
                    }
                }
                .padding(24)
            }
        } else if let detailError, !selection.isEmpty {
            LoadFailed(message: detailError) { Task { await loadDetail() } }
        } else {
            Placeholder(text: selection.isEmpty ? "選一段對話，看它的摘要與學到的記憶" : "讀取中…")
        }
    }

    /// The queue state of one session, the buttons that change it, and what the run would
    /// do with it.
    private func queueControls(_ detail: SessionDetail) -> some View {
        let blocked = detail.curation?.blocked ?? []
        let expires = detail.curation?.expires == true
        let waiting = detail.pending == true
        return VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                if waiting {
                    if expires {
                        Button("仍要整理") {
                            Task { await change(keys: [detail.key], requeue: true) }
                        }
                        .buttonStyle(OutlineButtonStyle(accent: true))
                        .help("已經等超過期限，仍然讓下次自動整理讀它")
                    }
                    Button("略過，不整理") {
                        Task { await change(keys: [detail.key], requeue: false) }
                    }
                    .buttonStyle(OutlineButtonStyle())
                    .help("從等待名單拿掉，自動整理不會再讀它")
                } else {
                    Button("再整理一次") {
                        Task { await change(keys: [detail.key], requeue: true) }
                    }
                    .buttonStyle(OutlineButtonStyle(accent: true))
                    .disabled(!blocked.isEmpty)
                }
            }
            .disabled(working)
            Text(queueLine(waiting: waiting, blocked: !blocked.isEmpty, expires: expires, detail))
                .font(.system(size: 12))
                .foregroundColor(Theme.secondary)
            ForEach(blocked, id: \.self) { why in
                Text("不會整理：\(Lexicon.blocked(why))。")
                    .font(.system(size: 12, weight: .medium))
                    .foregroundColor(Theme.amberText)
            }
        }
    }

    private func queueLine(waiting: Bool, blocked: Bool, expires: Bool, _ detail: SessionDetail)
        -> String
    {
        if waiting {
            if blocked { return "在等待名單裡，但自動整理會跳過它。" }
            if expires { return "等太久了，自動整理會跳過它，除非按「仍要整理」。" }
            if detail.curation?.byHand == true { return "已由你送回：下次自動整理一定會讀它。" }
            return "下次自動整理會讀它。"
        }
        return blocked ? "不在等待名單裡。" : "不在等待名單裡。按「再整理一次」會讓下次自動整理重讀它。"
    }

    private var batchView: some View {
        let keys = Array(selection)
        let waiting = keys.filter { isPending($0) == true }
        let done = keys.filter { isPending($0) == false }
        return VStack(spacing: 14) {
            Text("已選 \(keys.count) 段對話").font(.system(size: 18, weight: .semibold))
            Text("對話已經帶來的記憶都會保留。")
                .font(.system(size: 13))
                .foregroundColor(Theme.secondary)
            HStack(spacing: 8) {
                Button("略過 \(waiting.count) 段") {
                    Task { await change(keys: waiting, requeue: false) }
                }
                .buttonStyle(OutlineButtonStyle())
                .disabled(waiting.isEmpty || working)
                Button("再整理 \(done.count) 段") {
                    Task { await change(keys: done, requeue: true) }
                }
                .buttonStyle(OutlineButtonStyle(accent: true))
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
        if app.sessionsPendingOnly {
            app.sessionsPendingOnly = false
            pendingOnly = true
        }
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
        // A later request, or a change made meanwhile, makes this one's answer stale.
        detailRequest += 1
        let request = detailRequest
        guard selection.count == 1, let key = selection.first else {
            detail = nil
            detailError = nil
            return
        }
        do {
            let loaded = try await Backend.current().session(key: key)
            guard request == detailRequest, selection == [key] else { return }
            detail = loaded
            detailError = nil
        } catch {
            guard request == detailRequest, selection == [key] else { return }
            detail = nil
            detailError = error.localizedDescription
        }
    }

    @MainActor
    private func change(keys: [String], requeue: Bool) async {
        guard !working, !keys.isEmpty else { return }
        working = true
        detailRequest += 1  // what is being loaded now predates this change
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
        outcome = ActionNote(result, verb: requeue ? "已排入整理" : "已略過", unit: "段")
        await loadSessions(wait: false)
        // What is selected now, which need not be what was changed.
        await loadDetail()
    }
}

/// Where a session stands: waiting, or already read into a summary.
private struct StateTag: View {
    let pending: Bool?
    let summary: String?

    var body: some View {
        if pending == true {
            Tag(text: "等待整理", tone: .amber)
        } else if let summary, !summary.isEmpty {
            Tag(text: "已有摘要", tone: .accent)
        }
    }
}
