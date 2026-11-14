import SwiftUI

/// Colours of the console. One accent (green) for what can be acted on or is in use, amber
/// only for what waits, everything else neutral.
enum Theme {
    static let accent = Color(hex: 0x1F6F54)
    static let accentTint = Color(hex: 0xE6F1EC)
    static let accentSoft = Color(hex: 0xF6FAF8)
    static let accentBar = Color(hex: 0x9CC7B5)
    static let ink = Color(hex: 0x1A1D1A)
    static let secondary = Color(hex: 0x5C635C)
    static let hairline = Color(hex: 0xE4E6E1)
    static let control = Color(hex: 0xD5D8D1)
    static let sidebar = Color(hex: 0xF3F4F1)
    static let panel = Color(hex: 0xFAFAF8)
    static let surface = Color.white
    static let amberText = Color(hex: 0x7A4A00)
    static let amberTint = Color(hex: 0xFBEFD9)
    static let amberBar = Color(hex: 0xF0C56B)
    static let neutralTint = Color(hex: 0xEEF0EC)
    static let dangerText = Color(hex: 0xA3341F)
}

extension Color {
    init(hex: UInt32) {
        self.init(
            .sRGB,
            red: Double((hex >> 16) & 0xFF) / 255,
            green: Double((hex >> 8) & 0xFF) / 255,
            blue: Double(hex & 0xFF) / 255,
            opacity: 1)
    }
}

// MARK: words

/// The words the console shows for what the command line names in English.
enum Lexicon {
    static func kind(_ value: String) -> String {
        switch value {
        case "decision": return "決定"
        case "constraint": return "限制"
        case "pitfall": return "地雷"
        case "fact": return "事實"
        case "preference": return "偏好"
        case "procedure": return "做法"
        default: return value
        }
    }

    static func status(_ value: String) -> String {
        switch value {
        case "active": return "使用中"
        case "superseded": return "已改寫"
        case "retired": return "已停用"
        case "stale": return "可能過期"
        case "all": return "全部狀態"
        default: return value
        }
    }

    static func tool(_ value: String?) -> String {
        switch value ?? "" {
        case "codex": return "Codex"
        case "claude": return "Claude Code"
        case "grok": return "Grok"
        case "muse": return "Muse"
        case "antigravity": return "Antigravity"
        case "cursor": return "Cursor"
        case "cursor-cli": return "Cursor CLI"
        case "openclaw-native": return "OpenClaw"
        case "": return "未知工具"
        default: return value ?? ""
        }
    }

    /// What happened to a memory, as its history names it.
    static func event(_ op: String) -> String {
        switch op {
        case "add": return "加入記憶"
        case "confirm": return "再次確認"
        case "edit": return "改寫"
        case "supersede", "superseded": return "被新的說法取代"
        case "retire", "retired": return "停用"
        case "restore", "restored": return "復原"
        case "stale": return "標為可能過期"
        case "merge", "merged": return "合併"
        case "note": return "備註"
        default: return op
        }
    }

    /// The ways HKS looks for an answer.
    static func route(_ value: String) -> String {
        switch value {
        case "wiki": return "整理過的頁面"
        case "graph": return "關聯圖"
        case "vector": return "語意相近"
        case "lexical": return "關鍵字"
        case "page_tree": return "文件目錄"
        default: return value
        }
    }

    /// Why no run would read a session (`_curation` in console.py).
    static func blocked(_ reason: String) -> String {
        if reason.contains("no digest") { return "沒有可讀的內容" }
        if reason.contains("workspace is unknown") { return "不知道屬於哪個專案" }
        if reason.contains("scratch") { return "屬於暫存、測試或已刪除的資料夾" }
        if reason.contains("too little") { return "只有一來一往，內容太少" }
        return reason
    }
}

// MARK: time

