import SwiftUI

struct RunsPane: View {
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
            if let message {
                ErrorBanner(message: message).padding(8)
            }
            if let overview {
                counts(overview)
            }
            HSplitView {
                runList.frame(minWidth: 260, idealWidth: 300, maxWidth: 380)
                detailView.frame(minWidth: 420, maxWidth: .infinity)
            }
        }
        .navigationTitle("Runs")
        .toolbar {
            ToolbarItem {
                Button {
                    Task { await load() }
                } label: {
                    Label("Reload", systemImage: "arrow.clockwise")
                }
                .disabled(loading)
            }
        }
        .task { await load() }
    }

    private func counts(_ overview: Overview) -> some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 16) {
                ForEach(overview.counts.sorted { $0.key < $1.key }, id: \.key) { item in
                    VStack(alignment: .leading, spacing: 2) {
                        Text(String(item.value)).font(.title3).fontWeight(.semibold)
                        Text(item.key.replacingOccurrences(of: "_", with: " "))
                            .font(.caption)
                            .foregroundColor(.secondary)
                    }
                }
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
        }
        .help(overview.store)
    }

    private var runList: some View {
        Group {
            if runs.isEmpty {
                Placeholder(text: loading ? "Loading…" : "No runs yet")
            } else {
                List(runs, selection: $selected) { run in
                    VStack(alignment: .leading, spacing: 3) {
                        HStack {
                            Text(shortTime(run.startedAt)).fontWeight(.medium)
                            Spacer()
                            if run.finishedAt == nil {
                                Text("unfinished").font(.caption).foregroundColor(.orange)
                            } else if hasError(run) {
                                Text("error").font(.caption).foregroundColor(.red)
                            }
                        }
                        Text(headline(run)).font(.caption).foregroundColor(.secondary)
                    }
                    .padding(.vertical, 2)
                    .tag(run.runId)
                }
            }
        }
    }

    @ViewBuilder
    private var detailView: some View {
        if let run = runs.first(where: { $0.runId == selected }) {
            ScrollView {
                VStack(alignment: .leading, spacing: 14) {
                    Text(
                        "Run \(run.runId) · started \(shortTime(run.startedAt)) · finished \(shortTime(run.finishedAt))"
                    )
                    .font(.caption)
                    .foregroundColor(.secondary)
                    .textSelection(.enabled)
                    if let skipped = run.report["skipped"]?.stringValue {
                        ErrorBanner(message: "Skipped: \(skipped)")
                    }
                    ForEach(steps(run), id: \.name) { step in
                        stepBlock(step.name, step.value)
                    }
                }
                .padding(16)
            }
        } else {
            Placeholder(text: "Choose a run")
        }
    }

    private func stepBlock(_ name: String, _ value: JSONValue) -> some View {
        Block(name) {
            if let failure = value["error"]?.stringValue {
                ErrorBanner(message: failure)
            }
            if let reason = value["skipped"]?.stringValue {
                Text("Skipped: \(reason)").foregroundColor(.secondary)
            }
            let simple = simpleMembers(value)
            if !simple.isEmpty {
                VStack(alignment: .leading, spacing: 2) {
                    ForEach(simple, id: \.key) { member in
                        HStack(alignment: .top) {
                            Text(member.key.replacingOccurrences(of: "_", with: " "))
                                .foregroundColor(.secondary)
                                .frame(width: 210, alignment: .leading)
                            Text(member.value.brief).textSelection(.enabled)
                        }
                        .font(.callout)
                    }
                }
            }
            let nested = nestedMembers(value)
            ForEach(nested, id: \.key) { member in
                DisclosureGroup(member.key.replacingOccurrences(of: "_", with: " ")) {
                    CodeText(text: member.value.pretty)
                }
            }
        }
    }

    private func simpleMembers(_ value: JSONValue) -> [(key: String, value: JSONValue)] {
        value.members.filter { member in
            if member.key == "error" || member.key == "skipped" {
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

    private func hasError(_ run: RunRecord) -> Bool {
        run.report.members.contains { $0.value["error"] != nil }
    }

    private func headline(_ run: RunRecord) -> String {
        var parts: [String] = []
        if let found = run.report["scan"]?["sessions_new"]?.numberValue {
            parts.append("\(Int(found)) new sessions")
        }
        if let calls = run.report["curate"]?["llm_calls"]?.numberValue {
            parts.append("\(Int(calls)) curate calls")
        }
        if let calls = run.report["reconcile"]?["llm_calls"]?.numberValue {
            parts.append("\(Int(calls)) reconcile calls")
        }
        if run.report["dry_run"] == .bool(true) {
            parts.append("dry run")
        }
        return parts.isEmpty ? run.runId : parts.joined(separator: " · ")
    }

    @MainActor
    private func load() async {
        loading = true
        defer { loading = false }
        do {
            let backend = Backend.current()
            overview = try await backend.overview()
            runs = try await backend.runs(limit: 60)
            message = nil
            if selected == nil {
                selected = runs.first?.runId
            }
        } catch {
            message = error.localizedDescription
        }
    }
}
