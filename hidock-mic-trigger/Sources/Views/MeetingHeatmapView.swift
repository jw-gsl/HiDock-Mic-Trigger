import SwiftUI

/// GitHub-style contribution heatmap for meeting activity: 7 rows (days
/// Mon–Sun) × ~53 columns (weeks), one small square per day, colour intensity
/// bucketed by the number of meetings recorded that day. Hover a square for the
/// day's stats. Driven entirely by `viewModel.meetingActivityByDay` (Tier-1,
/// in-memory) — Tier-2 stats (speakers / action items) appear in the tooltip
/// once they're populated.
struct MeetingHeatmapView: View {
    @ObservedObject var viewModel: HiDockViewModel
    @ObservedObject var ledMatrix: LEDMatrix
    @ObservedObject var ledSettings: LEDSettings

    /// Day the pointer is currently over — drives the always-visible detail
    /// line (more reliable + immediate than the native `.help()` tooltip on
    /// 11px cells, which it supplements).
    @State private var hoveredDate: Date? = nil
    @State private var showLEDSettings = false

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

    /// "2 on H1 · 1 on Plaud" — extracted with an explicit closure signature so
    /// Swift's type-checker resolves it fast (inline sort+map+join chains with a
    /// ternary were tripping the "unable to type-check in reasonable time" path).
    private func deviceSummary(_ byDevice: [String: Int]) -> String {
        let sorted = byDevice.sorted { (lhs, rhs) -> Bool in
            if lhs.value != rhs.value { return lhs.value > rhs.value }
            return lhs.key < rhs.key
        }
        return sorted.map { "\($0.value) on \($0.key)" }.joined(separator: " · ")
    }