private func parseStamp(_ value: String) -> Date? {
    let formatter = ISO8601DateFormatter()
    formatter.formatOptions = [.withInternetDateTime]
    if let date = formatter.date(from: value) { return date }
    // Python writes microseconds, which the formatter does not read: drop them.
    let plain = value.replacingOccurrences(
        of: #"\.\d+"#, with: "", options: .regularExpression)
    return formatter.date(from: plain)
}

private func format(_ value: String?, _ pattern: String, _ zone: TimeZone) -> String? {
    guard let value, !value.isEmpty, let date = parseStamp(value) else { return nil }
    let formatter = DateFormatter()
    formatter.locale = Locale(identifier: "en_US_POSIX")
    formatter.timeZone = zone
    formatter.dateFormat = pattern
    return formatter.string(from: date)
}

/// "2026-11-14T05:53:27+00:00" -> "11/14 13:53" where the Mac is eight hours ahead. A
/// value that cannot be read is shown as it came.
func localTime(_ value: String?, zone: TimeZone = .current) -> String {
    format(value, "M/d HH:mm", zone) ?? shortTime(value)
}

/// The day of a stamp in local time, for grouping: "11 月 14 日".
func localDay(_ value: String?, zone: TimeZone = .current) -> String {
    format(value, "M 月 d 日", zone) ?? shortTime(value)
}

/// The time of day of a stamp in local time: "13:53".
func localClock(_ value: String?, zone: TimeZone = .current) -> String {
    format(value, "HH:mm", zone) ?? shortTime(value)
}

/// 7039 -> "7,039".
func grouped(_ number: Int) -> String {
    let formatter = NumberFormatter()
    formatter.numberStyle = .decimal
    formatter.locale = Locale(identifier: "en_US")
    return formatter.string(from: NSNumber(value: number)) ?? String(number)
}

// MARK: building blocks

/// The top of a page: its name, one line on what it is for, and its controls.
struct PageHeader<Trailing: View>: View {
    let title: String
    let subtitle: String
    let trailing: Trailing

    init(_ title: String, subtitle: String, @ViewBuilder trailing: () -> Trailing) {
        self.title = title
        self.subtitle = subtitle
        self.trailing = trailing()
    }

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            VStack(alignment: .leading, spacing: 4) {
                Text(title).font(.system(size: 26, weight: .bold)).foregroundColor(Theme.ink)
                Text(subtitle).font(.system(size: 14)).foregroundColor(Theme.secondary)
            }
            Spacer(minLength: 12)
            trailing
        }
        .padding(.horizontal, 32)
        .padding(.top, 24)
        .padding(.bottom, 16)
    }
}

extension PageHeader where Trailing == EmptyView {
    init(_ title: String, subtitle: String) {
        self.init(title, subtitle: subtitle) { EmptyView() }
    }
}

struct SearchField: View {
    @Binding var text: String
    let prompt: String

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: "magnifyingglass").foregroundColor(Theme.secondary)
            TextField(prompt, text: $text)
                .textFieldStyle(.plain)
                .font(.system(size: 14))
            if !text.isEmpty {
                Button {
                    text = ""
                } label: {
                    Image(systemName: "xmark.circle.fill").foregroundColor(Theme.secondary)
                }
                .buttonStyle(.plain)
                .help("清除")
            }
        }
        .padding(.horizontal, 12)
        .frame(width: 250, height: 38)
        .background(Theme.surface)
        .overlay(RoundedRectangle(cornerRadius: 10).stroke(Theme.control))
        .cornerRadius(10)
    }
}

/// The one filled button of a page or sheet.
struct PrimaryButtonStyle: ButtonStyle {
    var height: CGFloat = 38
    @Environment(\.isEnabled) private var enabled

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 14, weight: .semibold))
            .foregroundColor(.white)
            .padding(.horizontal, height > 40 ? 24 : 16)
            .frame(height: height)
            .background(Theme.accent.opacity(enabled ? (configuration.isPressed ? 0.85 : 1) : 0.4))
            .cornerRadius(10)
            .contentShape(Rectangle())
    }
}

