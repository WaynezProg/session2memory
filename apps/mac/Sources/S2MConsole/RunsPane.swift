import SwiftUI

/// What one unattended run did, in words. Reads the run's report by step and field.
struct RunStory {
    let run: RunRecord

    func number(_ step: String, _ field: String) -> Int? {
        run.report[step]?[field]?.numberValue.map { Int($0) }
    }

    var dryRun: Bool { run.report["dry_run"] == .bool(true) }

    var hasError: Bool {
        run.report.members.contains { $0.value["error"] != nil }
    }

    /// How often the model was asked, over the steps that ask it.
    var modelCalls: Int {
        (number("curate", "llm_calls") ?? 0) + (number("reconcile", "llm_calls") ?? 0)
    }

    var modelFailures: Int {
        (number("curate", "llm_failures") ?? 0) + (number("reconcile", "llm_failures") ?? 0)
    }

    var cost: Double {
        (run.report["curate"]?["cost_usd"]?.numberValue ?? 0)
            + (run.report["reconcile"]?["cost_usd"]?.numberValue ?? 0)
    }

    /// Seconds spent, summed over the steps that report it.
    var seconds: Int {
        Int(
            run.report.members.reduce(0.0) { sum, member in
                sum + (member.value["seconds"]?.numberValue ?? 0)
            }.rounded())
    }

    /// One line for the list.
    var headline: String {
        let text: String
        if let found = number("scan", "sessions_new") {
            if let added = number("curate", "memories_added"), added > 0 {
                text = "讀了 \(grouped(found)) 段新對話，新增 \(grouped(added)) 條記憶"
            } else {
                text = "讀了 \(grouped(found)) 段新對話"
            }
        } else if run.report["reconcile"] != nil {
            text = "檢查重複與矛盾的記憶"
        } else if run.report["curate"] != nil {
            text = "把對話整理成記憶"
        } else if run.report["embed"] != nil {
            text = "更新搜尋用的索引"
        } else if run.report["export"] != nil {
            text = "寫出記憶給代理人讀"
        } else {
            text = "整理 \(run.runId)"
        }
        return dryRun ? "試跑（不寫入）：" + text : text
    }

    /// What each step came to, one sentence per step that did something.
    var sentences: [String] {
        var lines: [String] = []
        if let found = number("scan", "sessions_new") {
            let updated = number("scan", "sessions_updated") ?? 0
            lines.append("讀到 \(grouped(found)) 段新對話，另有 \(grouped(updated)) 段有更新。")
        }
        if run.report["curate"] != nil {
            var line =
                "整理了 \(grouped(number("curate", "sessions_curated") ?? 0)) 段對話："
                + "新增 \(number("curate", "memories_added") ?? 0) 條記憶、"
                + "再次確認 \(number("curate", "memories_confirmed") ?? 0) 條、"
                + "改寫 \(number("curate", "memories_superseded") ?? 0) 條、"
                + "停用 \(number("curate", "memories_retired") ?? 0) 條。"
            if let left = number("curate", "sessions_left"), left > 0 {
                line += "還有 \(grouped(left)) 段排在後面。"
            }
            lines.append(line)
        }
        if run.report["reconcile"] != nil {
            var line =
                "檢查了 \(number("reconcile", "projects_checked") ?? 0) 個專案的記憶："
                + "合併 \(number("reconcile", "memories_merged") ?? 0) 條、"
                + "停用 \(number("reconcile", "memories_retired") ?? 0) 條。"
            if let left = number("reconcile", "projects_left"), left > 0 {
                line += "還有 \(left) 個專案排在後面。"
            }
            lines.append(line)
        }
        if let done = number("archive", "done"), done > 0 {
            lines.append("歸檔了 \(grouped(done)) 個舊的對話檔。")
        }
        if let embedded = number("embed", "embedded"), embedded > 0 {
            lines.append("更新了 \(grouped(embedded)) 條記憶的搜尋索引。")
        }
        if let written = number("export", "files_written"), written > 0 {
            lines.append("寫出 \(grouped(written)) 個檔案給代理人讀。")
        }
        return lines
    }
}

struct RunsPane: View {
    @EnvironmentObject private var app: AppState
    @State private var overview: Overview?
    @State private var runs: [RunRecord] = []
    @State private var selected: String?
    @State private var message: String?
    @State private var loading = false

