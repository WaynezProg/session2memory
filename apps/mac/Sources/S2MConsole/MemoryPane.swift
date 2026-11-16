import AppKit
import SwiftUI

struct MemoryPane: View {
    @EnvironmentObject private var app: AppState
    /// Coding memory under its subjects, or OpenClaw memory under its agents.
    @State private var lens: MemoryLens = .topics
    /// Every topic of this view, also the ones nothing is filed under.
    @State private var topics: [Topic] = []
    @State private var status = "active"
    @State private var kind = ""
    @State private var topic = ""
    @State private var groupByTopic = true
    @State private var query = ""
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
    /// The topic an entry is written under or moved to; a topic's new name; what it holds.
    @State private var draftTopic = ""
    @State private var draftName = ""
    @State private var draftAbout = ""
    /// The view is about to change because an entry of the other one was asked for:
    /// that entry stays selected.
    @State private var keepSelection = 0
    /// An entry to scroll to once the list holds it.
    @State private var reveal: String?

    private enum Sheet: Identifiable {
        case status(ids: [String], restore: Bool)
        case edit(id: String, text: String)
        case add
        case move(ids: [String])
        case topic(Topic)
        case newTopic

        var id: String {
            switch self {
            case .status(let ids, let restore): return "status|\(restore)|\(ids.joined(separator: ","))"
            case .edit(let id, _): return "edit|\(id)"
            case .add: return "add"
            case .move(let ids): return "move|\(ids.joined(separator: ","))"
            case .topic(let topic): return "topic|\(topic.id)"
            case .newTopic: return "newTopic"
            }
        }
    }

    private let statuses = ["active", "superseded", "retired", "stale", "all"]
    private let kinds = ["decision", "constraint", "pitfall", "fact", "preference", "procedure"]

