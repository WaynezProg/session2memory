import SwiftUI

/// A line shown when something could not be loaded.
struct ErrorBanner: View {
    let message: String

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: "exclamationmark.triangle.fill").foregroundColor(Theme.amberText)
            Text(message).foregroundColor(Theme.ink).textSelection(.enabled)
            Spacer(minLength: 0)
        }
        .padding(10)
        .background(Theme.amberTint)
        .cornerRadius(10)
    }
}

struct StatusBadge: View {
    let status: String

    var body: some View {
        Tag(text: Lexicon.status(status), tone: status == "active" ? .accent : .neutral)
    }
}

struct Placeholder: View {
    let text: String

    var body: some View {
        Text(text)
            .font(.system(size: 14))
            .foregroundColor(Theme.secondary)
            .multilineTextAlignment(.center)
            .padding(24)
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
        VStack(alignment: .leading, spacing: 8) {
            Text(title).font(.system(size: 13, weight: .semibold)).foregroundColor(Theme.secondary)
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
            .padding(10)
            .background(Theme.sidebar)
            .cornerRadius(10)
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

    /// "已停用 3 條。" or "已停用 2 條，1 條沒有成功：<first reason>".
    func message(_ verb: String, unit: String) -> String {
        if failures.isEmpty {
            return "\(verb) \(done) \(unit)。"
        }
        return "\(verb) \(done) \(unit)，\(failures.count) \(unit)沒有成功：\(failures[0])"
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

    init(_ outcome: BatchOutcome, verb: String, unit: String) {
        text = outcome.message(verb, unit: unit)
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
                .foregroundColor(note.failed ? Theme.amberText : Theme.accent)
            Text(note.text).foregroundColor(Theme.ink).textSelection(.enabled)
            Spacer(minLength: 0)
            Button(action: dismiss) {
                Image(systemName: "xmark").foregroundColor(Theme.secondary)
            }
            .buttonStyle(.plain)
            .help("關閉")
        }
        .padding(10)
        .background(note.failed ? Theme.amberTint : Theme.accentTint)
        .cornerRadius(10)
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
            .foregroundColor(count > memoryTextLimit ? Theme.dangerText : Theme.secondary)
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
            Text("讀不到這筆資料").font(.headline).foregroundColor(Theme.ink)
            Text(message).foregroundColor(Theme.secondary).textSelection(.enabled)
                .multilineTextAlignment(.center)
            Button("再試一次", action: retry).buttonStyle(OutlineButtonStyle())
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
