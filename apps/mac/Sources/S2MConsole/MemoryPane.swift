import AppKit
import SwiftUI

struct MemoryPane: View {
    @EnvironmentObject private var app: AppState
    @State private var projects: [Project] = []
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
            PageHeader("記憶", subtitle: "代理人開新對話時，會先讀到這個專案的記憶。") {
                SearchField(text: $query, prompt: "搜尋記憶")
                Button {
                    if let project = selectedProject { startAdd(project: project) }
                } label: {
                    Label("新增記憶", systemImage: "plus")
                }
                .buttonStyle(PrimaryButtonStyle())
                .keyboardShortcut("n", modifiers: [.command, .shift])
                .disabled(selectedProject == nil || working)
                .help("替這個專案手動寫一條記憶（⇧⌘N）")
                Button {
                    Task { await reloadAll() }
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
                detailView.detailPanel()
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
            Group {
                switch item {
                case .status(let ids, let restore): statusSheet(ids: ids, restore: restore)
                case .edit(let id, let text): editSheet(id: id, original: text)
                case .add(let project): addSheet(project: project)
                }
            }
            .foregroundColor(Theme.ink)
            .tint(Theme.accent)
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

    // MARK: filters

    private var listedProjects: [Project] {
        workspacesToList(projects, filter: "", withoutMemory: showEmpty, keeping: selectedProject)
    }

    private var projectName: String {
        projects.first(where: { $0.project == selectedProject })?.name ?? "選擇專案"
    }

    /// The kinds the loaded entries have, with how many of each.
    private var kindCounts: [(kind: String, count: Int)] {
        kinds.compactMap { item in
            let count = memories.filter { $0.kind == item }.count
            return count > 0 || item == kind ? (item, count) : nil
        }
    }

    private var filterBar: some View {
        HStack(spacing: 10) {
            Menu(projectName) {
                ForEach(listedProjects) { project in
                    Button("\(project.name)　\(project.active) 條") {
                        selectedProject = project.project
                    }
                }
                Divider()
                Toggle("也列出還沒有記憶的專案", isOn: $showEmpty)
            }
            .fixedSize()
            .help(selectedProject ?? "選一個專案")
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 6) {
                    Chip(title: "全部 \(memories.count)", selected: kind.isEmpty) { kind = "" }
                    ForEach(kindCounts, id: \.kind) { item in
                        Chip(
                            title: "\(Lexicon.kind(item.kind)) \(item.count)",
                            selected: kind == item.kind
                        ) { kind = item.kind }
                    }
                }
            }
            if searching {
                Toggle("搜尋所有專案", isOn: $everywhere)
                    .toggleStyle(.checkbox)
                    .fixedSize()
            }
            Text(countLine).font(.system(size: 12)).foregroundColor(Theme.secondary).fixedSize()
            Picker("狀態", selection: $status) {
                ForEach(statuses, id: \.self) { item in
                    Text(Lexicon.status(item)).tag(item)
                }
            }
            .labelsHidden()
            .fixedSize()
            .help("要看哪種狀態的記憶")
        }
        .padding(.horizontal, 32)
        .padding(.bottom, 14)
    }

    private var countLine: String {
        var line = "\(shown.count) 條"
        if searching && memories.count >= Backend.searchLimit {
            line = "只顯示前 \(shown.count) 條，多打幾個字縮小範圍"
        }
        if selection.count > 1 { line += " · 已選 \(selection.count) 條" }
        return line
    }

    // MARK: list