    var body: some View {
        VStack(spacing: 0) {
            PageHeader("記憶", subtitle: "記憶歸在主題下，不看當時開的是哪個資料夾；OpenClaw 的記憶歸給各自的代理人。") {
                SearchField(text: $query, prompt: "搜尋記憶")
                Button("概覽") { selection = [] }
                    .buttonStyle(OutlineButtonStyle())
                    .disabled(selection.isEmpty)
                Button {
                    startAdd()
                } label: {
                    Label("新增記憶", systemImage: "plus")
                }
                .buttonStyle(PrimaryButtonStyle())
                .keyboardShortcut("n", modifiers: [.command, .shift])
                .disabled(topics.isEmpty || working)
                .help("手動寫一條記憶到某個\(lens.noun)下（⇧⌘N）")
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
        .task(id: listKey) { await loadMemories(wait: searching) }
        .task(id: selection) { await loadDetail() }
        .onAppear { takeTarget() }
        .onChange(of: app.memoryTarget) { _ in takeTarget() }
        .onChange(of: lens) { _ in
            topic = ""
            // What is listed belongs to the other view until the new list arrives.
            memories = []
            if keepSelection > 0 { keepSelection -= 1 } else { selection = [] }
        }
        .sheet(item: $sheet) { item in
            Group {
                switch item {
                case .status(let ids, let restore): statusSheet(ids: ids, restore: restore)
                case .edit(let id, let text): editSheet(id: id, original: text)
                case .add: addSheet()
                case .move(let ids): moveSheet(ids: ids)
                case .topic(let topic): topicSheet(topic)
                case .newTopic: newTopicSheet()
                }
            }
            .foregroundColor(Theme.ink)
            .tint(Theme.accent)
        }
    }

    private var listKey: String {
        [lens.rawValue, status, query].joined(separator: "|")
    }

    private var searching: Bool {
        !query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    /// The loaded entries of the chosen kind.
    private var shown: [Memory] {
        MemoryOrganization.filter(memories, kind: kind, topic: topic, by: lens)
    }

    // MARK: filters

    /// The kinds the loaded entries have, with how many of each.
    private var kindCounts: [(kind: String, count: Int)] {
        kinds.compactMap { item in
            let count = memories.filter { $0.kind == item }.count
            return count > 0 || item == kind ? (item, count) : nil
        }
    }

    private var filterBar: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 12) {
                Picker("檢視", selection: $lens) {
                    ForEach(MemoryLens.allCases) { item in
                        Text(item.title).tag(item)
                    }
                }
                .pickerStyle(.segmented)
                .labelsHidden()
                .fixedSize()
                .help("開發記憶歸在主題下；OpenClaw 的記憶歸給各自的代理人")
                Spacer()
                Picker("排列", selection: $groupByTopic) {
                    Text("依\(lens.noun)分組").tag(true)
                    Text("依確認時間").tag(false)
                }.pickerStyle(.segmented).labelsHidden().fixedSize()
                Picker(lens.noun, selection: Binding(get: { topic }, set: { topic = $0; selection = [] })) {
                    Text("全部\(lens.noun)").tag("")
                    ForEach(MemoryOrganization.groups(memories, by: lens)) { group in
                        Text("\(group.topic.label)（\(group.memories.count)）").tag(group.id)
                    }
                }.labelsHidden().frame(width: 210)
            }
            HStack(spacing: 10) {
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
            if shown.isEmpty {
                Placeholder(text: loading ? "讀取中…" : emptyText)
            } else {
                ScrollViewReader { proxy in
                    List(selection: $selection) {
                        if groupByTopic {
                            ForEach(MemoryOrganization.groups(shown, by: lens)) { group in
                                Section {
                                    ForEach(group.memories) { memory in
                                        row(memory).listCell(gap: 5).tag(memory.id)
                                    }
                                } header: {
                                    Text("\(group.topic.label) · \(group.memories.count) 條")
                                        .font(.system(size: 12, weight: .semibold))
                                }
                            }
                        } else {
                            ForEach(shown) { memory in
                                row(memory).listCell(gap: 5).tag(memory.id)
                            }
                        }
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
                if !groupByTopic {
                    Text(lens.label(of: memory).label)
                        .font(.system(size: 12))
                        .foregroundColor(Theme.accent)
                        .lineLimit(1)
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
            Button(ids.count == 1 ? "搬到別的\(lens.noun)…" : "把 \(ids.count) 條搬到別的\(lens.noun)…") {
                startMove(ids: ids)
            }
            .disabled(working || topics.count < 2)
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
                        fact("學到的資料夾", detail.project)
                        fact("範圍", detail.scope)
                        fact("建立", shortTime(detail.createdAt))
                        fact("最後確認", shortTime(detail.lastConfirmedAt))
                    }
                }
                .padding(24)
            }
        } else if let detailError, !selection.isEmpty {
            LoadFailed(message: detailError) { Task { await loadDetail() } }
        } else if selection.isEmpty {
            MemoryProjectOverview(
                lens: lens, memories: memories, topics: topics, searching: searching,
                status: status, selectedTopic: topic,
                chooseTopic: { chosen in
                    topic = chosen
                    selection = []
                },
                manageTopic: { startTopic($0) },
                addTopic: { startNewTopic() })
        } else {
            Placeholder(text: "讀取中…")
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
            Button("搬到別的\(lens.noun)…") { startMove(ids: ids) }
                .buttonStyle(OutlineButtonStyle())
                .disabled(working || topics.count < 2)
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
                Text((detail.topic ?? .general).label)
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundColor(Theme.accent)
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
                    Button("搬到別的\(lens.noun)") { startMove(ids: [detail.id]) }
                        .buttonStyle(OutlineButtonStyle())
                        .disabled(topics.count < 2)
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
                            Text(Lexicon.reason(why))
                                .font(.system(size: 13)).foregroundColor(Theme.secondary)
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

    private func topicPicker(_ title: String, without: Set<String> = []) -> some View {
        Picker(title, selection: $draftTopic) {
            ForEach(topics.filter { !without.contains($0.id) }) { topic in
                Text(topic.label).tag(topic.id)
            }
        }
        .frame(width: 320)
    }

    private func addSheet() -> some View {
        VStack(alignment: .leading, spacing: 14) {
            sheetTitle("新增記憶", "寫到一個\(lens.noun)下。這條記憶背後沒有對話紀錄。")
            topicPicker(lens.noun)
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
                    let target = draftTopic
                    sheet = nil
                    Task { await saveNew(topic: target, kind: kind, text: text, reason: why) }
                }
                .buttonStyle(PrimaryButtonStyle())
                .keyboardShortcut(.defaultAction)
                .disabled(trimmedReason.isEmpty || !draftFits || draftTopic.isEmpty)
            }
        }
        .padding(24)
    }

    private func moveSheet(ids: [String]) -> some View {
        VStack(alignment: .leading, spacing: 14) {
            sheetTitle(
                ids.count > 1 ? "把 \(ids.count) 條記憶搬到別的\(lens.noun)" : "搬到別的\(lens.noun)",
                "只改歸在哪裡；內容、來源與狀態都不變。")
            topicPicker("搬到")
            QuickReasons(choices: ["歸錯了", "這裡更合適"], reason: $reason)
            TextField("原因（會記在修改紀錄裡）", text: $reason)
                .textFieldStyle(.roundedBorder)
                .frame(width: 420)
            HStack(spacing: 8) {
                Spacer()
                Button("取消") { sheet = nil }
                    .buttonStyle(OutlineButtonStyle())
                    .keyboardShortcut(.cancelAction)
                Button("搬移") {
                    let why = trimmedReason
                    let target = draftTopic
                    sheet = nil
                    Task { await move(ids: ids, topic: target, reason: why) }
                }
                .buttonStyle(PrimaryButtonStyle())
                .keyboardShortcut(.defaultAction)
                .disabled(trimmedReason.isEmpty || draftTopic.isEmpty)
            }
        }
        .padding(24)
    }

    private var tidyName: String {
        draftName.split(whereSeparator: \.isWhitespace).joined(separator: " ")
    }

    private func topicFields() -> some View {
        VStack(alignment: .leading, spacing: 8) {
            TextField("名稱", text: $draftName).textFieldStyle(.roundedBorder).frame(width: 420)
            TextField("這個主題收什麼（給整理對話時參考）", text: $draftAbout)
                .textFieldStyle(.roundedBorder).frame(width: 420)
        }
    }

    private func topicSheet(_ topic: Topic) -> some View {
        VStack(alignment: .leading, spacing: 14) {
            sheetTitle("整理主題「\(topic.name)」", "改名或補說明不會動到記憶。")
            topicFields()
            HStack(spacing: 8) {
                Spacer()
                Button("儲存") {
                    let name = tidyName
                    let about = draftAbout
                    sheet = nil
                    Task { await saveTopic(topic, name: name, about: about) }
                }
                .buttonStyle(PrimaryButtonStyle())
                .disabled(
                    tidyName.isEmpty
                        || (tidyName == topic.name && draftAbout == (topic.description ?? "")))
            }
            if topics.count > 1 {
                Divider()
                Text("併到另一個主題").font(.system(size: 14, weight: .semibold))
                Text("這個主題下的 \(topic.active) 條記憶都會搬過去，這個主題就不再存在。")
                    .font(.system(size: 12)).foregroundColor(Theme.secondary)
                topicPicker("併到", without: [topic.id])
                TextField("原因（會記在每條記憶的修改紀錄裡）", text: $reason)
                    .textFieldStyle(.roundedBorder)
                    .frame(width: 420)
                HStack(spacing: 8) {
                    Spacer()
                    Button("合併") {
                        let why = trimmedReason
                        let target = draftTopic
                        sheet = nil
                        Task { await mergeTopic(topic, into: target, reason: why) }
                    }
                    .buttonStyle(OutlineButtonStyle())
                    .disabled(
                        trimmedReason.isEmpty || draftTopic.isEmpty || draftTopic == topic.id)
                }
            }
            HStack {
                Spacer()
                Button("關閉") { sheet = nil }
                    .buttonStyle(OutlineButtonStyle())
                    .keyboardShortcut(.cancelAction)
            }
        }
        .padding(24)
    }

    private func newTopicSheet() -> some View {
        VStack(alignment: .leading, spacing: 14) {
            sheetTitle("新增主題", "之後整理對話時，符合的記憶會歸到這裡。")
            topicFields()
            HStack(spacing: 8) {
                Spacer()
                Button("取消") { sheet = nil }
                    .buttonStyle(OutlineButtonStyle())
                    .keyboardShortcut(.cancelAction)
                Button("新增") {
                    let name = tidyName
                    let about = draftAbout
                    sheet = nil
                    Task { await saveNewTopic(name: name, about: about) }
                }
                .buttonStyle(PrimaryButtonStyle())
                .keyboardShortcut(.defaultAction)
                .disabled(tidyName.isEmpty)
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

    /// The topic a sheet starts on: the one being looked at, else the first.
    private func startingTopic(not excluded: String = "") -> String {
        let choices = topics.map(\.id).filter { $0 != excluded }
        return choices.contains(topic) ? topic : choices.first ?? ""
    }

    private func startAdd() {
        guard !working, !topics.isEmpty else { return }
        reason = ""
        draft = ""
        draftKind = kind.isEmpty ? "fact" : kind
        draftTopic = startingTopic()
        sheet = .add
    }

    private func startMove(ids: [String]) {
        guard !working, !ids.isEmpty else { return }
        reason = ""
        let current = Set(memories.filter { ids.contains($0.id) }.map { lens.label(of: $0).id })
        draftTopic = startingTopic(not: current.count == 1 ? current.first ?? "" : "")
        sheet = .move(ids: ids)
    }

    private func startTopic(_ topic: Topic) {
        guard !working else { return }
        reason = ""
        draftName = topic.name
        draftAbout = topic.description ?? ""
        draftTopic = topics.first { $0.id != topic.id }?.id ?? ""
        sheet = .topic(topic)
    }

    private func startNewTopic() {
        guard !working else { return }
        draftName = ""
        draftAbout = ""
        sheet = .newTopic
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
        topic = ""
        reveal = id
        selection = [id]
    }

    private func takeTarget() {
        guard let target = app.memoryTarget else { return }
        app.memoryTarget = nil
        query = ""
        kind = ""
        topic = ""
        status = "all"
        reveal = target.id
        selection = [target.id]
    }

    // MARK: loading and changing

    @MainActor
    private func reloadAll() async {
        await loadMemories(wait: false)
        await loadDetail()
    }

    @MainActor
    private func loadMemories(wait: Bool) async {
        if wait {
            // Typing: wait for a pause before asking.
            try? await Task.sleep(nanoseconds: 300_000_000)
            if Task.isCancelled { return }
        }
        let words = query.trimmingCharacters(in: .whitespacesAndNewlines)
        let scope = lens.scope
        loading = true
        defer { loading = false }
        do {
            let backend = Backend.current()
            let found: [Memory]
            if words.isEmpty {
                found = try await backend.memories(scope: scope, status: status)
            } else {
                found = try await backend.search(
                    query: words, project: nil, status: status, scope: scope)
            }
            // An older command line has no topics: the page still lists and groups.
            let listed = (try? await backend.topics(scope: scope)) ?? []
            if Task.isCancelled { return }
            memories = found
            topics = listed.sorted {
                $0.label.localizedStandardCompare($1.label) == .orderedAscending
            }
            if !topic.isEmpty && !found.contains(where: { lens.label(of: $0).id == topic }) {
                topic = ""
            }
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
            // Asked for from another page: show the view the entry belongs to.
            let wanted: MemoryLens = loaded.scope == "openclaw" ? .agents : .topics
            if wanted != lens {
                keepSelection += 1
                lens = wanted
            }
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
        // What is selected now, which need not be what was changed.
        await loadDetail()
    }

    @MainActor
    private func move(ids: [String], topic target: String, reason: String) async {
        guard !working, !ids.isEmpty, !target.isEmpty else { return }
        working = true
        detailRequest += 1
        defer { working = false }
        outcome = nil
        let backend = Backend.current()
        var result = BatchOutcome()
        for id in ids {
            do {
                _ = try await backend.move(id: id, topic: target, reason: reason)
                result.done += 1
            } catch {
                result.failures.append(error.localizedDescription)
            }
        }
        outcome = ActionNote(result, verb: "已搬移", unit: "條")
        await loadMemories(wait: false)
        await loadDetail()
    }

    @MainActor
    private func change(topic work: @escaping (Backend) async throws -> String) async {
        guard !working else { return }
        working = true
        defer { working = false }
        outcome = nil
        do {
            outcome = ActionNote(text: try await work(Backend.current()))
        } catch {
            outcome = ActionNote(text: error.localizedDescription, failed: true)
        }
        await loadMemories(wait: false)
        await loadDetail()
    }

    @MainActor
    private func saveTopic(_ topic: Topic, name: String, about: String) async {
        await change { backend in
            _ = try await backend.editTopic(id: topic.id, name: name, description: about)
            return "已儲存主題「\(name)」。"
        }
    }

    @MainActor
    private func mergeTopic(_ topic: Topic, into target: String, reason: String) async {
        if self.topic == topic.id { self.topic = "" }
        await change { backend in
            let kept = try await backend.mergeTopic(id: topic.id, into: target, reason: reason)
            return "已把「\(topic.name)」併到「\(kept.name)」。"
        }
    }

    @MainActor
    private func saveNewTopic(name: String, about: String) async {
        await change { backend in
            let made = try await backend.addTopic(name: name, description: about, scope: "coding")
            return "已新增主題「\(made.name)」。"
        }
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
    private func saveNew(topic target: String, kind: String, text: String, reason: String) async {
        guard !working else { return }
        working = true
        defer { working = false }
        outcome = nil
        do {
            let added = try await Backend.current().add(
                topic: target, kind: kind, text: text, reason: reason)
            outcome = ActionNote(text: "已加入。")
            // Make sure the new entry is in the list that is reloaded.
            if status != "active" && status != "all" { status = "active" }
            if !self.kind.isEmpty && self.kind != kind { self.kind = "" }
            // Also while searching: the new entry may match what is being looked for.
            if !topic.isEmpty && topic != target { topic = "" }
            await loadMemories(wait: false)
            reveal = added.id
            selection = [added.id]
        } catch {
            outcome = ActionNote(text: error.localizedDescription, failed: true)
        }
    }
}