/// A bordered button; `accent` for the main thing to do with what is shown.
struct OutlineButtonStyle: ButtonStyle {
    var accent = false
    @Environment(\.isEnabled) private var enabled

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 14, weight: accent ? .semibold : .regular))
            .foregroundColor(accent ? Theme.accent : Theme.ink)
            .padding(.horizontal, 14)
            .frame(height: 36)
            .background(configuration.isPressed ? Theme.neutralTint : Theme.surface)
            .overlay(
                RoundedRectangle(cornerRadius: 10).stroke(accent ? Theme.accent : Theme.control)
            )
            .cornerRadius(10)
            .opacity(enabled ? 1 : 0.45)
            .contentShape(Rectangle())
    }
}

/// A round filter button; the chosen one is dark.
struct Chip: View {
    let title: String
    let selected: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(title)
                .font(.system(size: 13, weight: selected ? .medium : .regular))
                .foregroundColor(selected ? .white : Theme.ink)
                .padding(.horizontal, 12)
                .frame(height: 32)
                .background(selected ? Theme.ink : Theme.sidebar)
                .cornerRadius(16)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(selected ? .isSelected : [])
    }
}

/// A small label: green for in use or done, amber for waiting, grey otherwise.
struct Tag: View {
    enum Tone { case accent, amber, neutral }

    let text: String
    var tone: Tone = .neutral

    var body: some View {
        Text(text)
            .font(.system(size: 12, weight: .semibold))
            .foregroundColor(foreground)
            .padding(.horizontal, 9)
            .padding(.vertical, 3)
            .background(background)
            .cornerRadius(11)
            .fixedSize()
    }

    private var foreground: Color {
        switch tone {
        case .accent: return Theme.accent
        case .amber: return Theme.amberText
        case .neutral: return Color(hex: 0x4A504A)
        }
    }

    private var background: Color {
        switch tone {
        case .accent: return Theme.accentTint
        case .amber: return Theme.amberTint
        case .neutral: return Theme.neutralTint
        }
    }
}

extension View {
    /// A white box with a hairline, or the green outline of what is chosen.
    func card(selected: Bool = false, padding: CGFloat = 14) -> some View {
        self
            .padding(padding)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(selected ? Theme.accentSoft : Theme.surface)
            .overlay(
                RoundedRectangle(cornerRadius: 12)
                    .stroke(selected ? Theme.accent : Theme.hairline, lineWidth: selected ? 2 : 1)
            )
            .cornerRadius(12)
    }

    /// A row of a list that draws its own selection: it covers the system's highlight,
    /// which would otherwise show as a block behind the row.
    func listCell(gap: CGFloat) -> some View {
        self
            .padding(.vertical, gap)
            .padding(.horizontal, 4)
            .background(Theme.surface)
            .listRowSeparator(.hidden)
            .listRowInsets(EdgeInsets())
            .listRowBackground(Theme.surface)
    }

    /// The right-hand side of a page, where one entry is read.
    func detailPanel(width: CGFloat = 400) -> some View {
        self
            .frame(width: width)
            .frame(maxHeight: .infinity)
            .background(Theme.panel)
    }
}

/// One number with what it counts, for the pages that report.
struct StatTile: View {
    let value: String
    let label: String
    var note: String?
    var color: Color = Theme.ink

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(value).font(.system(size: 30, weight: .bold)).foregroundColor(color)
            Text(label).font(.system(size: 14, weight: .semibold)).foregroundColor(Theme.ink)
            if let note {
                Text(note).font(.system(size: 12)).foregroundColor(Theme.secondary)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// A closed group for what only matters when something has to be looked into.
struct EngineeringDetails<Content: View>: View {
    let title: String
    let content: Content
    @State private var open = false

    init(_ title: String, @ViewBuilder content: () -> Content) {
        self.title = title
        self.content = content()
    }

    var body: some View {
        DisclosureGroup(isExpanded: $open) {
            VStack(alignment: .leading, spacing: 8) { content }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.top, 8)
        } label: {
            Text(title).font(.system(size: 13)).foregroundColor(Theme.secondary)
        }
    }
}