    private func tooltip(day: Date, activity: DayActivity?) -> String {
        let dateStr = Self.tooltipDateFormatter.string(from: day)
        guard let a = activity, a.count > 0 else { return "\(dateStr)\nNo meetings" }
        var lines: [String] = [dateStr]
        let mtg: String = "\(a.count) meeting\(a.count == 1 ? "" : "s")"
        lines.append(mtg + " · " + Self.formatDuration(a.totalDuration))
        let dev: String = deviceSummary(a.byDevice)
        if !dev.isEmpty { lines.append(dev) }
        if a.transcribed > 0 || a.summarised > 0 {
            lines.append("\(a.transcribed) transcribed · \(a.summarised) summarised")
        }
        // Speakers (from transcripts) and action items (from summaries) shown
        // independently — action items are sparse (one per summarised meeting).
        var extra: [String] = []
        if let sp = a.speakers, sp > 0 { extra.append("\(sp) speaker\(sp == 1 ? "" : "s")") }
        if let ai = a.actionItems, ai > 0 { extra.append("\(ai) action item\(ai == 1 ? "" : "s")") }
        if !extra.isEmpty {
            lines.append("—")
            lines.append(extra.joined(separator: " · "))
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

    private let weekdayCol = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

    var body: some View {
        let today = calendar.startOfDay(for: Date())
        let columns = weekColumns(today: today)
        let activity = viewModel.meetingActivityByDay
        let labels = monthLabels(columns)

        // Show the LED ticker instead of the grid when the user toggled LED
        // mode, or when an event is "taking over" the heatmap briefly.
        let showLED = ledSettings.enabled
            && (viewModel.heatmapLEDMode || (ledSettings.eventTakeover && ledMatrix.isActive))

        return VStack(alignment: .leading, spacing: 6) {
            header
            if showLED {
                // Keep the calendar chrome — month labels on top, Mon–Sun
                // labels down the left — and drive only the grid with the LED
                // ticker (text in the Tue–Sat band).
                VStack(alignment: .leading, spacing: gap) {
                    monthLabelRow(labels)
                    HStack(alignment: .top, spacing: gap) {
                        weekdayGutter
                        LEDMatrixView(matrix: ledMatrix, settings: ledSettings)
                    }
                }
            } else {
                detailLine(activity: activity)
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
    }

    private var header: some View {
        HStack(spacing: 10) {
            HStack(spacing: 6) {
                Image(systemName: "calendar")
                    .font(.caption2)
                    .foregroundColor(.secondary)
                Text("Meeting activity")
                    .font(.caption.weight(.semibold))
                    .foregroundColor(.secondary)
            }
            // Date-mode switch — Recorded (default) vs Transcribed.
            Picker("", selection: $viewModel.heatmapDateMode) {
                ForEach(HiDockViewModel.HeatmapDateMode.allCases) { mode in
                    Text(mode.label).tag(mode)
                }
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .controlSize(.mini)
            .font(.caption)
            .fixedSize()
            .help("Recorded = when meetings happened. Transcribed = when they were transcribed.")
            legend
            ledControls
            Spacer()
            // Refreshing / downloading status lives here now (shows/hides as
            // needed) instead of on its own row — less is more.
            if !viewModel.syncStatus.isEmpty {
                HStack(spacing: 5) {
                    if viewModel.syncBusy || viewModel.syncDownloading {
                        ProgressView().controlSize(.mini)
                    }
                    Text(viewModel.syncStatus)
                        .font(.caption)
                        .foregroundColor(.secondary)
                        .lineLimit(1)
                }
            }
        }
    }

    /// LED ticker controls in the header: a heatmap↔LED toggle and a settings
    /// gear (popover). The toggle persists as the default view.
    @ViewBuilder private var ledControls: some View {
        HStack(spacing: 6) {
            if ledSettings.enabled {
                Button {
                    viewModel.heatmapLEDMode.toggle()
                    ledSettings.defaultView = viewModel.heatmapLEDMode ? .led : .heatmap
                } label: {
                    Image(systemName: viewModel.heatmapLEDMode ? "rectangle.grid.1x2.fill" : "lightbulb")
                        .font(.caption2)
                }
                .buttonStyle(.plain)
                .foregroundColor(viewModel.heatmapLEDMode ? .accentColor : .secondary)
                .help(viewModel.heatmapLEDMode ? "Show the heatmap" : "Show the LED ticker")
            }
            Button { showLEDSettings.toggle() } label: {
                Image(systemName: "gearshape").font(.caption2)
            }
            .buttonStyle(.plain)
            .foregroundColor(.secondary)
            .help("LED ticker settings")
            .popover(isPresented: $showLEDSettings, arrowEdge: .bottom) {
                LEDSettingsView(settings: ledSettings)
            }
        }
    }

    /// Always-visible readout that updates as the pointer moves over the grid.
    /// Shows the exact date for every day, including zero-meeting days.
    private func detailLine(activity: [Date: DayActivity]) -> some View {
        // Hover gives a transient preview; a clicked day stays locked. Show the
        // hovered day if hovering, else the locked day, else a hint.
        let activeDay = hoveredDate ?? viewModel.heatmapSelectedDay
        let locked = viewModel.heatmapSelectedDay
        return HStack(spacing: 8) {
            if let d = activeDay {
                Text(detailText(day: d, activity: activity[d]))
                    .font(.caption)
                    .foregroundColor(.primary)
                    .lineLimit(1)
                    .truncationMode(.tail)
            } else {
                Text("Hover a day for details · click to filter the list")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
            if locked != nil {
                Button {
                    viewModel.heatmapSelectedDay = nil
                } label: {
                    Label("Clear filter", systemImage: "xmark.circle.fill")
                        .font(.caption)
                }
                .buttonStyle(.plain)
                .foregroundColor(.secondary)
                .help("Stop filtering the list to this day")
            }
            Spacer()
        }
    }

    /// Single-line version of the tooltip for the detail readout.
    private func detailText(day: Date, activity a: DayActivity?) -> String {
        let dateStr = Self.tooltipDateFormatter.string(from: day)
        guard let a = a, a.count > 0 else { return "\(dateStr) — no meetings" }
        var parts: [String] = ["\(a.count) meeting\(a.count == 1 ? "" : "s")", Self.formatDuration(a.totalDuration)]
        let dev: String = deviceSummary(a.byDevice)
        if !dev.isEmpty { parts.append(dev) }
        if a.transcribed > 0 || a.summarised > 0 {
            parts.append("\(a.transcribed) transcribed · \(a.summarised) summarised")
        }
        if let sp = a.speakers, sp > 0 { parts.append("\(sp) speaker\(sp == 1 ? "" : "s")") }
        if let ai = a.actionItems, ai > 0 { parts.append("\(ai) action item\(ai == 1 ? "" : "s")") }
        return "\(dateStr) — " + parts.joined(separator: " · ")
    }

    private func monthLabelRow(_ labels: [String?]) -> some View {
        // Position each month label by absolute x-offset (column pitch) rather
        // than constraining it to one cell's width — otherwise "Aug" wraps
        // vertically inside an 11px cell. Labels take their natural width and
        // sit over the (empty) columns following the month's first week.
        let pitch = cell + gap
        return ZStack(alignment: .topLeading) {
            Color.clear.frame(width: CGFloat(labels.count) * pitch, height: 11)
            ForEach(Array(labels.enumerated()), id: \.offset) { idx, label in
                if let label = label {
                    Text(label)
                        .font(.system(size: 9))
                        .foregroundColor(.secondary)
                        .fixedSize()
                        .offset(x: CGFloat(idx) * pitch)
                }
            }
        }
        .padding(.leading, 30 + gap)   // align with the grid's weekday gutter
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
            let hasMeetings = (a?.count ?? 0) > 0
            let selected = viewModel.heatmapSelectedDay == date
            let strokeColor: Color = selected ? .accentColor : (hoveredDate == date ? .primary : .clear)
            let strokeWidth: CGFloat = selected ? 1.5 : (hoveredDate == date ? 1 : 0)
            RoundedRectangle(cornerRadius: 2)
                .fill(fill(level(a?.count ?? 0)))
                .overlay(
                    RoundedRectangle(cornerRadius: 2)
                        .stroke(strokeColor, lineWidth: strokeWidth)
                )
                .frame(width: cell, height: cell)
                .contentShape(Rectangle())
                .help(tooltip(day: date, activity: a))
                .onHover { inside in
                    if inside { hoveredDate = date }
                    else if hoveredDate == date { hoveredDate = nil }
                }
                // Only days with meetings are selectable — clicking an empty
                // day would just clear the filter, which feels pointless.
                .onTapGesture { if hasMeetings { viewModel.toggleHeatmapDay(date) } }
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
