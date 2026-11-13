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
