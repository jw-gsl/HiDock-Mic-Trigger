import SwiftUI

/// GitHub-style contribution heatmap for meeting activity: 7 rows (days
/// Mon–Sun) × ~53 columns (weeks), one small square per day, colour intensity
/// bucketed by the number of meetings recorded that day. Hover a square for the
/// day's stats. Driven entirely by `viewModel.meetingActivityByDay` (Tier-1,
/// in-memory) — Tier-2 stats (speakers / action items) appear in the tooltip
/// once they're populated.
struct MeetingHeatmapView: View {
    @ObservedObject var viewModel: HiDockViewModel

    private let cell: CGFloat = 11
    private let gap: CGFloat = 3
    private let weeksBack = 52        // 52 columns back + current week = 53 columns

    /// Monday-first calendar (matches UK weekday convention).
    private var calendar: Calendar {
        var c = Calendar.current
        c.firstWeekday = 2
        return c
    }

    // MARK: Grid model

    /// Columns of weeks (oldest → newest); each week is 7 day-dates (Mon→Sun).
    /// Days after today are nil so the current partial week renders blank cells.
    private func weekColumns(today: Date) -> [[Date?]] {
        let cal = calendar
        let thisWeekStart = cal.dateInterval(of: .weekOfYear, for: today)?.start ?? today
        guard let firstWeekStart = cal.date(byAdding: .weekOfYear, value: -weeksBack, to: thisWeekStart) else {
            return []
        }
        var columns: [[Date?]] = []
        for col in 0...weeksBack {
            guard let weekStart = cal.date(byAdding: .weekOfYear, value: col, to: firstWeekStart) else { continue }
            var days: [Date?] = []
            for d in 0..<7 {
                if let day = cal.date(byAdding: .day, value: d, to: weekStart) {
                    days.append(day > today ? nil : cal.startOfDay(for: day))
                } else {
                    days.append(nil)
                }
            }
            columns.append(days)
        }
        return columns
    }

    /// 0 = none, 1…4 = increasing intensity. Fixed thresholds (meeting counts
    /// per day are small) read more clearly than relative quantiles.
    private func level(_ count: Int) -> Int {
        switch count {
        case 0: return 0
        case 1: return 1
        case 2...3: return 2
        case 4...5: return 3
        default: return 4
        }
    }

    private func fill(_ level: Int) -> Color {
        switch level {
        case 1: return Color.green.opacity(0.28)
        case 2: return Color.green.opacity(0.50)
        case 3: return Color.green.opacity(0.72)
        case 4: return Color.green
        default: return Color.secondary.opacity(0.12)   // empty day
        }
    }

    // MARK: Tooltip

    private func tooltip(day: Date, activity: DayActivity?) -> String {
        let dateStr = Self.tooltipDateFormatter.string(from: day)
        guard let a = activity, a.count > 0 else { return "\(dateStr)\nNo meetings" }
        var lines = [dateStr]
        let mtg = "\(a.count) meeting\(a.count == 1 ? "" : "s")"
        lines.append("\(mtg) · \(Self.formatDuration(a.totalDuration))")
        let dev = a.byDevice.sorted { $0.value != $1.value ? $0.value > $1.value : $0.key < $1.key }
            .map { "\($0.value) on \($0.key)" }
            .joined(separator: " · ")
        if !dev.isEmpty { lines.append(dev) }
        if a.transcribed > 0 || a.summarised > 0 {
            lines.append("\(a.transcribed) transcribed · \(a.summarised) summarised")
        }
        // Tier 2 — only when populated.
        if let sp = a.speakers, let ai = a.actionItems {
            lines.append("—")
            lines.append("\(sp) speaker\(sp == 1 ? "" : "s") · \(ai) action item\(ai == 1 ? "" : "s")")
        }
        return lines.joined(separator: "\n")
    }

