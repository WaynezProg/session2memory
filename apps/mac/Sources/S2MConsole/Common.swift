import SwiftUI

/// A line shown when something could not be loaded.
struct ErrorBanner: View {
    let message: String

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: "exclamationmark.triangle.fill").foregroundColor(.orange)
            Text(message).textSelection(.enabled)
            Spacer(minLength: 0)
        }
        .padding(8)
        .background(Color.orange.opacity(0.12))
        .cornerRadius(6)
    }
}

struct StatusBadge: View {
    let status: String

    private var color: Color {
        switch status {
        case "active": return .green
        case "superseded": return .blue
        case "retired": return .red
        case "stale": return .gray
        default: return .secondary
        }
    }

    var body: some View {
        Text(status)
            .font(.caption)
            .padding(.horizontal, 6)
            .padding(.vertical, 2)
            .background(color.opacity(0.18))
            .foregroundColor(color)
            .cornerRadius(4)
    }
}

struct Placeholder: View {
    let text: String

    var body: some View {
        Text(text)
            .foregroundColor(.secondary)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}

/// A titled block of a detail page.
struct Block<Content: View>: View {
    let title: String
    let content: Content

    init(_ title: String, @ViewBuilder content: () -> Content) {
        self.title = title
        self.content = content()
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title).font(.headline)
            content
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// Monospaced, selectable text for digests and JSON.
struct CodeText: View {
    let text: String

    var body: some View {
        Text(text)
            .font(.system(.callout, design: .monospaced))
            .textSelection(.enabled)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(8)
            .background(Color.secondary.opacity(0.08))
            .cornerRadius(6)
    }
}

/// What a batch of changes came to, for the line shown afterwards.
struct BatchOutcome: Equatable {
    var done = 0
    var failures: [String] = []

    /// "Retired 3." or "Retired 2, 1 failed: <first reason>".
    func summary(verb: String) -> String {
        if failures.isEmpty {
            return "\(verb) \(done)."
        }
        return "\(verb) \(done), \(failures.count) failed: \(failures[0])"
    }
}

/// Workspaces whose name or path holds every word typed, in the order given.
func filterProjects(_ projects: [Project], by text: String) -> [Project] {
    let words = text.lowercased().split(separator: " ").map(String.init)
    guard !words.isEmpty else { return projects }
    return projects.filter { project in
        let haystack = project.project.lowercased()
        return words.allSatisfy { haystack.contains($0) }
    }
}

/// What a change came to. Kept apart from loading errors, so a reload that succeeds
/// afterwards does not wipe out what failed.
struct ActionNote: Equatable {
    let text: String
    let failed: Bool

    init(_ outcome: BatchOutcome, verb: String) {
        text = outcome.summary(verb: verb)
        failed = !outcome.failures.isEmpty
    }

    init(text: String, failed: Bool = false) {
        self.text = text
        self.failed = failed
    }
}

/// Shows an `ActionNote` until it is dismissed or the next change replaces it.
struct NoteBanner: View {
    let note: ActionNote
    let dismiss: () -> Void

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: note.failed ? "exclamationmark.triangle.fill" : "checkmark.circle.fill")
                .foregroundColor(note.failed ? .orange : .green)
            Text(note.text).textSelection(.enabled)
            Spacer(minLength: 0)
            Button(action: dismiss) {
                Image(systemName: "xmark")
            }
            .buttonStyle(.plain)
            .help("Dismiss")
        }
        .padding(8)
        .background((note.failed ? Color.orange : Color.green).opacity(0.12))
        .cornerRadius(6)
    }
}

/// The most characters a memory holds (`MAX_TEXT_CHARS` in the Python package).
let memoryTextLimit = 240

/// Memory text as it will be stored: runs of spaces and line breaks become one space.
func tidyText(_ text: String) -> String {
    text.split(whereSeparator: { $0.isWhitespace }).joined(separator: " ")
}

/// The length the limit is checked against: code points, as the Python side counts them
/// (an accented letter typed as letter plus accent is two).
func storedLength(_ text: String) -> Int {
    tidyText(text).unicodeScalars.count
}

/// "128 / 240", red past the limit.
struct LengthCount: View {
    let text: String

    var body: some View {
        let count = storedLength(text)
        Text("\(count) / \(memoryTextLimit)")
            .font(.caption)
            .foregroundColor(count > memoryTextLimit ? .red : .secondary)
    }
}

/// The workspaces the Memory pane lists: those matching the filter, and, unless asked
/// otherwise, only those that have memory in some status. The chosen one always stays.
func workspacesToList(
    _ projects: [Project], filter: String, withoutMemory: Bool, keeping chosen: String?
) -> [Project] {
    filterProjects(projects, by: filter).filter { project in
        withoutMemory || project.project == chosen
            || project.active + project.superseded + project.retired + project.stale > 0
    }
}

/// The folder a workspace lies in, with the home folder written as "~".
func parentFolder(of path: String, home: String = NSHomeDirectory()) -> String {
    let parent = (path as NSString).deletingLastPathComponent
    if parent == home { return "~" }
    if parent.hasPrefix(home + "/") { return "~" + parent.dropFirst(home.count) }
    return parent
}

/// Shown where one entry's detail could not be loaded.
struct LoadFailed: View {
    let message: String
    let retry: () -> Void

    var body: some View {
        VStack(spacing: 10) {
            Text("Could not load this").font(.headline)
            Text(message).foregroundColor(.secondary).textSelection(.enabled)
                .multilineTextAlignment(.center)
            Button("Try Again", action: retry)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding(20)
    }
}

/// A row of ready-made reasons that fill the reason field.
struct QuickReasons: View {
    let choices: [String]
    @Binding var reason: String

    var body: some View {
        HStack(spacing: 6) {
            ForEach(choices, id: \.self) { choice in
                Button(choice) { reason = choice }
                    .buttonStyle(.bordered)
                    .controlSize(.small)
            }
        }
    }
}
