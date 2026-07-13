import SwiftUI

/// GitHub-style contribution heatmap for meeting activity: 7 rows (days
/// Mon–Sun) × ~53 columns (weeks), one small square per day, colour intensity
/// bucketed by the number of meetings recorded that day. Hover a square for the
/// day's stats. Driven entirely by `viewModel.meetingActivityByDay` (Tier-1,
/// in-memory) — Tier-2 stats (speakers / action items) appear in the tooltip
/// once they're populated.
struct MeetingHeatmapView: View {
    @ObservedObject var viewModel: HiDockViewModel
    /// NOT @ObservedObject on purpose: the matrix updates ~20×/s while the
    /// conveyor scrolls, and only the small LED Canvas (LEDMatrixView, which
    /// observes it) should redraw — never this parent (which would re-render the
    /// heatmap/labels and cost real CPU). We just hand it down.
    var ledMatrix: LEDMatrix
    @ObservedObject var ledSettings: LEDSettings

    /// Day the pointer is currently over — drives the always-visible detail
    /// line (more reliable + immediate than the native `.help()` tooltip on
    /// 11px cells, which it supplements).
    @State private var hoveredDate: Date? = nil
    @State private var showLEDSettings = false
    /// True while the LED is scrolling back home after an off-tap — the mode flag
    /// stays true until the animation finishes, so use this to flip the button to
    /// its "off" look immediately for feedback.
    @State private var ledWindingDown = false

    private let cell: CGFloat = 11
    private let gap: CGFloat = 3
    private let weeksBack = 52        // history depth (columns before the current week)
    /// One full week ahead of "today's week" so any meeting pixel always has a
    /// grey neighbour to its right (current-week remainder + next week).
    private let weeksForward = 1

    /// Sunday-first calendar (rows read Sun→Sat, top→bottom).
    private var calendar: Calendar {
        var c = Calendar.current
        c.firstWeekday = 1
        return c
    }

    // MARK: Grid model