    private static let tooltipDateFormatter: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "EEE, d MMM yyyy"
        return f
    }()

    private static func formatDuration(_ seconds: Double) -> String {
        let total = Int(seconds.rounded())
        let h = total / 3600
        let m = (total % 3600) / 60
        if h > 0 { return m > 0 ? "\(h)h \(m)m" : "\(h)h" }
        if m > 0 { return "\(m)m" }
        return "<1m"
    }

    // MARK: Month labels

    /// Month abbreviation to show above a column, or nil. Labels the first
    /// column whose first (Monday) cell falls in a new month.
    private func monthLabels(_ columns: [[Date?]]) -> [String?] {
        var labels: [String?] = []
        var lastMonth = -1
        for week in columns {
            let firstDay = week.compactMap { $0 }.first
            if let d = firstDay {
                let m = calendar.component(.month, from: d)
                if m != lastMonth {
                    labels.append(Self.monthFormatter.string(from: d))
                    lastMonth = m
                } else {
                    labels.append(nil)
                }
            } else {
                labels.append(nil)
            }
        }
        return labels
    }

    private static let monthFormatter: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "MMM"
        return f
    }()

    // MARK: View

    private let weekdayCol = ["Mon", "", "Wed", "", "Fri", "", ""]

    var body: some View {
        let today = calendar.startOfDay(for: Date())
        let columns = weekColumns(today: today)
        let activity = viewModel.meetingActivityByDay
        let labels = monthLabels(columns)

        return VStack(alignment: .leading, spacing: 6) {
            header
            ScrollView(.horizontal, showsIndicators: false) {
                ScrollViewReader { proxy in
                    VStack(alignment: .leading, spacing: gap) {
                        monthLabelRow(labels)
                        gridRow(columns: columns, activity: activity)
                    }
                    .onAppear { proxy.scrollTo(columns.count - 1, anchor: .trailing) }
                }
            }
        }
    }

    private var header: some View {
        HStack(spacing: 6) {
            Image(systemName: "calendar")
                .font(.caption2)
                .foregroundColor(.secondary)
            Text("Meeting activity")
                .font(.caption.weight(.semibold))
                .foregroundColor(.secondary)
            Spacer()
            legend
        }
    }

    private func monthLabelRow(_ labels: [String?]) -> some View {
        HStack(spacing: gap) {
            Spacer().frame(width: 30)   // weekday-label gutter
            ForEach(Array(labels.enumerated()), id: \.offset) { _, label in
                Text(label ?? "")
                    .font(.system(size: 9))
                    .foregroundColor(.secondary)
                    .frame(width: cell, alignment: .leading)
                    .fixedSize()
            }
        }
    }

    private func gridRow(columns: [[Date?]], activity: [Date: DayActivity]) -> some View {
        HStack(alignment: .top, spacing: gap) {
            weekdayGutter
            ForEach(Array(columns.enumerated()), id: \.offset) { colIndex, week in
                weekColumn(week: week, activity: activity).id(colIndex)
            }
        }
    }

    private var weekdayGutter: some View {
        VStack(alignment: .leading, spacing: gap) {
            ForEach(0..<7, id: \.self) { row in
                Text(weekdayCol[row])
                    .font(.system(size: 9))
                    .foregroundColor(.secondary)
                    .frame(width: 30, height: cell, alignment: .leading)
            }
        }
    }

    private func weekColumn(week: [Date?], activity: [Date: DayActivity]) -> some View {
        VStack(spacing: gap) {
            ForEach(0..<7, id: \.self) { row in
                cellView(date: week[row], activity: activity)
            }
        }
    }

    @ViewBuilder
    private func cellView(date: Date?, activity: [Date: DayActivity]) -> some View {
        if let date = date {
            let a = activity[date]
            RoundedRectangle(cornerRadius: 2)
                .fill(fill(level(a?.count ?? 0)))
                .frame(width: cell, height: cell)
                .help(tooltip(day: date, activity: a))
        } else {
            Color.clear.frame(width: cell, height: cell)
        }
    }

    private var legend: some View {
        HStack(spacing: 3) {
            Text("Less").font(.system(size: 9)).foregroundColor(.secondary)
            ForEach(0..<5, id: \.self) { lvl in
                RoundedRectangle(cornerRadius: 2)
                    .fill(fill(lvl))
                    .frame(width: cell, height: cell)
            }
            Text("More").font(.system(size: 9)).foregroundColor(.secondary)
        }
    }
}
