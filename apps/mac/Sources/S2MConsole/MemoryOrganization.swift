import Foundation
import SwiftUI

struct MemoryTopic: Decodable, Hashable, Identifiable {
    let id: String
    let label: String

    /// Memory nobody has filed yet, and every entry of a command line from before topics.
    static let general = MemoryTopic(id: "general", label: "待歸類")
}

/// Which memory the page shows. Memory is filed under topics, not under the folder a
/// session was opened in: coding memory under subjects, OpenClaw memory under the agent
/// it belongs to, since each agent has its own job. The two are never mixed.
enum MemoryLens: String, CaseIterable, Identifiable {
    case topics, agents

    var id: String { rawValue }

    var title: String { self == .topics ? "開發記憶 · 主題" : "OpenClaw · 代理人" }

    var scope: String { self == .topics ? "coding" : "openclaw" }

    /// What a group is called.
    var noun: String { self == .agents ? "代理人" : "主題" }

    func label(of memory: Memory) -> MemoryTopic { memory.topic ?? .general }
}

struct MemoryGroup: Identifiable {
    let topic: MemoryTopic
    let memories: [Memory]
    var id: String { topic.id }
}

enum MemoryOrganization {
    static func groups(_ memories: [Memory], by lens: MemoryLens = .topics) -> [MemoryGroup] {
        let buckets = Dictionary(grouping: memories) { lens.label(of: $0).id }
        return buckets.compactMap { _, rows in
            rows.first.map { MemoryGroup(topic: lens.label(of: $0), memories: rows) }
        }.sorted {
            if $0.id == "general" { return false }
            if $1.id == "general" { return true }
            return $0.topic.label.localizedStandardCompare($1.topic.label) == .orderedAscending
        }
    }

    static func filter(
        _ memories: [Memory], kind: String, topic: String, by lens: MemoryLens = .topics
    ) -> [Memory] {
        memories.filter {
            (kind.isEmpty || $0.kind == kind) && (topic.isEmpty || lens.label(of: $0).id == topic)
        }
    }
}

/// Uses only the current list response, so a global search never claims to describe one
/// workspace. Topics are hints; neither grouping nor filtering changes stored entries.
struct MemoryProjectOverview: View {
    let lens: MemoryLens
    let memories: [Memory]
    /// Every topic of this view, also the ones nothing is filed under yet.
    let topics: [Topic]
    let searching: Bool
    let status: String
    let selectedTopic: String
    let chooseTopic: (String) -> Void
    /// Rename, describe or merge the topic with this id.
    let manageTopic: (Topic) -> Void
    let addTopic: () -> Void

    private var groups: [MemoryGroup] { MemoryOrganization.groups(memories, by: lens) }

    private var hint: String {
        if searching { return "以下統計只包含本次搜尋結果。" }
        return lens == .topics
            ? "記憶歸在主題下，不看當時開的是哪個資料夾。新對話會先拿到相關主題的記憶。"
            : "每個代理人負責的工作不同，記憶各自歸檔，不互相混用。"
    }

    /// Topics nothing in the list is filed under: new ones, or emptied by a filter.
    private var emptyTopics: [Topic] {
        let used = Set(groups.map(\.id))
        return searching ? [] : topics.filter { !used.contains($0.id) && $0.active == 0 }
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                VStack(alignment: .leading, spacing: 8) {
                    Text(lens == .topics ? "主題概覽" : "代理人分工概覽")
                        .font(.system(size: 13, weight: .semibold)).foregroundColor(Theme.accent)
                    Text(searching ? "搜尋結果" : lens == .topics ? "所有開發記憶" : "OpenClaw 代理人記憶")
                        .font(.system(size: 25, weight: .semibold))
                    Text(hint).font(.system(size: 13)).foregroundColor(Theme.secondary)
                }
                HStack(spacing: 28) {
                    metric("\(memories.count)", Lexicon.status(status) + "記憶")
                    metric("\(groups.count)", "個\(lens.noun)")
                }
                Divider()
                HStack {
                    Text(lens.noun).font(.system(size: 16, weight: .semibold))
                    Spacer()
                    if !selectedTopic.isEmpty {
                        Button("顯示全部") { chooseTopic("") }.buttonStyle(.link)
                    }
                    if lens == .topics {
                        Button("新增主題") { addTopic() }.buttonStyle(.link)
                    }
                }
                ForEach(groups) { group in
                    let topic = topics.first { $0.id == group.id }
                    HStack(alignment: .top, spacing: 8) {
                        Button {
                            chooseTopic(group.id == selectedTopic ? "" : group.id)
                        } label: {
                            VStack(alignment: .leading, spacing: 7) {
                                HStack {
                                    Text(group.topic.label)
                                        .font(.system(size: 14, weight: .semibold))
                                    Spacer()
                                    Text("\(group.memories.count) 條").font(.system(size: 12))
                                }
                                Text(topic?.description.flatMap { lens == .topics ? $0 : nil }
                                    ?? group.memories.first?.text ?? "")
                                    .font(.system(size: 12)).lineLimit(2)
                                    .foregroundColor(Theme.secondary)
                            }
                            .foregroundColor(Theme.ink)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .card(selected: selectedTopic == group.id)
                        }.buttonStyle(.plain)
                        if lens == .topics, let topic {
                            Button("整理") { manageTopic(topic) }
                                .buttonStyle(.link).help("改名、補說明，或併到另一個主題")
                                .padding(.top, 14)
                        }
                    }
                }
                ForEach(emptyTopics) { topic in
                    HStack {
                        Text(topic.label).font(.system(size: 13))
                        Text("還沒有記憶").font(.system(size: 12)).foregroundColor(Theme.secondary)
                        Spacer()
                        Button("整理") { manageTopic(topic) }.buttonStyle(.link)
                    }
                }
                Text((lens == .agents ? "代理人依工作區與來源對話判定" : "主題由整理對話時判定，可以手動搬移、改名、合併")
                    + "。「使用中」表示仍會提供給代理人；最後確認時間不代表內容已逐條重新驗證。")
                    .font(.system(size: 12)).foregroundColor(Theme.secondary)
            }.padding(24)
        }
    }

    private func metric(_ value: String, _ label: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(value).font(.system(size: 23, weight: .semibold))
            Text(label).font(.system(size: 12)).foregroundColor(Theme.secondary)
        }
    }
}
