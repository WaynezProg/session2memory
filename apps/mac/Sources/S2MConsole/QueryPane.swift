import SwiftUI

struct QueryPane: View {
    @State private var question = ""
    @State private var response: QueryResponse?
    @State private var message: String?
    @State private var running = false
    @State private var asked = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                TextField("Ask the knowledge base", text: $question)
                    .textFieldStyle(.roundedBorder)
                    .onSubmit { run() }
                Button("Ask") { run() }
                    .keyboardShortcut(.defaultAction)
                    .disabled(running || trimmed.isEmpty)
            }
            .padding(12)
            if let message {
                ErrorBanner(message: message).padding(.horizontal, 12)
            }
            if running {
                ProgressView("Running ks query…").padding(16)
            }
            if let response {
                ScrollView {
                    VStack(alignment: .leading, spacing: 16) {
                        answer(response)
                        evidence(response)
                        trace(response)
                    }
                    .padding(16)
                }
            } else if !running {
                Placeholder(
                    text: "Asks HKS with `ks query … --writeback=no`; nothing is written back.")
            }
            if running {
                // Nothing below fills the pane while a query runs; keep the field at the top.
                Spacer(minLength: 0)
            }
        }
        .navigationTitle("HKS Query")
    }

    private var trimmed: String {
        question.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private func answer(_ response: QueryResponse) -> some View {
        Block("Answer") {
            HStack(spacing: 8) {
                if response.source.isEmpty {
                    Text("no hit")
                        .font(.caption)
                        .padding(.horizontal, 6)
                        .padding(.vertical, 2)
                        .background(Color.gray.opacity(0.2))
                        .cornerRadius(4)
                }
                ForEach(response.source, id: \.self) { route in
                    Text(route)
                        .font(.caption)
                        .padding(.horizontal, 6)
                        .padding(.vertical, 2)
                        .background(Color.accentColor.opacity(0.18))
                        .cornerRadius(4)
                }
                Text(String(format: "confidence %.2f", response.confidence))
                    .font(.caption)
                    .foregroundColor(.secondary)
                Spacer()
                Text(asked).font(.caption).foregroundColor(.secondary).lineLimit(1)
            }
            Text(response.answer).textSelection(.enabled)
        }
    }

    @ViewBuilder
    private func evidence(_ response: QueryResponse) -> some View {
        let items = response.evidence ?? []
        if !items.isEmpty {
            Block("Evidence") {
                ForEach(Array(items.enumerated()), id: \.offset) { _, item in
                    VStack(alignment: .leading, spacing: 2) {
                        Text(
                            [item.sourceRelpath, item.sectionPath, item.route]
                                .compactMap { $0 }
                                .joined(separator: " · ")
                        )
                        .font(.caption)
                        .foregroundColor(.secondary)
                        .textSelection(.enabled)
                        Text(item.quote ?? "").font(.callout).textSelection(.enabled)
                    }
                    .padding(.vertical, 2)
                }
            }
        }
    }

    private func trace(_ response: QueryResponse) -> some View {
        Block("Trace (route: \(response.trace.route))") {
            ForEach(Array(response.trace.steps.enumerated()), id: \.offset) { _, step in
                DisclosureGroup(step.kind + stepNote(step)) {
                    CodeText(text: step.detail.pretty)
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
