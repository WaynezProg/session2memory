import SwiftUI

/// One run's count of new sessions, for the chart.
struct RunBar: Identifiable, Equatable {
    let id: String
    let startedAt: String
    let count: Int
}

/// The latest runs that read sessions, oldest first.
func runBars(_ runs: [RunRecord], limit: Int = 8) -> [RunBar] {
    let found = runs.compactMap { run -> RunBar? in
        guard let count = run.report["scan"]?["sessions_new"]?.numberValue else { return nil }
        return RunBar(id: run.runId, startedAt: run.startedAt, count: Int(count))
    }
    return Array(found.prefix(limit).reversed())
}

/// The count a full-height bar stands for. One run far above the rest (a first import)
/// would flatten the others, so the scale then follows the second largest.
func barScale(_ counts: [Int]) -> Int {
    let sorted = counts.sorted(by: >)
    guard let largest = sorted.first else { return 1 }
    if sorted.count > 1, sorted[1] > 0, largest > sorted[1] * 4 { return sorted[1] }
    return max(largest, 1)
}

/// How much is remembered and how much still waits, at a glance.
struct OverviewPane: View {
    @EnvironmentObject private var app: AppState
    @State private var overview: Overview?
    @State private var projects: [Project] = []
    @State private var runs: [RunRecord] = []
    @State private var message: String?
    @State private var loading = false

    var body: some View {
        VStack(spacing: 0) {
            PageHeader("總覽", subtitle: "代理人現在記得多少，還有多少對話沒讀。") {
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
                ErrorBanner(message: message).padding(.horizontal, 32).padding(.bottom, 12)
            }
            if let overview {
                ScrollView {
                    VStack(spacing: 16) {
                        HStack(alignment: .top, spacing: 16) {
                            remembered(overview.counts)
                            progress(overview.counts)
                        }
                        .fixedSize(horizontal: false, vertical: true)
                        HStack(alignment: .top, spacing: 16) {
                            chart
                            topProjects
                        }
                        .fixedSize(horizontal: false, vertical: true)
                    }
                    .padding(.horizontal, 32)
                    .padding(.bottom, 24)
                }
            } else {
                Placeholder(text: loading ? "讀取中…" : "沒有資料")
            }
        }
        .task { await load() }
    }

    // MARK: cards