    private var listColumn: some View {
        Group {
            if selectedProject == nil && !(searching && everywhere) {
                Placeholder(text: "先選一個專案")
            } else if shown.isEmpty {
                Placeholder(text: loading ? "讀取中…" : emptyText)
            } else {
                ScrollViewReader { proxy in
                    List(shown, selection: $selection) { memory in
                        row(memory)
                            .listCell(gap: 5)
                            .tag(memory.id)
                    }
                    .listStyle(.plain)
                    .scrollContentBackground(.hidden)
                    .contextMenu(forSelectionType: String.self) { ids in
                        rowMenu(Array(ids))
                    }
                    .onDeleteCommand {
                        ask(ids: retirable(Array(selection)), restore: false)
                    }
                    .onAppear { scroll(proxy) }
                    .onChange(of: memories) { _ in scroll(proxy) }
                }
                .padding(.horizontal, 16)
                .padding(.top, 8)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private func scroll(_ proxy: ScrollViewProxy) {
        guard let id = reveal, shown.contains(where: { $0.id == id }) else { return }
        reveal = nil
        proxy.scrollTo(id, anchor: .center)
    }

    private var emptyText: String {
        if searching { return "找不到符合的記憶" }
        return kind.isEmpty ? "這個狀態下沒有記憶" : "這個狀態下沒有「\(Lexicon.kind(kind))」"
    }

    private func row(_ memory: Memory) -> some View {
        let chosen = selection.contains(memory.id)
        return VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                Text(Lexicon.kind(memory.kind))
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundColor(chosen ? Theme.accent : Theme.ink)
                if status == "all" {
                    StatusBadge(status: memory.status)
                }
                if searching && everywhere {
                    Text((memory.project as NSString).lastPathComponent)
                        .font(.system(size: 12))
                        .foregroundColor(Theme.accent)
                }
                Spacer()
                Text(localTime(memory.lastConfirmedAt))
                    .font(.system(size: 12))
                    .foregroundColor(Theme.secondary)
            }
            Text(memory.text)
                .font(.system(size: 15))
                .lineSpacing(3)
                .lineLimit(3)
                .foregroundColor(Theme.ink)
        }
        .card(selected: chosen)
    }

    @ViewBuilder
    private func rowMenu(_ ids: [String]) -> some View {
        let toRetire = retirable(ids)
        let toRestore = restorable(ids)
        if !toRetire.isEmpty {
            Button(toRetire.count == 1 ? "停用…" : "停用 \(toRetire.count) 條…") {
                ask(ids: toRetire, restore: false)
            }
            .disabled(working)
        }
        if !toRestore.isEmpty {
            Button(toRestore.count == 1 ? "復原…" : "復原 \(toRestore.count) 條…") {
                ask(ids: toRestore, restore: true)
            }
            .disabled(working)
        }
        if ids.count == 1, toRetire.count == 1, let text = text(of: ids[0]) {
            Button("改寫…") { startEdit(id: ids[0], text: text) }
                .disabled(working)
        }
        if !ids.isEmpty {
            Divider()
            Button("複製文字") { copy(ids) }
        }
    }

    // MARK: detail

    @ViewBuilder
    private var detailView: some View {
        if selection.count > 1 {
            batchView
        } else if let detail, selection.contains(detail.id) {
            ScrollView {
                VStack(alignment: .leading, spacing: 22) {
                    header(detail)
                    links(detail)
                    evidence(detail)
                    history(detail)
                    Text("停用不會刪除，之後隨時可以復原。")
                        .font(.system(size: 12))
                        .foregroundColor(Theme.secondary)
                    EngineeringDetails("詳細資料（給工程用）") {
                        fact("編號", detail.id)
                        fact("專案資料夾", detail.project)
                        fact("範圍", detail.scope)
                        fact("建立", shortTime(detail.createdAt))
                        fact("最後確認", shortTime(detail.lastConfirmedAt))
                    }
                }
                .padding(24)
            }
        } else if let detailError, !selection.isEmpty {
            LoadFailed(message: detailError) { Task { await loadDetail() } }
        } else {
            Placeholder(text: selection.isEmpty ? "選一條記憶，看它的內容與來源" : "讀取中…")
        }
    }

    private func fact(_ name: String, _ value: String) -> some View {
        HStack(alignment: .top, spacing: 8) {
            Text(name).foregroundColor(Theme.secondary).frame(width: 76, alignment: .leading)
            Text(value).textSelection(.enabled)
        }
        .font(.system(size: 12))
    }

    private var batchView: some View {
        let ids = Array(selection)
        let toRetire = retirable(ids)
        let toRestore = restorable(ids)
        return VStack(spacing: 14) {
            Text("已選 \(ids.count) 條記憶").font(.system(size: 18, weight: .semibold))
            Text("每條的修改紀錄都會留下，不會刪除任何東西。")
                .font(.system(size: 13))
                .foregroundColor(Theme.secondary)
                .multilineTextAlignment(.center)
            HStack(spacing: 8) {
                Button("停用 \(toRetire.count) 條…") { ask(ids: toRetire, restore: false) }
                    .buttonStyle(OutlineButtonStyle())
                    .disabled(toRetire.isEmpty || working)
                Button("復原 \(toRestore.count) 條…") { ask(ids: toRestore, restore: true) }
                    .buttonStyle(OutlineButtonStyle())
                    .disabled(toRestore.isEmpty || working)
            }
            Button("複製文字") { copy(ids) }.buttonStyle(OutlineButtonStyle())
            if working { ProgressView().controlSize(.small) }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(20)
    }

    private func header(_ detail: MemoryDetail) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 8) {
                Tag(text: Lexicon.kind(detail.kind), tone: .accent)
                Text(Lexicon.status(detail.status))
                    .font(.system(size: 12))
                    .foregroundColor(Theme.secondary)
                Spacer()
                if working { ProgressView().controlSize(.small) }
            }
            Text(detail.text)
                .font(.system(size: 18, weight: .medium))
                .lineSpacing(5)
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
            HStack(spacing: 8) {
                if detail.status == "active" {
                    Button("改寫") { startEdit(id: detail.id, text: detail.text) }
                        .buttonStyle(OutlineButtonStyle(accent: true))
                        .keyboardShortcut("e", modifiers: .command)
                        .help("換個說法（⌘E）")
                    Button("停用") { ask(ids: [detail.id], restore: false) }
                        .buttonStyle(OutlineButtonStyle())
                } else if detail.restorable {
                    Button("復原") { ask(ids: [detail.id], restore: true) }
                        .buttonStyle(OutlineButtonStyle(accent: true))
                }
            }
            .disabled(working)
        }
    }

    @ViewBuilder
    private func links(_ detail: MemoryDetail) -> some View {
        if detail.supersedesId != nil || !detail.replacedBy.isEmpty {
            Block("相關的說法") {
                if let older = detail.supersedesId {
                    linkButton("看改寫前的說法") { open(older) }
                }
                ForEach(detail.replacedBy, id: \.self) { newer in
                    linkButton("已被改寫，看新的說法") { open(newer) }
                }
            }
        }
    }

    private func linkButton(_ title: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(title)
                .font(.system(size: 14, weight: .semibold))
                .foregroundColor(Theme.accent)
                .multilineTextAlignment(.leading)
        }
        .buttonStyle(.plain)
    }

    private func evidence(_ detail: MemoryDetail) -> some View {
        Block("從哪段對話學到的") {
            if detail.evidenceSessions.isEmpty {
                Text("沒有對話紀錄，是手動加入的。")
                    .font(.system(size: 13))
                    .foregroundColor(Theme.secondary)
            }
            ForEach(detail.evidenceSessions) { session in
                VStack(alignment: .leading, spacing: 5) {
                    linkButton(session.title ?? "尚未命名的對話") {
                        app.show(session: session.key)
                    }
                    .help("到「對話」看這段對話")
                    Text("\(Lexicon.tool(session.tool)) · \(localTime(session.lastActivity))")
                        .font(.system(size: 12))
                        .foregroundColor(Theme.secondary)
                    if let summary = session.summary, !summary.isEmpty {
                        Text(summary)
                            .font(.system(size: 13))
                            .lineSpacing(3)
                            .lineLimit(3)
                    }
                }
                .card(padding: 12)
            }
        }
    }

    private func history(_ detail: MemoryDetail) -> some View {
        Block("修改紀錄") {
            ForEach(Array(detail.events.enumerated()), id: \.offset) { _, event in
                HStack(alignment: .top, spacing: 10) {
                    Circle().fill(Theme.accent).frame(width: 8, height: 8).padding(.top, 5)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(Lexicon.event(event.op)).font(.system(size: 14))
                        if let why = event.detail["reason"]?.stringValue, !why.isEmpty {
                            Text(why).font(.system(size: 13)).foregroundColor(Theme.secondary)
                        }
                    }
                    Spacer()
                    Text(localTime(event.at))
                        .font(.system(size: 12))
                        .foregroundColor(Theme.secondary)
                }
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

    private func sheetTitle(_ title: String, _ note: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title).font(.system(size: 18, weight: .bold))
            Text(note).font(.system(size: 13)).foregroundColor(Theme.secondary)
        }
    }

    private func statusSheet(ids: [String], restore: Bool) -> some View {
        let many = ids.count > 1
        return VStack(alignment: .leading, spacing: 14) {
            sheetTitle(
                restore
                    ? (many ? "復原 \(ids.count) 條記憶" : "復原這條記憶")
                    : (many ? "停用 \(ids.count) 條記憶" : "停用這條記憶"),
                restore
                    ? "會重新使用，新的對話也會讀到。"
                    : "會保留下來，但不再被搜尋、匯出，也不會給新的對話。")
            QuickReasons(
                choices: restore
                    ? ["仍然成立", "誤停用"]
                    : ["已經過時", "內容有誤", "重複了", "太瑣碎"],
                reason: $reason)
            TextField("原因（會記在修改紀錄裡）", text: $reason)
                .textFieldStyle(.roundedBorder)
                .frame(width: 420)
            HStack(spacing: 8) {
                Spacer()
                Button("取消") { sheet = nil }
                    .buttonStyle(OutlineButtonStyle())
                    .keyboardShortcut(.cancelAction)
                Button(restore ? "復原" : "停用") {
                    let why = trimmedReason
                    sheet = nil
                    Task { await change(ids: ids, restore: restore, reason: why) }
                }
                .buttonStyle(PrimaryButtonStyle())
                .keyboardShortcut(.defaultAction)
                .disabled(trimmedReason.isEmpty)
            }
        }
        .padding(24)
    }

    private func editSheet(id: String, original: String) -> some View {
        VStack(alignment: .leading, spacing: 14) {
            sheetTitle("改寫這條記憶", "原本的說法會保留為「已改寫」，並連到新的說法。")
            draftEditor(height: 130)
            QuickReasons(choices: ["寫得更清楚", "修正細節", "內容過時"], reason: $reason)
            TextField("原因（會記在修改紀錄裡）", text: $reason)
                .textFieldStyle(.roundedBorder)
                .frame(width: 480)
            HStack(spacing: 8) {
                Spacer()
                Button("取消") { sheet = nil }
                    .buttonStyle(OutlineButtonStyle())
                    .keyboardShortcut(.cancelAction)
                Button("儲存") {
                    let text = tidyDraft
                    let why = trimmedReason
                    sheet = nil
                    Task { await saveEdit(id: id, text: text, reason: why) }
                }
                .buttonStyle(PrimaryButtonStyle())
                .keyboardShortcut(.defaultAction)
                .disabled(trimmedReason.isEmpty || !draftFits || tidyDraft == tidyText(original))
            }
        }
        .padding(24)
    }

    private func addSheet(project: String) -> some View {
        VStack(alignment: .leading, spacing: 14) {
            sheetTitle(
                "新增記憶", "給 \((project as NSString).lastPathComponent)。這條記憶背後沒有對話紀錄。")
            Picker("種類", selection: $draftKind) {
                ForEach(kinds, id: \.self) { kind in
                    Text(Lexicon.kind(kind)).tag(kind)
                }
            }
            .frame(width: 200)
            draftEditor(height: 110)
            QuickReasons(choices: ["團隊規則", "在對話之外決定的"], reason: $reason)
            TextField("原因（會記在修改紀錄裡）", text: $reason)
                .textFieldStyle(.roundedBorder)
                .frame(width: 480)
            HStack(spacing: 8) {
                Spacer()
                Button("取消") { sheet = nil }
                    .buttonStyle(OutlineButtonStyle())
                    .keyboardShortcut(.cancelAction)
                Button("加入") {
                    let text = tidyDraft
                    let why = trimmedReason
                    let kind = draftKind
                    sheet = nil
                    Task { await saveNew(project: project, kind: kind, text: text, reason: why) }
                }
                .buttonStyle(PrimaryButtonStyle())
                .keyboardShortcut(.defaultAction)
                .disabled(trimmedReason.isEmpty || !draftFits)
            }
        }
        .padding(24)
    }

    private func draftEditor(height: CGFloat) -> some View {
        VStack(alignment: .trailing, spacing: 4) {
            TextEditor(text: $draft)
                .font(.system(size: 14))
                .frame(width: 480, height: height)
                .overlay(RoundedRectangle(cornerRadius: 8).stroke(Theme.control))
            HStack {
                Text("只會存成一行：換行會變成空白。")
                    .font(.system(size: 12))
                    .foregroundColor(Theme.secondary)
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
        kind = ""
        status = "all"
        keepSelection = target.project != selectedProject
        selectedProject = target.project
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
        outcome = ActionNote(result, verb: restore ? "已復原" : "已停用", unit: "條")
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
            outcome = ActionNote(text: "已儲存。原本的說法保留為「已改寫」。")
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
            outcome = ActionNote(text: "已加入。")
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
