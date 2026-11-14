import SwiftUI

/// How sure an answer is, in three steps. 0.75 is where HKS itself trusts an answer enough
/// to write it back; below 0.5 it is more likely a near miss than an answer.
func confidenceLabel(_ value: Double) -> (text: String, tone: Tag.Tone) {
    if value >= 0.75 { return ("把握高", .accent) }
    if value >= 0.5 { return ("把握普通", .neutral) }
    return ("把握不高", .amber)
}

/// One way HKS looked, and what came of it.
struct LookupOutcome: Identifiable, Equatable {
    let route: String
    let used: Bool
    let hit: Bool?

    var id: String { route }

    var note: String {
        if used { return "採用這個" }
        switch hit {
        case .some(true): return "有找到，沒採用"
        case .some(false): return "沒找到"
        case .none: return "有查"
        }
    }
}

/// The `*_lookup` steps of a trace, in the order they ran.
func lookups(_ response: QueryResponse) -> [LookupOutcome] {
    response.trace.steps.compactMap { step in
        guard step.kind.hasSuffix("_lookup") else { return nil }
        let route = String(step.kind.dropLast("_lookup".count))
        var hit: Bool?
        if let found = step.detail["hit"] { hit = found == .bool(true) }
        return LookupOutcome(route: route, used: response.source.contains(route), hit: hit)
    }
}

struct QueryPane: View {
    @State private var question = ""
    @State private var response: QueryResponse?
    @State private var message: String?
    @State private var running = false
    @State private var asked = ""

    var body: some View {
        VStack(spacing: 0) {
            PageHeader("問知識庫", subtitle: "直接問 HKS 知識庫。只會查，不會寫回任何東西。")
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    field
                    if let message {
                        ErrorBanner(message: message)
                    }
                    if running {
                        HStack(spacing: 10) {
                            ProgressView().controlSize(.small)
                            Text("正在查…").foregroundColor(Theme.secondary)
                        }
                    }
                    if let response {
                        answer(response)
                        how(response)
                    }
                }
                .frame(maxWidth: 760, alignment: .leading)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, 32)
                .padding(.bottom, 24)
            }
        }
    }

    private var trimmed: String {
        question.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private var field: some View {
        HStack(spacing: 10) {
            HStack(spacing: 10) {
                Image(systemName: "magnifyingglass").foregroundColor(Theme.secondary)
                TextField("想問什麼？", text: $question)
                    .textFieldStyle(.plain)
                    .font(.system(size: 16))
                    .onSubmit { run() }
            }
            .padding(.horizontal, 16)
            .frame(height: 48)
            .overlay(RoundedRectangle(cornerRadius: 12).stroke(Theme.accent, lineWidth: 2))
            Button("提問") { run() }
                .buttonStyle(PrimaryButtonStyle(height: 48))
                .keyboardShortcut(.defaultAction)
                .disabled(running || trimmed.isEmpty)
        }
    }

    private func answer(_ response: QueryResponse) -> some View {
        let found = !response.source.isEmpty
        let sure = confidenceLabel(response.confidence)
        return VStack(alignment: .leading, spacing: 14) {
            HStack(spacing: 8) {
                if found {
                    Tag(text: sure.text, tone: sure.tone)
                    Text(
                        "用「\(response.source.map(Lexicon.route).joined(separator: "、"))」找到 · 相符程度 "
                            + String(format: "%.2f", response.confidence)
                    )
                    .font(.system(size: 12))
                    .foregroundColor(Theme.secondary)
                } else {
                    Tag(text: "沒找到")
                }
                Spacer()
                Text(asked).font(.system(size: 12)).foregroundColor(Theme.secondary).lineLimit(1)
            }
            Text(response.answer)
                .font(.system(size: 15))
                .lineSpacing(4)
                .textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(14)
                .background(Theme.sidebar)
                .cornerRadius(10)
            let items = response.evidence ?? []
            if !items.isEmpty {
                Block("出處") {
                    ForEach(Array(items.enumerated()), id: \.offset) { _, item in
                        VStack(alignment: .leading, spacing: 2) {
                            Text(
                                [item.sourceRelpath, item.sectionPath]
                                    .compactMap { $0 }
                                    .joined(separator: " · ")
                            )
                            .font(.system(size: 14))
                            .textSelection(.enabled)
                            if let quote = item.quote, !quote.isEmpty, quote != response.answer {
                                Text(quote)
                                    .font(.system(size: 13))
                                    .foregroundColor(Theme.secondary)
                                    .lineLimit(3)
                                    .textSelection(.enabled)
                            }
                        }
                    }
                }
            }
            if found {
                Text("知識庫回的是原文片段，不是重新寫過的答案。把握不高時，換個問法再試。")
                    .font(.system(size: 12))
                    .foregroundColor(Theme.secondary)
            }
        }
        .padding(20)
        .overlay(RoundedRectangle(cornerRadius: 14).stroke(Theme.hairline))
    }

    private func how(_ response: QueryResponse) -> some View {
        let found = lookups(response)
        return VStack(alignment: .leading, spacing: 10) {
            if !found.isEmpty {
                Text("怎麼找到的")
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundColor(Theme.secondary)
                LazyVGrid(
                    columns: [GridItem(.adaptive(minimum: 130), spacing: 8)], alignment: .leading,
                    spacing: 8
                ) {
                    ForEach(found) { item in
                        VStack(alignment: .leading, spacing: 2) {
                            Text(Lexicon.route(item.route))
                                .font(.system(size: 14, weight: .semibold))
                            Text(item.note).font(.system(size: 12))
                        }
                        .foregroundColor(item.used ? Theme.accent : Theme.ink)
                        .padding(12)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(item.used ? Theme.accentSoft : Theme.sidebar)
                        .overlay(
                            RoundedRectangle(cornerRadius: 12)
                                .stroke(item.used ? Theme.accent : Color.clear)
                        )
                        .cornerRadius(12)
                    }
                }
            }
            EngineeringDetails("完整過程（給工程用）") {
                Text("route: \(response.trace.route)").font(.system(size: 12))
                ForEach(Array(response.trace.steps.enumerated()), id: \.offset) { _, step in
                    DisclosureGroup(step.kind + stepNote(step)) {
                        CodeText(text: step.detail.pretty)
                    }
                    .font(.system(size: 12))
                }
            }
        }
    }

    private func stepNote(_ step: TraceStep) -> String {
        if let hit = step.detail["hit"] {
            return hit == .bool(true) ? " — hit" : " — miss"
        }
        if let status = step.detail["status"]?.stringValue {
            return " — \(status)"
        }
        if let strategy = step.detail["strategy"]?.stringValue {
            return " — \(strategy)"
        }
        return ""
    }

    private func run() {
        let text = trimmed
        guard !text.isEmpty, !running else { return }
        running = true
        message = nil
        response = nil
        asked = text
        Task { @MainActor in
            do {
                response = try await Backend.current().query(text)
            } catch {
                message = error.localizedDescription
            }
            running = false
        }
    }
}