    private func remembered(_ counts: [String: Int]) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("現在記得").font(.system(size: 13, weight: .medium))
            Text(grouped(counts["memories_active"] ?? 0)).font(.system(size: 44, weight: .bold))
            Text("條使用中的記憶").font(.system(size: 14, weight: .semibold))
            Text(
                "已改寫 \(counts["memories_superseded"] ?? 0) · 已停用 \(counts["memories_retired"] ?? 0) · 可能過期 \(counts["memories_stale"] ?? 0)"
            )
            .font(.system(size: 12))
            .padding(.top, 6)
        }
        .foregroundColor(.white)
        .padding(20)
        .frame(width: 280, alignment: .leading)
        .frame(maxHeight: .infinity, alignment: .topLeading)
        .background(Theme.accent)
        .cornerRadius(14)
    }

    private func progress(_ counts: [String: Int]) -> some View {
        let total = counts["sessions"] ?? 0
        let waiting = counts["sessions_pending"] ?? 0
        let handled = max(total - waiting, 0)
        let share = total > 0 ? Double(handled) / Double(total) : 0
        return VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .firstTextBaseline) {
                Text("對話整理進度").font(.system(size: 15, weight: .semibold))
                Spacer()
                Text("共 \(grouped(total)) 段對話").font(.system(size: 13))
                    .foregroundColor(Theme.secondary)
            }
            GeometryReader { box in
                HStack(spacing: 2) {
                    Rectangle().fill(Theme.accent)
                        .frame(width: max(box.size.width * share - 1, 0))
                    Rectangle().fill(Theme.amberBar)
                }
            }
            .frame(height: 26)
            .cornerRadius(8)
            .accessibilityElement()
            .accessibilityLabel(
                "\(grouped(total)) 段對話中，\(grouped(handled)) 段已處理或不需整理，\(grouped(waiting)) 段等待整理")
            HStack(alignment: .top, spacing: 28) {
                legend(Theme.accent, grouped(handled), "段已處理或不需整理（\(percent(share))）")
                legend(Theme.amberBar, grouped(waiting), "段等待整理（\(percent(1 - share))）")
                Spacer()
                VStack(alignment: .trailing, spacing: 2) {
                    Text(grouped(counts["sessions_with_summary"] ?? 0))
                        .font(.system(size: 20, weight: .bold))
                    Text("段已有摘要").font(.system(size: 12)).foregroundColor(Theme.secondary)
                }
            }
            if waiting > 0 {
                Button("看等待整理的對話") { app.showPendingSessions() }
                    .buttonStyle(.plain)
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundColor(Theme.accent)
            }
        }
        .padding(20)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .overlay(RoundedRectangle(cornerRadius: 14).stroke(Theme.hairline))
    }

    private func legend(_ color: Color, _ value: String, _ label: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(spacing: 8) {
                RoundedRectangle(cornerRadius: 3).fill(color).frame(width: 10, height: 10)
                Text(value).font(.system(size: 20, weight: .bold))
            }
            Text(label).font(.system(size: 12)).foregroundColor(Theme.secondary)
        }
    }

    private func percent(_ share: Double) -> String {
        "\(Int((share * 100).rounded()))%"
    }

    private var chart: some View {
        let bars = runBars(runs)
        let scale = barScale(bars.map(\.count))
        return VStack(alignment: .leading, spacing: 6) {
            Text("每次整理讀到的新對話").font(.system(size: 15, weight: .semibold))
            Text(chartNote(bars, scale: scale))
                .font(.system(size: 12))
                .foregroundColor(Theme.secondary)
            if !bars.isEmpty {
                HStack(alignment: .bottom, spacing: 12) {
                    ForEach(bars) { bar in
                        let latest = bar.id == bars.last?.id
                        VStack(spacing: 6) {
                            Text(grouped(bar.count))
                                .font(.system(size: 13, weight: latest ? .bold : .semibold))
                                .foregroundColor(latest ? Theme.accent : Theme.ink)
                            UnevenTop()
                                .fill(
                                    bar.count > scale
                                        ? Theme.control : (latest ? Theme.accent : Theme.accentBar))
                                .frame(height: height(bar.count, scale: scale))
                            VStack(spacing: 0) {
                                Text(localDayShort(bar.startedAt))
                                Text(localClock(bar.startedAt))
                            }
                            .font(.system(size: 11, weight: latest ? .semibold : .regular))
                            .foregroundColor(latest ? Theme.accent : Theme.secondary)
                        }
                        .frame(maxWidth: .infinity)
                    }
                }
                .padding(.top, 12)
                .frame(height: 250, alignment: .bottom)
            }
            Spacer(minLength: 0)
            Button("看整理紀錄") { app.pane = .runs }
                .buttonStyle(.plain)
                .font(.system(size: 13, weight: .semibold))
                .foregroundColor(Theme.accent)
        }
        .padding(20)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .overlay(RoundedRectangle(cornerRadius: 14).stroke(Theme.hairline))
    }

    private func chartNote(_ bars: [RunBar], scale: Int) -> String {
        if bars.isEmpty { return "還沒有整理紀錄。" }
        var note = "最近 \(bars.count) 次有讀對話的整理。"
        if bars.contains(where: { $0.count > scale }) {
            note += "灰色的那次遠多於其他次，長條已截短。"
        }
        return note
    }

    private func height(_ count: Int, scale: Int) -> CGFloat {
        let share = min(Double(count) / Double(scale), 1)
        return max(CGFloat(share) * 170, 4)
    }

    private func localDayShort(_ value: String) -> String {
        String(localTime(value).split(separator: " ").first ?? "")
    }

    private var topProjects: some View {
        let top = projects.filter { $0.active > 0 }.sorted { $0.active > $1.active }.prefix(6)
        let most = max(top.first?.active ?? 1, 1)
        return VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .firstTextBaseline) {
                Text("記憶最多的專案").font(.system(size: 15, weight: .semibold))
                Spacer()
                Button("看全部") { app.pane = .memory }
                    .buttonStyle(.plain)
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundColor(Theme.accent)
            }
            if top.isEmpty {
                Text("還沒有記憶。").font(.system(size: 13)).foregroundColor(Theme.secondary)
            }
            ForEach(Array(top)) { project in
                VStack(alignment: .leading, spacing: 4) {
                    HStack {
                        Text(project.name).font(.system(size: 14)).lineLimit(1)
                        Spacer()
                        Text(String(project.active)).font(.system(size: 14, weight: .semibold))
                    }
                    GeometryReader { box in
                        ZStack(alignment: .leading) {
                            Capsule().fill(Theme.neutralTint)
                            Capsule().fill(Theme.accent)
                                .frame(
                                    width: max(
                                        box.size.width * CGFloat(project.active) / CGFloat(most), 6))
                        }
                    }
                    .frame(height: 8)
                }
                .help(project.project)
            }
            Spacer(minLength: 0)
        }
        .padding(20)
        .frame(width: 340, alignment: .topLeading)
        .frame(maxHeight: .infinity, alignment: .topLeading)
        .overlay(RoundedRectangle(cornerRadius: 14).stroke(Theme.hairline))
    }

    @MainActor
    private func load() async {
        loading = true
        defer { loading = false }
        do {
            let backend = Backend.current()
            overview = try await backend.overview()
            projects = try await backend.projects()
            runs = try await backend.runs(limit: 60)
            app.lastRunAt = overview?.lastRun?.finishedAt
            message = nil
        } catch {
            message = error.localizedDescription
        }
    }
}

/// A bar: round at the top, flat on the baseline.
private struct UnevenTop: Shape {
    func path(in rect: CGRect) -> Path {
        let radius = min(6, rect.height / 2, rect.width / 2)
        var path = Path()
        path.move(to: CGPoint(x: rect.minX, y: rect.maxY))
        path.addLine(to: CGPoint(x: rect.minX, y: rect.minY + radius))
        path.addQuadCurve(
            to: CGPoint(x: rect.minX + radius, y: rect.minY),
            control: CGPoint(x: rect.minX, y: rect.minY))
        path.addLine(to: CGPoint(x: rect.maxX - radius, y: rect.minY))
        path.addQuadCurve(
            to: CGPoint(x: rect.maxX, y: rect.minY + radius),
            control: CGPoint(x: rect.maxX, y: rect.minY))
        path.addLine(to: CGPoint(x: rect.maxX, y: rect.maxY))
        path.closeSubpath()
        return path
    }
}