    /// Columns of weeks (oldest → newest); each week is 7 day-dates (Sun→Sat).
    /// Always includes all 7 days of the current week *and* one full week into
    /// the future — future days render as empty grey cells so (a) the current
    /// column is a full 7-pixel bar rather than "hanging" past-only squares,
    /// and (b) any meeting colour always has grey padding to its right.
    private func weekColumns(today: Date) -> [[Date?]] {
        let cal = calendar
        // Anchor on today's week; range runs weeksBack behind → weeksForward ahead.
        let thisWeekStart = cal.dateInterval(of: .weekOfYear, for: today)?.start ?? today
        guard let firstWeekStart = cal.date(byAdding: .weekOfYear, value: -weeksBack, to: thisWeekStart) else {
            return []
        }
        let lastCol = weeksBack + weeksForward   // e.g. 52 + 1 → col 53 is next week
        var columns: [[Date?]] = []
        for col in 0...lastCol {
            guard let weekStart = cal.date(byAdding: .weekOfYear, value: col, to: firstWeekStart) else { continue }
            var days: [Date?] = []
            for d in 0..<7 {
                if let day = cal.date(byAdding: .day, value: d, to: weekStart) {
                    days.append(cal.startOfDay(for: day))
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

    private let weekdayCol = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]

    var body: some View {
        let today = calendar.startOfDay(for: Date())
        let columns = weekColumns(today: today)
        let activity = viewModel.meetingActivityByDay
        let labels = monthLabels(columns)

        // Show the LED ticker instead of the grid when the user toggled LED
        // mode, or when an event is "taking over" the heatmap briefly.
        // LED mode is an explicit toggle now; the conveyor runs constantly while
        // shown, so there's no isActive/takeover to observe.
        let showLED = ledSettings.enabled && viewModel.heatmapLEDMode

        return VStack(alignment: .leading, spacing: 6) {
            header
            // The detail line renders in BOTH modes: it disappearing was half
            // of the visible "jump" when the LED view loaded (everything below
            // shifted up by one caption row). In LED mode it still shows the
            // locked day filter + Clear button.
            detailLine(activity: activity, ledMode: showLED)
            if showLED {
                // Keep the calendar chrome — month labels on top, Mon–Sun
                // labels down the left — and drive only the grid with the LED
                // ticker (text in the Tue–Sat band). Geometry must mirror the
                // heatmap exactly (same trailing weeks, same label positions,
                // heatmap day colours as the unlit dots) so the switch doesn't
                // visibly move or drop any pixels.
                ledPanel(columns: columns, labels: labels, activity: activity)
            } else {
                // Sticky weekday gutter + horizontally scrolling year grid.
                // Gutter must stay outside the ScrollView so Sun–Sat don't
                // slide off when the window is narrow / scrolled to trailing.
                // ScrollView is width-constrained so the full year can't force
                // the window wider than the available column.
                heatmapGrid(columns: columns, labels: labels, activity: activity)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    /// Static heatmap: pinned weekday labels + scrollable month labels / cells.
    private func heatmapGrid(columns: [[Date?]], labels: [String?], activity: [Date: DayActivity]) -> some View {
        HStack(alignment: .top, spacing: gap) {
            VStack(alignment: .leading, spacing: gap) {
                Color.clear.frame(width: 30, height: 11)   // align under month-label row
                weekdayGutter
            }
            ScrollView(.horizontal, showsIndicators: false) {
                ScrollViewReader { proxy in
                    VStack(alignment: .leading, spacing: gap) {
                        monthLabelRow(labels, includeGutterPad: false)
                        gridRow(columns: columns, activity: activity)
                    }
                    .onAppear { proxy.scrollTo(columns.count - 1, anchor: .trailing) }
                }
            }
            // Critical: don't let the year's ideal width expand the parent —
            // take only the space the window offers, scroll for the rest.
            .frame(minWidth: 0, maxWidth: .infinity, alignment: .leading)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    // MARK: LED panel

    /// LED mode with heatmap-identical geometry: shows the trailing weeks that
    /// fit (what the heatmap's scrolled-to-trailing view shows), positions the
    /// month labels for exactly those weeks, and feeds the day colours in as
    /// the LED grid's unlit state.
    private func ledPanel(columns: [[Date?]], labels: [String?], activity: [Date: DayActivity]) -> some View {
        let pitch = cell + gap
        let gridHeight = CGFloat(7) * pitch - gap
        let panelHeight = 11 + gap + gridHeight   // month row + spacing + grid
        return GeometryReader { geo in
            let ledWidth = geo.size.width - 30 - gap          // minus weekday gutter
            let fitCols = max(8, Int((ledWidth + gap) / pitch))
            let cols = min(labels.count, fitCols)
            // When the full year doesn't fit, the heatmap sits scrolled to
            // trailing (right edge flush). Right-align the LED grid the same
            // way so the dots occupy identical positions in both modes.
            let leadPad = cols < labels.count ? max(0, ledWidth - (CGFloat(cols) * pitch - gap)) : 0
            let visLabels = Array(labels.suffix(cols))
            let visColumns = Array(columns.suffix(cols))
            VStack(alignment: .leading, spacing: gap) {
                monthLabelRow(visLabels)
                    .padding(.leading, leadPad)
                HStack(alignment: .top, spacing: gap) {
                    weekdayGutter
                    LEDMatrixView(
                        matrix: ledMatrix,
                        fixedCols: cols,
                        heatmap: ledColumns(columns: visColumns, activity: activity)
                    )
                    .padding(.leading, leadPad)
                }
            }
        }
        .frame(height: panelHeight)
    }

    /// The heatmap as LED columns — one per visible week, each with a colour per
    /// day-row: green at the day's meeting-volume intensity, or nil (off) for
    /// empty days (including future days in the current week and the whole next
    /// week, which stay grey so meeting colours always have padding to the
    /// right). This is the engine's resting content (and what scrolls off/on
    /// around a message). Not dimmed — full heatmap colours.
    private func ledColumns(columns: [[Date?]], activity: [Date: DayActivity]) -> [LEDColumn] {
        columns.map { week in
            LEDColumn(cells: week.map { date -> Color? in
                guard let d = date, let a = activity[d], a.count > 0 else { return nil }
                return fill(level(a.count))
            })
        }
    }

    /// Progressively drops the legend / title text so the row never forces the
    /// window wider than the available column (narrow-window overflow fix).
    private var header: some View {
        ViewThatFits(in: .horizontal) {
            headerRow(showTitleText: true, showLegend: true)
            headerRow(showTitleText: true, showLegend: false)
            headerRow(showTitleText: false, showLegend: false)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func headerRow(showTitleText: Bool, showLegend: Bool) -> some View {
        HStack(spacing: 10) {
            HStack(spacing: 6) {
                Image(systemName: "calendar")
                    .font(.caption2)
                    .foregroundColor(.secondary)
                if showTitleText {
                    Text("Meeting activity")
                        .font(.caption.weight(.semibold))
                        .foregroundColor(.secondary)
                        .lineLimit(1)
                }
            }
            .help("Meeting activity")
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
            .layoutPriority(1)
            .help("Recorded = when meetings happened. Transcribed = when they were transcribed.")
            if showLegend { legend }
            ledControls
                .layoutPriority(1)
            Spacer(minLength: 0)
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
                        .truncationMode(.tail)
                }
                .frame(minWidth: 0)
                .layoutPriority(-1)
            }
        }
    }

    /// LED ticker controls in the header: a heatmap↔LED toggle and a settings
    /// gear (popover). The toggle persists as the default view.
    @ViewBuilder private var ledControls: some View {
        HStack(spacing: 6) {
            if ledSettings.enabled {
                let ledOn = viewModel.heatmapLEDMode && !ledWindingDown
                Button {
                    hoveredDate = nil   // hover is inert in LED mode; don't pin a stale day
                    if viewModel.heatmapLEDMode && !ledWindingDown {
                        // Turning LED off: flip the button to its "off" look now for
                        // instant feedback, but let the conveyor scroll back to the
                        // resting heatmap before swapping to the static grid — no jump.
                        ledWindingDown = true
                        ledMatrix.returnHomeThenStop {
                            viewModel.heatmapLEDMode = false
                            ledWindingDown = false
                            ledSettings.defaultView = .heatmap
                        }
                    } else if !viewModel.heatmapLEDMode {
                        viewModel.heatmapLEDMode = true
                        ledWindingDown = false
                        ledSettings.defaultView = .led
                    }
                } label: {
                    Image(systemName: ledOn ? "rectangle.grid.1x2.fill" : "lightbulb")
                        .font(.caption2)
                }
                .buttonStyle(.plain)
                .foregroundColor(ledOn ? .accentColor : .secondary)
                .help(ledOn ? "Show the heatmap" : "Show the LED ticker")
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
    private func detailLine(activity: [Date: DayActivity], ledMode: Bool = false) -> some View {
        // Hover gives a transient preview; a clicked day stays locked. Show the
        // hovered day if hovering, else the locked day, else a hint. In LED
        // mode hover/click are inactive, so the hint is dropped — but the row
        // itself always renders (fixed caption height) so toggling modes never
        // shifts the grid below it.
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
                Text(ledMode ? " " : "Hover a day for details · click to filter the list")
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

    private func monthLabelRow(_ labels: [String?], includeGutterPad: Bool = true) -> some View {
        // Position each month label by absolute x-offset (column pitch) rather
        // than constraining it to one cell's width — otherwise "Aug" wraps
        // vertically inside an 11px cell. Labels take their natural width and
        // sit over the (empty) columns following the month's first week.
        // includeGutterPad: when the weekday gutter sits *beside* this row
        // (LED panel, or the old all-in-scroll layout), pad so labels line up
        // with the cells. Heatmap scroll mode keeps the gutter outside and
        // passes false.
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
        .padding(.leading, includeGutterPad ? 30 + gap : 0)
    }

    /// Week columns only — weekday gutter is rendered outside the scroll view.
    private func gridRow(columns: [[Date?]], activity: [Date: DayActivity]) -> some View {
        HStack(alignment: .top, spacing: gap) {
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