    /// Steps in the order the run executes them; anything else follows alphabetically.
    private let stepOrder = [
        "scan", "compress", "archive", "curate", "reconcile", "embed", "export", "index",
    ]
    private let nonSteps: Set<String> = [
        "started_at", "finished_at", "dry_run", "run_id", "totals", "skipped",
    ]

    var body: some View {
        VStack(spacing: 0) {
            PageHeader("整理紀錄", subtitle: "每次自動整理讀了多少對話、寫了多少記憶。") {
                Button {
                    Task { await load() }
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
            if let overview {
                counts(overview.counts)
            }
            Rectangle().fill(Theme.hairline).frame(height: 1)
            HStack(spacing: 0) {
                runList
                Rectangle().fill(Theme.hairline).frame(width: 1)
                detailView.detailPanel()
            }
        }
        .task { await load() }
    }

    private func counts(_ counts: [String: Int]) -> some View {
        HStack(alignment: .top, spacing: 24) {
            StatTile(
                value: grouped(counts["memories_active"] ?? 0), label: "條使用中的記憶",
                note:
                    "另有 \(counts["memories_superseded"] ?? 0) 條已改寫、\(counts["memories_retired"] ?? 0) 條已停用",
                color: Theme.accent)
            StatTile(
                value: grouped(counts["sessions"] ?? 0), label: "段對話",
                note: "其中 \(grouped(counts["sessions_with_summary"] ?? 0)) 段已有摘要")
            StatTile(
                value: grouped(counts["sessions_pending"] ?? 0), label: "段還在等待整理",
                note: "每次整理處理一部分", color: Theme.amberText)
        }
        .padding(20)
        .background(Theme.accentSoft)
        .overlay(RoundedRectangle(cornerRadius: 14).stroke(Color(hex: 0xD6E7DF)))
        .cornerRadius(14)
        .padding(.horizontal, 32)
        .padding(.bottom, 16)
    }

    // MARK: list

    private struct Day: Identifiable {
        let id: String
        var runs: [RunRecord]
    }

    /// The runs by the local day they started on, newest first as they come.
    private var days: [Day] {
        var found: [Day] = []
        for run in runs {
            let day = localDay(run.startedAt)
            if found.last?.id == day {
                found[found.count - 1].runs.append(run)
            } else {
                found.append(Day(id: day, runs: [run]))
            }
        }
        return found
    }

    private var runList: some View {
        Group {
            if runs.isEmpty {
                Placeholder(text: loading ? "讀取中…" : "還沒有整理過")
            } else {
                List(selection: $selected) {
                    ForEach(days) { day in
                        Section {
                            ForEach(day.runs) { run in
                                row(run)
                                    .listCell(gap: 2)
                                    .tag(run.runId)
                            }
                        } header: {
                            Text(day.id)
                                .font(.system(size: 13, weight: .semibold))
                                .foregroundColor(Theme.secondary)
                        }
                    }
                }
                .listStyle(.plain)
                .scrollContentBackground(.hidden)
                .padding(.horizontal, 16)
                .padding(.top, 8)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private func row(_ run: RunRecord) -> some View {
        let story = RunStory(run: run)
        let chosen = selected == run.runId
        return HStack(alignment: .firstTextBaseline, spacing: 14) {
            Text(localClock(run.startedAt))
                .font(.system(size: 14, weight: .semibold))
                .frame(width: 46, alignment: .leading)
            Text(story.headline)
                .font(.system(size: 14, weight: chosen ? .semibold : .regular))
                .lineLimit(2)
            Spacer(minLength: 8)
            if run.finishedAt == nil {
                Tag(text: "沒跑完", tone: .amber)
            } else if story.hasError {
                Tag(text: "有錯誤", tone: .amber)
            }
            if story.modelCalls > 0 {
                Text("問模型 \(story.modelCalls) 次")
                    .font(.system(size: 12))
                    .foregroundColor(Theme.secondary)
            }
        }
        .foregroundColor(Theme.ink)
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
        .background(chosen ? Theme.accentSoft : Color.clear)
        .overlay(
            RoundedRectangle(cornerRadius: 12).stroke(chosen ? Theme.accent : Color.clear, lineWidth: 2)
        )
        .cornerRadius(12)
    }

    // MARK: detail

    @ViewBuilder
    private var detailView: some View {
        if let run = runs.first(where: { $0.runId == selected }) {
            let story = RunStory(run: run)
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(
                            "\(localDay(run.startedAt)) \(localClock(run.startedAt)) · 用時 \(story.seconds) 秒"
                        )
                        .font(.system(size: 12))
                        .foregroundColor(Theme.secondary)
                        Text(story.headline)
                            .font(.system(size: 20, weight: .bold))
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    if let skipped = run.report["skipped"]?.stringValue {
                        ErrorBanner(message: "這次沒有執行：\(skipped)")
                    }
                    ForEach(failures(run), id: \.self) { failure in
                        ErrorBanner(message: failure)
                    }
                    ForEach(story.sentences, id: \.self) { line in
                        Text(line)
                            .font(.system(size: 15))
                            .lineSpacing(4)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    LazyVGrid(
                        columns: [GridItem(.flexible(), spacing: 10), GridItem(.flexible())],
                        spacing: 10
                    ) {
                        tile(String(story.number("curate", "memories_added") ?? 0), "條新增的記憶")
                        tile(
                            String(story.modelCalls),
                            story.modelFailures > 0
                                ? "次問模型，\(story.modelFailures) 次失敗" : "次問模型，沒有失敗")
                        tile(String(format: "US$%.4f", story.cost), "模型費用")
                        tile("\(story.seconds) 秒", "用時")
                    }
                    EngineeringDetails("完整數字（給工程用）") {
                        Text("Run \(run.runId)").font(.system(size: 12)).textSelection(.enabled)
                        ForEach(steps(run), id: \.name) { step in
                            stepBlock(step.name, step.value)
                        }
                    }
                }
                .padding(24)
            }
        } else {
            Placeholder(text: "選一次整理，看它做了什麼")
        }
    }

    private func tile(_ value: String, _ label: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(value).font(.system(size: 20, weight: .bold))
            Text(label).font(.system(size: 12)).foregroundColor(Theme.secondary)
        }
        .card(padding: 12)
    }

    /// What went wrong, by step.
    private func failures(_ run: RunRecord) -> [String] {
        run.report.members.compactMap { member in
            member.value["error"]?.stringValue.map { "\(member.key)：\($0)" }
        }
    }

    private func stepBlock(_ name: String, _ value: JSONValue) -> some View {
        Block(name) {
            if let reason = value["skipped"]?.stringValue {
                Text("Skipped: \(reason)").foregroundColor(Theme.secondary)
            }
            let simple = simpleMembers(value)
            if !simple.isEmpty {
                VStack(alignment: .leading, spacing: 2) {
                    ForEach(simple, id: \.key) { member in
                        HStack(alignment: .top) {
                            Text(member.key.replacingOccurrences(of: "_", with: " "))
                                .foregroundColor(Theme.secondary)
                                .frame(width: 170, alignment: .leading)
                            Text(member.value.brief).textSelection(.enabled)
                        }
                        .font(.system(size: 12))
                    }
                }
            }
            let nested = nestedMembers(value)
            ForEach(nested, id: \.key) { member in
                DisclosureGroup(member.key.replacingOccurrences(of: "_", with: " ")) {
                    CodeText(text: member.value.pretty)
                }
                .font(.system(size: 12))
            }
        }
    }

    private func simpleMembers(_ value: JSONValue) -> [(key: String, value: JSONValue)] {
        value.members.filter { member in
            if member.key == "skipped" {
                return false
            }
            switch member.value {
            case .object, .array: return false
            default: return true
            }
        }
    }

    private func nestedMembers(_ value: JSONValue) -> [(key: String, value: JSONValue)] {
        value.members.filter { member in
            switch member.value {
            case .object(let found): return !found.isEmpty
            case .array(let found): return !found.isEmpty
            default: return false
            }
        }
    }

    private struct Step {
        let name: String
        let value: JSONValue
    }

    private func steps(_ run: RunRecord) -> [Step] {
        let members = run.report.members.filter { !nonSteps.contains($0.key) }
        let known = stepOrder.compactMap { name in
            members.first { $0.key == name }.map { Step(name: name, value: $0.value) }
        }
        let others = members.filter { !stepOrder.contains($0.key) }.map {
            Step(name: $0.key, value: $0.value)
        }
        var all = known + others
        if let totals = run.report["totals"] {
            all.append(Step(name: "totals", value: totals))
        }
        return all
    }

    @MainActor
    private func load() async {
        loading = true
        defer { loading = false }
        do {
            let backend = Backend.current()
            overview = try await backend.overview()
            runs = try await backend.runs(limit: 60)
            app.lastRunAt = overview?.lastRun?.finishedAt
            message = nil
            if selected == nil {
                selected = runs.first?.runId
            }
        } catch {
            message = error.localizedDescription
        }
    }
}
