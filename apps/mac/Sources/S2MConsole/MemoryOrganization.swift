import Foundation
import SwiftUI

struct MemoryTopic: Decodable, Hashable, Identifiable {
    let id: String
    let label: String

    static let general = MemoryTopic(id: "general", label: "其他專案規則")
}

struct MemoryGroup: Identifiable {
    let topic: MemoryTopic
    let memories: [Memory]
    var id: String { topic.id }
}

enum MemoryOrganization {
    static func groups(_ memories: [Memory]) -> [MemoryGroup] {
        let buckets = Dictionary(grouping: memories) { ($0.topic ?? .general).id }
        return buckets.map { _, rows in
            MemoryGroup(topic: rows.first?.topic ?? .general, memories: rows)
        }.sorted {
            if $0.id == "general" { return false }
            if $1.id == "general" { return true }
            return $0.topic.label.localizedStandardCompare($1.topic.label) == .orderedAscending
        }
    }

    static func filter(_ memories: [Memory], kind: String, topic: String) -> [Memory] {
        memories.filter {
            (kind.isEmpty || $0.kind == kind) && (topic.isEmpty || ($0.topic ?? .general).id == topic)
        }
    }
}

/// Uses only the current list response, so a global search never claims to describe one
/// workspace. Topics are hints; neither grouping nor filtering changes stored entries.
struct MemoryProjectOverview: View {
    let project: Project?
    let memories: [Memory]
    let globalSearch: Bool
    let searching: Bool
    let status: String
    let selectedTopic: String
    let chooseTopic: (String) -> Void

    private var groups: [MemoryGroup] { MemoryOrganization.groups(memories) }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                VStack(alignment: .leading, spacing: 8) {
                    Text(globalSearch ? "跨專案搜尋概覽" : "專案概覽")
                        .font(.system(size: 13, weight: .semibold)).foregroundColor(Theme.accent)
                    Text(globalSearch ? "搜尋結果" : project?.name ?? "選擇專案")
                        .font(.system(size: 25, weight: .semibold))
                    if !globalSearch, let project {
                        Text(project.project).font(.system(size: 12))
                            .foregroundColor(Theme.secondary).textSelection(.enabled)
                    }
                    Text(searching ? "以下統計只包含本次搜尋結果。" : "先看主題，再選一條查看內容、來源與歷史。")
                        .font(.system(size: 13)).foregroundColor(Theme.secondary)
                }
                HStack(spacing: 28) {
                    metric("\(memories.count)", Lexicon.status(status) + "記憶")
                    metric("\(groups.count)", "個主題")
                    if globalSearch {
                        metric("\(Set(memories.map(\.project)).count)", "個專案")
                    } else if let project {
                        metric("\(project.sessions)", "個對話")
                    }
                }
                Divider()
                HStack {
                    Text("主題").font(.system(size: 16, weight: .semibold))
                    Spacer()
                    if !selectedTopic.isEmpty {
                        Button("顯示全部") { chooseTopic("") }.buttonStyle(.link)
                    }
                }
                ForEach(groups) { group in
                    Button {
                        chooseTopic(group.id == selectedTopic ? "" : group.id)
                    } label: {
                        VStack(alignment: .leading, spacing: 7) {
                            HStack {
                                Text(group.topic.label).font(.system(size: 14, weight: .semibold))
                                Spacer()
                                Text("\(group.memories.count) 條").font(.system(size: 12))
                            }
                            Text(group.memories.first?.text ?? "")
                                .font(.system(size: 12)).lineLimit(2)
                                .foregroundColor(Theme.secondary)
                        }
                        .foregroundColor(Theme.ink).frame(maxWidth: .infinity, alignment: .leading)
                        .card(selected: selectedTopic == group.id)
                    }.buttonStyle(.plain)
                }
                Text("主題依文字提示分組，可切回時間排序。「使用中」表示仍會提供給代理人；最後確認時間不代表內容已逐條重新驗證。")
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
