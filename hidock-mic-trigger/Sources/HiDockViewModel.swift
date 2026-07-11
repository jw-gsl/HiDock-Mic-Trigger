import SwiftUI
import Combine

/// Whether the people filter requires a meeting to contain ANY or ALL of the
/// selected people.
enum PeopleFilterMode: String { case any, all }

final class HiDockViewModel: ObservableObject {
    // MARK: - Mic Trigger State
    @Published var triggerRunning = false
    @Published var triggerPID: Int32?
    @Published var triggerUptime: String = ""
    /// True once the CLI has confirmed it found both the watched USB mic
    /// and the HiDock audio device, and is actively polling. Distinct
    /// from `triggerRunning` (process != nil): a process can be up but
    /// stuck in the wait-for-devices loop, in which case the trigger
    /// isn't actually doing anything yet. UI uses this to paint amber
    /// (waiting) vs green (healthy).
    @Published var triggerHealthy = false
    /// Human-readable status while the CLI is waiting for a device to
    /// enumerate (e.g. "Waiting for USB mic 'Samson Q2U Microphone'").
    /// nil when the trigger is healthy or not running.
    @Published var triggerWaitMessage: String?
    /// Wall-clock time of the most recent successful (re)start of the
    /// trigger CLI process. Surfaced in the Mic Trigger row as "↻
    /// 16:23:18" so the user has passive confirmation that an unplug/
    /// replug cycle did in fact bounce the trigger — useful when
    /// notifications get coalesced or missed by macOS.
    @Published var triggerLastStartedAt: Date?
    /// When the HiDock became connected (uptime anchor). The Mic Trigger row
    /// ticks its own uptime label from this via a local TimelineView, so the
    /// per-second update never touches shared view-model state (which would
    /// re-render the whole window). Changes only on connect/disconnect.
    @Published var triggerConnectedSince: Date?
    @Published var selectedMicName: String?
    @Published var autoStartOnLaunch = true
    @Published var availableMics: [String] = []
    /// The mic-trigger's ffmpeg is currently streaming from a HiDock —
    /// meaning the HiDock is actively recording. Set/cleared when the
    /// trigger CLI emits 'IN USE' / 'NOT IN USE' lines. The HiDock's
    /// USB data endpoint is unreachable while this is true, so data
    /// queries will fail until the stream stops.
    @Published var hidockRecordingActive: Bool = false
    /// The CoreAudio device name ffmpeg is currently holding, e.g.
    /// "HiDock H1" or "HiDock P1". Nil when nothing is being held.
    /// Used by DeviceCardView to put the Recording chip on only the
    /// specific HiDock being recorded, not every card.
    @Published var hidockRecordingDeviceName: String? = nil

    // MARK: - Sync State
    @Published var syncStatus: String = "Not loaded"
    @Published var syncStatusLevel: StatusLevel = .secondary
    @Published var syncOutputFolder: String?
    @Published var syncTranscriptFolder: String?
    @Published var syncEntries: [HiDockSyncRecordingEntry] = [] { didSet { markDerivedDirty() } }
    @Published var syncCheckedRecordings: Set<String> = []
    @Published var syncAutoDownload = false
    @Published var syncAutoTranscribe = false
    @Published var syncAutoSummarise = false
    /// How often to poll paired Plaud accounts for new recordings (seconds).
    /// 0 = off. Plaud is an API, so this is the only thing that surfaces new
    /// recordings on it. Changed via the device manager; applied by AppDelegate.
    @Published var plaudPollIntervalSeconds: Double = 120
    var onSetPlaudPollInterval: (Double) -> Void = { _ in }
    /// True when at least one paired device is a Plaud account (gates the
    /// poll-interval control).
    var hasPlaudAccount: Bool { syncPairedDevices.contains { $0.deviceType == .plaud } }
    /// Whether the right-hand embedded CLI pane is shown. Toggled by the
    /// bottom-bar "CLI" button; auto-set true when an Ask Claude Code or a
    /// summarise run starts so the user sees the activity.
    @Published var cliPaneVisible = false
    /// Shared embedded-terminal controller — the SwiftUI pane displays it,
    /// AppDelegate drives it (interactive auth + template authoring).
    let terminalController = TerminalPaneController()

    /// What the right-hand CLI pane currently shows.
    /// - `.summary`: live formatted readout of a summarise/reclassify run
    /// - `.chat`: conversational Ask AI (formatted, multi-turn)
    /// - `.terminal`: raw SwiftTerm shell (auth, template authoring, power use)
    enum CLIPaneMode { case summary, chat, terminal }
    @Published var cliPaneMode: CLIPaneMode = .terminal
    /// Formatted readout for the auto/selected/reclassify summarise flow.
    let summaryTranscript = AgentTranscript()
    /// Formatted conversation for Ask AI.
    let chatTranscript = AgentTranscript()
    /// Title shown above the chat pane (e.g. the recording name).
    @Published var chatTitle: String = "Ask AI"
    /// True while a chat turn is in flight (disables the input box / Send).
    @Published var chatRunning = false
    /// User submitted a chat follow-up — AppDelegate runs the next turn.
    var onSendChat: (String) -> Void = { _ in }
    /// Open the raw terminal pane (for `claude auth login` / power use).
    var onOpenRawTerminal: () -> Void = { }

    // MARK: LED ticker
    /// Persisted LED-ticker settings (shared with the settings popover).
    let ledSettings = LEDSettings()
    /// The LED matrix engine — driven by app events, rendered over the heatmap.
    lazy var ledMatrix = LEDMatrix(settings: ledSettings)
    /// Runtime heatmap ↔ LED toggle (seeded from the persisted default view).
    @Published var heatmapLEDMode: Bool =
        (UserDefaults.standard.string(forKey: "led.defaultView") == "led")
    @Published var mergeGroups: [MergeGroup] = [] { didSet { markDerivedDirty() } }
    @Published var expandedMergeGroups: Set<String> = [] { didSet { markDerivedDirty() } }
    @Published var syncBusy = false
    @Published var syncDownloading = false
    @Published var syncDownloadProgress: String?
    /// Device-side filename of the recording the extractor is currently
    /// pulling (e.g. `2026May06-...hda`). Updated in real time from the
    /// extractor's `FILE_START:` / `FILE_DONE:` stderr markers, so the
    /// recordings table can paint that one row "Downloading" (yellow)
    /// while the rest of the pending batch stays "On device".
    @Published var currentlyDownloadingName: String?
    @Published var syncSortKey: String = "created" { didSet { markDerivedDirty() } }
    @Published var syncSortAscending: Bool = false { didSet { markDerivedDirty() } }
    @Published var syncFilterDeviceId: String? { didSet { markDerivedDirty() } }
    /// Pipeline-stage filter for the recordings table. Defaults to
    /// `.all`. Combined with `syncFilterDeviceId` (AND) and the
    /// user's sort key inside `visibleEntries`.
    /// Multi-select status filter (stackable, like the Hide menu). Empty = show
    /// all. Entries matching ANY selected status pass (OR semantics).
    @Published var statusFilters: Set<SyncStatusFilter> = [] { didSet { markDerivedDirty() } }

    /// recording name → the named people in that meeting (from the diarized
    /// sidecars, refreshed alongside transcription state). Drives the people
    /// filter + the Voice Library "# meetings" count.
    /// True during the initial launch load (cache read) so the list can show a
    /// spinner instead of a blank table until the first combined paint lands.
    @Published var recordingsLoading = false
    @Published var meetingPeople: [String: Set<String>] = [:] { didSet { markDerivedDirty() } }
    /// Active people filter (empty = off). Combined AND with device/status/day.
    @Published var syncFilterPeople: Set<String> = [] { didSet { markDerivedDirty() } }
    /// Whether a meeting must contain ANY or ALL of the filtered people.
    @Published var syncPeopleFilterMode: PeopleFilterMode = .any { didSet { markDerivedDirty() } }

    /// Every named person seen across meetings, sorted — for the filter menu.
    var allPeople: [String] {
        Array(Set(meetingPeople.values.flatMap { $0 })).sorted { $0.localizedCaseInsensitiveCompare($1) == .orderedAscending }
    }
    /// person name → number of meetings they appear in.
    var personMeetingCounts: [String: Int] {
        var counts: [String: Int] = [:]
        for people in meetingPeople.values {
            for p in people { counts[p, default: 0] += 1 }
        }
        return counts
    }

    /// Whether an entry matches a given status filter.
    func matchesStatusFilter(_ e: HiDockSyncRecordingEntry, _ f: SyncStatusFilter) -> Bool {
        switch f {
        case .all: return true
        case .onDevice: return e.statusText == "On device"
        case .downloaded: return e.statusText == "Downloaded"
        case .untranscribed:
            return e.recording.localExists && !e.transcribed && !e.transcriptionSkipped
        case .transcribed: return e.transcribed
        case .summarised: return e.statusText == "Summarised"
        case .skipped: return e.statusText == "Skipped"
        case .removed: return e.statusText == "Removed"
        case .failed: return e.statusText == "Failed"
        case .imported: return e.deviceId == "imported:local"
        case .merged:
            return mergeGroups.contains { $0.childNames.contains(e.recording.name) }
        }
    }

    /// Toggle a status in/out of the multi-select Filter set.
    func toggleStatusFilter(_ f: SyncStatusFilter) {
        if statusFilters.contains(f) { statusFilters.remove(f) }
        else { statusFilters.insert(f) }
    }
    /// Optional filter by summary classification type (e.g. "Brainstorming").
    /// nil = all types. AND-ed with the status + device filters.
    @Published var summaryTypeFilter: String? = nil { didSet { markDerivedDirty() } }
    /// Statuses the user has chosen to hide via the multiselect "Hide" menu.
    /// Values are statusText strings (see `hideableStatuses`). Sticky across
    /// launches. A hidden status is still shown if the user explicitly picks it
    /// in the Filter dropdown (they clearly want to see it then).
    @Published var hiddenStatuses: Set<String> =
        Set(UserDefaults.standard.stringArray(forKey: "hidockHiddenStatuses") ?? []) {
        didSet {
            UserDefaults.standard.set(Array(hiddenStatuses), forKey: "hidockHiddenStatuses")
            markDerivedDirty()
        }
    }

    /// The statuses the "Hide" menu offers — the user-driven "already actioned"
    /// terminal states. Deliberately excludes Failed (you want failures visible)
    /// and pipeline stages (that's the Filter dropdown's job).
    static let hideableStatuses = ["Skipped", "Removed"]

    /// How many recordings are currently at a given status, across ALL entries
    /// (ignores the Hide filter so a hidden status still reports its true
    /// count). Drives the "Skipped (N)" / "Removed (N)" counts in the Hide menu.
    func statusCount(_ status: String) -> Int {
        syncEntries.filter { $0.statusText == status }.count
    }

    /// Toggle a status in/out of the hidden set (drives the Hide menu).
    func toggleHidden(_ status: String) {
        if hiddenStatuses.contains(status) {
            hiddenStatuses.remove(status)
        } else {
            hiddenStatuses.insert(status)
        }
    }
    @Published var syncPairedDevices: [HiDockPairedDevice] = []
    @Published var syncDeviceConnected: [String: Bool] = [:]
    @Published var syncPaired = false

    // MARK: - Local-file Operation State
    /// True while an ffmpeg trim is in flight. Separate from
    /// `syncBusy` so a device still probing / connecting doesn't
    /// disable Trim — the operation is purely local-file.
    @Published var trimBusy = false

    // MARK: - Merge candidate detection (split-recording finder)
    /// Chains the extractor flagged as likely-one-conversation. High
    /// confidence ones are styled stronger; the rest sit behind a
    /// "show all" toggle in the merge candidates sheet.
    @Published var mergeCandidates: [MergeCandidate] = [] { didSet { markDerivedDirty() } }
    @Published var mergeCandidatesShowAll = false { didSet { markDerivedDirty() } }
    /// User-visible "Merge candidates (N)" count — hidden when zero.
    var mergeCandidateCountForBadge: Int {
        let pool = effectiveMergeCandidates
        return mergeCandidatesShowAll
            ? pool.count
            : pool.filter(\.high_confidence).count
    }
    /// Set of mp3 paths that appear in at least one currently-surfaced
    /// candidate. Used by the recordings table to draw the subtle
    /// blue left-border accent on those rows.
    var mergeCandidatePaths: Set<String> {
        ensureDerived(); return _mergeCandidatePaths
    }

    private func computeMergeCandidatePaths(_ pool: [MergeCandidate]) -> Set<String> {
        let visible = mergeCandidatesShowAll
            ? pool
            : pool.filter(\.high_confidence)
        return Set(visible.flatMap { $0.pieces.map(\.mp3_path) })
    }
    /// mp3 paths the user has ticked as "yes, include this in a merge".
    /// Multi-select within candidate rows: a 5-piece chain can be
    /// merged as a 3-of-5 subset by ticking only the pieces the user
    /// confirms belong together. Only paths visible in
    /// `mergeCandidatePaths` should ever be added here.
    @Published var mergeCandidatesTicked: Set<String> = []
    /// `true` once the user has ticked enough candidate rows to do a
    /// merge — drives the "Merge N selected" action's visibility.
    var canMergeTickedCandidates: Bool { mergeCandidatesTicked.count >= 2 }

    /// Candidates after filtering out chains where any piece is
    /// already a child of an active merge group. The visual heuristic
    /// is "if the row already shows the merge-triangle icon next to
    /// the folder, it's been merged — don't keep suggesting it." This
    /// double-defends against any race where the dismiss didn't
    /// propagate (state update lag, scan hadn't refreshed yet) by
    /// using the existing merge-groups state as the authoritative
    /// "already merged" signal. Used everywhere a candidate would
    /// otherwise surface (row tint, tick toggle, toolbar count,
    /// scroll target, right-click menu).
    var effectiveMergeCandidates: [MergeCandidate] {
        ensureDerived(); return _effectiveMergeCandidates
    }

    private func computeEffectiveMergeCandidates() -> [MergeCandidate] {
        let mergedStems = Set(mergeGroups.flatMap(\.childNames)
            .map { ($0 as NSString).deletingPathExtension })
        return mergeCandidates.filter { cand in
            let chainStems = cand.pieces.map {
                ($0.mp3_name as NSString).deletingPathExtension
            }
            return !chainStems.contains(where: { mergedStems.contains($0) })
        }
    }
    /// Path of the first candidate row, used by the toolbar's count
    /// label to scroll the table to the first suggestion when clicked.
    var firstMergeCandidatePath: String? {
        let pool = effectiveMergeCandidates
        let visible = mergeCandidatesShowAll
            ? pool
            : pool.filter(\.high_confidence)
        return visible.first?.pieces.first?.mp3_path
    }

    var onScanMergeCandidates: () -> Void = {}
    var onMergeCandidate: (MergeCandidate) -> Void = { _ in }
    var onDismissMergeCandidate: (MergeCandidate) -> Void = { _ in }
    var onMergeTickedCandidates: () -> Void = {}
    /// Increment to ask the recordings table to scroll to the first
    /// candidate row. The table observes this via `.onChange` and
    /// scrolls when it bumps. Counter rather than bool because we
    /// want consecutive clicks to work even when the row is already
    /// visible (a re-bump still triggers `.onChange`).
    @Published var scrollToFirstCandidateTrigger: Int = 0

    // MARK: - Transcription State
    @Published var diarizeEnabled = false
    @Published var transcriptionBusy = false
    @Published var transcriptionCurrentFile: String?
    @Published var transcriptionProgress: Int = 0
    /// Recordings (by outputName) currently being summarised — drives the
    /// transient "Summarising" status pill, mirroring transcriptionCurrentFile.
    @Published var summarisingNames: Set<String> = []
    @Published var transcriptionFileIndex: Int = 0
    @Published var transcriptionFileCount: Int = 0
    @Published var transcriptionStatus: String = ""
    @Published var transcriptionPaused = false
    @Published var transcriptionQueue: [TranscriptionQueueItem] = []
    var onCancelTranscription: () -> Void = {}
    var onShowTranscriptionQueue: () -> Void = {}
    var onRemoveFromQueue: (String) -> Void = { _ in }
    var onMoveInQueue: (Int, Int) -> Void = { _, _ in }
    var onPauseTranscription: () -> Void = {}
    var onResumeTranscription: () -> Void = {}
    var onRediarize: (String, Int?) -> Void = { _, _ in }  // jsonPath, nSpeakers

    // MARK: - Merge-group transcript state

    /// Merge groups don't live in `syncEntries` (which only holds
    /// HiDock + imported recordings), so the per-row `transcribed` /
    /// `speakersTagged` flags never set for them. Tracking the state
    /// per merged-mp3-name here lets mergeTranscriptionIndicator and
    /// `needsTaggingCount` surface merge-rediarize results correctly.
    /// Populated by refreshTranscriptionState after each Python `status`
    /// query — same source of truth as the per-row state.
    @Published var mergedFileTranscribed: Set<String> = [] { didSet { markDerivedDirty() } }
    @Published var mergedFileTagged: Set<String> = []
    /// Merged files where the voice library auto-matched ≥1 speaker but none is
    /// confirmed yet — the "confirm me" state (blue question mark). Mirrors the
    /// per-row `speakersAutoMatched`. Excluded from the "needs tagging" nag.
    @Published var mergedFileAutoMatched: Set<String> = []
    @Published var mergedFileTranscriptPaths: [String: String] = [:]
    /// Merged file mp3 name → its transcript mtime (when it was transcribed).
    /// Used for the heatmap's Transcribed date-mode so a merged meeting buckets
    /// on the date its merged transcript was produced.
    @Published var mergedFileTranscribedDates: [String: Date] = [:]
    /// Per-recording speaker / action-item counts (mp3 name → counts) parsed
    /// from transcript frontmatter via `transcribe.py activity-stats`, fetched
    /// once on load. Feeds the heatmap Tier-2 tooltip (shown when present).
    @Published var meetingExtraStats: [String: (speakers: Int, actionItems: Int)] = [:] { didSet { markDerivedDirty() } }

    // MARK: - Computed

    /// True if any currently-checked recording has been locally
    /// trimmed. Drives the Download Selected button's label flip to
    /// "Re-download Selected" so the user sees what the click will
    /// actually do (overwrite the trimmed local file with the
    /// device original).
    var selectionIncludesTrimmed: Bool {
        syncEntries.contains { entry in
            syncCheckedRecordings.contains(entry.recording.name)
                && (entry.recording.trimmed ?? false)
        }
    }

    /// Paths whose last transcription attempt ended in failure or was
    /// cancelled. Used by the tag column to show an X icon so the user
    /// can tell at a glance which rows need a retry, and by bulk-select
    /// flows that want to surface "N recordings need a retry".
    var failedTranscriptionPaths: Set<String> {
        Set(transcriptionQueue
            .filter { $0.status == .failed || $0.status == .cancelled }
            .map(\.path))
    }

    /// Look up the captured error / cancellation message for a path.
    /// Returns nil for queued/transcribing/completed items. The view
    /// layer hands this to an alert when the red X is clicked.
    func transcriptionErrorMessage(for path: String) -> String? {
        transcriptionQueue.first(where: { $0.path == path })?.errorMessage
    }

    /// Distinct summary classification types present across all recordings,
    /// for the Type filter menu. Empty until something has been summarised.
    var summaryTypeOptions: [String] {
        Array(Set(syncEntries.compactMap { $0.summaryType })).sorted()
    }

    /// Entries after the device / status / summary-type / Hide filters, but
    /// BEFORE the heatmap day-filter and sort. The heatmap is built from this
    /// (so selecting a day doesn't collapse the grid to that one day), while
    /// `visibleEntries` additionally applies the selected-day filter.
    // MARK: - Derived-list cache
    // These lists are O(n log n) over `syncEntries` (now ~1700+ after the
    // HiNotes migration) and were previously recomputed on *every* SwiftUI
    // render — `filteredEntriesNoDay` ran twice per render (visibleEntries +
    // meetingActivityByDay), each a full sort. That pegged the main thread and
    // made the app beachball. Now they're memoised: computed once when an input
    // changes (derivedDirty set by the inputs' didSet) and cached for reads.
    private var _filteredEntriesNoDay: [HiDockSyncRecordingEntry] = []
    private var _visibleEntries: [HiDockSyncRecordingEntry] = []
    private var _displayRows: [DisplayRow] = []
    private var _meetingActivityByDay: [Date: DayActivity] = [:]
    private var _effectiveMergeCandidates: [MergeCandidate] = []
    private var _mergeCandidatePaths: Set<String> = []
    private var _mergeCandidatesByPath: [String: [MergeCandidate]] = [:]
    private var derivedDirty = true

    /// Recompute the derived lists once if any input changed. Synchronous, so
    /// reads are always fresh; the dirty flag makes it a no-op between changes.
    private func ensureDerived() {
        guard derivedDirty else { return }
        derivedDirty = false
        let filtered = computeFilteredEntriesNoDay()
        let visible = computeVisibleEntries(filtered)
        _filteredEntriesNoDay = filtered
        _visibleEntries = visible
        _displayRows = computeDisplayRows(visible)
        _meetingActivityByDay = computeMeetingActivity(filtered)
        let eff = computeEffectiveMergeCandidates()
        _effectiveMergeCandidates = eff
        _mergeCandidatePaths = computeMergeCandidatePaths(eff)
        _mergeCandidatesByPath = computeMergeCandidatesByPath(eff)
    }

    /// The visible candidates that include each mp3 path — so the per-row
    /// context menu is an O(1) lookup instead of an O(candidates) filter.
    private func computeMergeCandidatesByPath(_ pool: [MergeCandidate]) -> [String: [MergeCandidate]] {
        let visible = mergeCandidatesShowAll ? pool : pool.filter(\.high_confidence)
        var map: [String: [MergeCandidate]] = [:]
        for cand in visible {
            for piece in cand.pieces {
                map[piece.mp3_path, default: []].append(cand)
            }
        }
        return map
    }

    /// Visible merge candidates that include `path` (cached; O(1)).
    func mergeCandidates(forPath path: String) -> [MergeCandidate] {
        ensureDerived(); return _mergeCandidatesByPath[path] ?? []
    }

    /// Mark the derived lists stale. Cheap (a bool); the recompute is deferred
    /// to the next read. Called from the inputs' `didSet`.
    func markDerivedDirty() { derivedDirty = true }

    private var filteredEntriesNoDay: [HiDockSyncRecordingEntry] {
        ensureDerived(); return _filteredEntriesNoDay
    }

    private func computeFilteredEntriesNoDay() -> [HiDockSyncRecordingEntry] {
        var entries = syncEntries
        if let filterDeviceId = syncFilterDeviceId {
            entries = entries.filter {
                $0.deviceId == filterDeviceId
            }
        }
        // People filter (AND-ed with the others). ANY = meeting includes at least
        // one of the selected people; ALL = includes every selected person.
        if !syncFilterPeople.isEmpty {
            entries = entries.filter { e in
                let people = meetingPeople[e.recording.name] ?? []
                switch syncPeopleFilterMode {
                case .any: return !people.isDisjoint(with: syncFilterPeople)
                case .all: return syncFilterPeople.isSubset(of: people)
                }
            }
        }
        // (Hide Downloaded toggle removed in 2026-04-26 cleanup —
        // it was a strict subset of the Filter dropdown's "On device"
        // / "Untranscribed" options, so keeping both was redundant.)
        //
        // Multi-select status filter (stackable). An entry passes if it matches
        // ANY selected status (OR). Empty set = no filtering. Evaluated on the
        // same statusText cascade the table renders.
        let activeStatusFilters = statusFilters.subtracting([.all])
        if !activeStatusFilters.isEmpty {
            entries = entries.filter { e in
                activeStatusFilters.contains { matchesStatusFilter(e, $0) }
            }
        }
        // Summary-type filter (AND-ed with the above). Only summarised rows
        // carry a type, so a non-nil filter implicitly hides un-summarised rows.
        if let type = summaryTypeFilter {
            entries = entries.filter { $0.summaryType == type }
        }
        // "Hide" menu (multiselect). Drop rows whose status the user chose to
        // hide — but never hide a status they've explicitly selected in the
        // Filter menu (they clearly want to see it then).
        if !hiddenStatuses.isEmpty {
            let explicitlyFiltered = Set(activeStatusFilters.map { $0.label })
            entries = entries.filter { e in
                if explicitlyFiltered.contains(e.statusText) { return true }
                return !hiddenStatuses.contains(e.statusText)
            }
        }
        return entries
    }

    var visibleEntries: [HiDockSyncRecordingEntry] {
        ensureDerived(); return _visibleEntries
    }

    private func computeVisibleEntries(_ filtered: [HiDockSyncRecordingEntry]) -> [HiDockSyncRecordingEntry] {
        var entries = filtered
        // Heatmap day-filter: when a day square is locked, the table narrows to
        // that day (the heatmap grid itself keeps using filteredEntriesNoDay).
        if let day = heatmapSelectedDay {
            entries = entries.filter { recordingDay($0.recording) == day }
        }
        entries.sort { lhs, rhs in
            // Descending order swaps the operands instead of negating the
            // ascending result — `!result` returns true for BOTH (a,b) and
            // (b,a) when keys compare equal (very common for Status), which
            // violates sort's strict-weak-ordering requirement (undefined
            // behavior / unstable row order).
            let a = syncSortAscending ? lhs : rhs
            let b = syncSortAscending ? rhs : lhs
            let ar = a.recording, br = b.recording
            switch syncSortKey {
            case "status":
                return a.statusText.localizedCaseInsensitiveCompare(b.statusText) == .orderedAscending
            case "name":
                return ar.outputName.localizedCaseInsensitiveCompare(br.outputName) == .orderedAscending
            case "created":
                return Self.createdSortKey(ar) < Self.createdSortKey(br)
            case "transcribed":
                // Untranscribed (nil) sort to the bottom in the default
                // (descending) order via distantPast.
                return (a.transcribedDate ?? .distantPast) < (b.transcribedDate ?? .distantPast)
            case "duration":
                return ar.duration < br.duration
            case "size":
                return ar.length < br.length
            case "path":
                return ar.outputPath.localizedCaseInsensitiveCompare(br.outputPath) == .orderedAscending
            case "device":
                return a.deviceName.localizedCaseInsensitiveCompare(b.deviceName) == .orderedAscending
            default:
                return Self.createdSortKey(ar) < Self.createdSortKey(br)
            }
        }
        return entries
    }

    /// A chronologically-comparable key for the "created" sort, robust to a
    /// missing `createDate`. HiDock/Plaud/volume all emit createDate as
    /// "yyyy/MM/dd" + createTime "HH:MM:SS" → digits "yyyymmddhhmmss". When
    /// createDate is empty (e.g. a Plaud cached-status paint), fall back to the
    /// leading "yyyy-MM-dd HH-mm-ss" timestamp in the filename — otherwise those
    /// rows sorted to the bottom and newer Plaud meetings dropped below older
    /// HiDock ones on launch.
    private static func createdSortKey(_ rec: HiDockSyncRecording) -> String {
        let cd = rec.createDate.trimmingCharacters(in: .whitespaces)
        if !cd.isEmpty {
            return (cd + rec.createTime).filter { $0.isNumber }
        }
        let candidate = rec.outputName.isEmpty ? rec.name : rec.outputName
        let digits = candidate.prefix(19).filter { $0.isNumber }
        // Pad so a partial fallback still orders sensibly against full keys.
        return digits.isEmpty ? "" : String(digits)
    }

    /// Build the display list with merge groups expanded/collapsed
    var displayRows: [DisplayRow] {
        ensureDerived(); return _displayRows
    }

    private func computeDisplayRows(_ visible: [HiDockSyncRecordingEntry]) -> [DisplayRow] {
        let entries = visible
        // Collect all child names across all merge groups
        let allChildNames = Set(mergeGroups.flatMap(\.childNames))

        var rows: [DisplayRow] = []
        var insertedGroups = Set<String>()

        for entry in entries {
            // Check if this entry is a child of a merge group
            if let group = mergeGroups.first(where: { $0.childNames.contains(entry.recording.name) }) {
                // Insert the merge parent row at the position of the first child we encounter
                if !insertedGroups.contains(group.id) {
                    insertedGroups.insert(group.id)
                    rows.append(.mergeParent(group))
                }
                // Show child only if the group is expanded
                if expandedMergeGroups.contains(group.id) {
                    rows.append(.mergeChild(entry))
                }
            } else {
                rows.append(.recording(entry))
            }
        }

        return rows
    }

    var hasSelection: Bool {
        !syncCheckedRecordings.isEmpty
    }

    var anySelectedMarkedOnly: Bool {
        // Unskip applies to either:
        //  1. download-skipped (downloaded flag set but file absent), or
        //  2. transcription-skipped (user opted out of transcribing a
        //     locally-present recording).
        guard hasSelection else { return false }
        return syncEntries.contains {
            guard syncCheckedRecordings.contains($0.recording.name) else { return false }
            let downloadSkipped = $0.recording.downloaded && !$0.recording.localExists
            return downloadSkipped || $0.transcriptionSkipped
        }
    }

    var needsTaggingCount: Int {
        // Only true "needs tagging" rows nag: transcribed, multi-speaker, and
        // neither confirmed (tagged) nor auto-matched. Auto-matched meetings
        // ("confirm me") are surfaced by the blue question-mark icon, not the pill.
        let perRow = syncEntries.filter {
            $0.transcribed && !$0.speakersTagged && !$0.speakersAutoMatched
        }.count
        // Same for merged-file rows (they don't live in syncEntries).
        let perMerge = mergedFileTranscribed
            .subtracting(mergedFileTagged)
            .subtracting(mergedFileAutoMatched)
            .count
        return perRow + perMerge
    }

    var syncSummary: String {
        let visible = visibleEntries
        // Count files actually present on disk (localExists), NOT the
        // `downloaded` flag. The flag is set for Skipped recordings too (Skip =
        // downloaded-flag + no local file) and stays set on Removed ones, so a
        // flag-based count was misleading — "downloaded" now means "I have the
        // file", which is what the user expects. Imported files count (they're
        // on disk); Skipped/Removed/On-device don't.
        let downloadedCount = syncEntries.filter(\.recording.localExists).count
        let selectedCount = syncCheckedRecordings.count
        var parts = ["\(visible.count) shown", "\(syncEntries.count) total", "\(downloadedCount) downloaded"]
        if selectedCount > 0 {
            parts.append("\(selectedCount) selected")
        }
        return parts.joined(separator: " · ")
    }

    // MARK: - Meeting activity heatmap

    /// Parser for the extractor's `createDate` ("yyyy/MM/dd", emitted for every
    /// device type — HiDock, Plaud, volume — in the resolved status item).
    private static let recordingDayFormatter: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "yyyy/MM/dd"
        return f
    }()

    /// Fallback parser for a leading "yyyy-MM-dd" in name/outputName.
    private static let recordingISODayFormatter: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "yyyy-MM-dd"
        return f
    }()

    /// The calendar day a recording belongs to (start-of-day, local), or nil if
    /// no date can be recovered. Prefers `createDate`; falls back to a leading
    /// ISO date in outputName/name.
    func recordingDay(_ rec: HiDockSyncRecording) -> Date? {
        let cd = rec.createDate.trimmingCharacters(in: .whitespaces)
        if !cd.isEmpty, let d = Self.recordingDayFormatter.date(from: cd) {
            return Calendar.current.startOfDay(for: d)
        }
        let candidate = rec.outputName.isEmpty ? rec.name : rec.outputName
        if candidate.count >= 10,
           let d = Self.recordingISODayFormatter.date(from: String(candidate.prefix(10))) {
            return Calendar.current.startOfDay(for: d)
        }
        return nil
    }

    /// Short device label for the heatmap tooltip ("H1", "P1", "Plaud",
    /// "Imported", or a cleaned volume name).
    func deviceShortLabel(for entry: HiDockSyncRecordingEntry) -> String {
        if entry.deviceId.hasPrefix("plaud:") { return "Plaud" }
        if entry.deviceId == "imported:local" { return "Imported" }
        var name = entry.deviceName
        if name.hasPrefix("HiDock ") { name = String(name.dropFirst("HiDock ".count)) }
        return name.isEmpty ? entry.deviceName : name
    }

    /// Per-day aggregated activity (Tier-1 fields only; computed in memory, no
    /// file IO). Mirrors the recordings table: built from `visibleEntries`, so
    /// the heatmap reflects the active device / status / Hide / summary-type
    /// filters.
    /// Day locked by clicking a heatmap square — filters the recordings table
    /// to that day (and locks the heatmap detail readout). nil = no day filter.
    @Published var heatmapSelectedDay: Date? = nil { didSet { markDerivedDirty() } }

    /// Which date the heatmap buckets by. Default Recorded ("when the meeting
    /// happened" — the common case); Transcribed = "when I processed it".
    enum HeatmapDateMode: String, CaseIterable, Identifiable {
        case recorded, transcribed
        var id: String { rawValue }
        var label: String { self == .recorded ? "Recorded" : "Transcribed" }
    }
    @Published var heatmapDateMode: HeatmapDateMode = .recorded { didSet { markDerivedDirty() } }

    /// Click handler for a heatmap square: toggle the day filter on/off.
    func toggleHeatmapDay(_ day: Date) {
        heatmapSelectedDay = (heatmapSelectedDay == day) ? nil : day
    }

    /// The day an entry buckets to under the current date-mode (start-of-day,
    /// local). Transcribed mode returns nil for untranscribed entries (they
    /// simply don't appear on the transcribed grid).
    private func activityDay(for entry: HiDockSyncRecordingEntry) -> Date? {
        switch heatmapDateMode {
        case .recorded: return recordingDay(entry.recording)
        case .transcribed:
            return entry.transcribedDate.map { Calendar.current.startOfDay(for: $0) }
        }
    }

    /// The day a merge group buckets to under the current date-mode. Recorded =
    /// earliest child's recording day; Transcribed = the merged file's
    /// transcript date (nil if the merged file isn't transcribed yet).
    private func mergeGroupDay(_ group: MergeGroup) -> Date? {
        switch heatmapDateMode {
        case .recorded:
            return group.childNames.compactMap { name in
                syncEntries.first { $0.recording.name == name }
                    .flatMap { recordingDay($0.recording) }
            }.min()
        case .transcribed:
            let mp3 = (group.outputPath as NSString).lastPathComponent
            return mergedFileTranscribedDates[mp3].map { Calendar.current.startOfDay(for: $0) }
        }
    }

    var meetingActivityByDay: [Date: DayActivity] {
        ensureDerived(); return _meetingActivityByDay
    }

    private func computeMeetingActivity(_ filtered: [HiDockSyncRecordingEntry]) -> [Date: DayActivity] {
        let childNames = Set(mergeGroups.flatMap(\.childNames))
        var out: [Date: DayActivity] = [:]
        var countedGroups = Set<String>()
        for entry in filtered {
            // A merged recording is ONE meeting: collapse its children into a
            // single count on the group's day (per date-mode) with the merged
            // total duration, rather than counting each piece separately.
            if childNames.contains(entry.recording.name),
               let group = mergeGroups.first(where: { $0.childNames.contains(entry.recording.name) }) {
                if countedGroups.contains(group.id) { continue }
                countedGroups.insert(group.id)
                guard let day = mergeGroupDay(group) else { continue }
                var a = out[day] ?? DayActivity()
                a.count += 1
                a.totalDuration += group.totalDuration
                a.byDevice[deviceShortLabel(for: entry), default: 0] += 1
                let mp3 = (group.outputPath as NSString).lastPathComponent
                if mergedFileTranscribed.contains(mp3) { a.transcribed += 1 }
                if let ex = meetingExtraStats[group.outputName] {
                    a.speakers = (a.speakers ?? 0) + ex.speakers
                    a.actionItems = (a.actionItems ?? 0) + ex.actionItems
                }
                out[day] = a
                continue
            }
            guard let day = activityDay(for: entry) else { continue }
            var a = out[day] ?? DayActivity()
            a.count += 1
            a.totalDuration += entry.recording.duration
            a.byDevice[deviceShortLabel(for: entry), default: 0] += 1
            if entry.transcribed { a.transcribed += 1 }
            if entry.summaryPath != nil { a.summarised += 1 }
            if let ex = meetingExtraStats[entry.recording.outputName] {
                a.speakers = (a.speakers ?? 0) + ex.speakers
                a.actionItems = (a.actionItems ?? 0) + ex.actionItems
            }
            out[day] = a
        }
        return out
    }

    // MARK: - Action Closures (set by AppDelegate)
    var onStartTrigger: () -> Void = {}
    var onStopTrigger: () -> Void = {}
    var onToggleAutoStart: () -> Void = {}
    var onSelectMic: (String) -> Void = { _ in }
    /// Per-device storage summary, keyed by deviceId. Summed recording
    /// sizes from the device catalogue — a minimum bound when firmware
    /// truncation flag is set.
    @Published var syncDeviceStorage: [String: HiDockStorageStats] = [:]

    /// Per-device last-error timestamp and message. When a status query
    /// fails (extractor timeout, USB EIO, access denied) we record the
    /// reason here so the storage summary can flag "H1: unreachable" and
    /// the user knows why new recordings aren't showing up even though
    /// the existing list looks fine.
    @Published var syncDeviceLastError: [String: (String, Date)] = [:]
    /// Per-device last-successful-query timestamp. Pair with lastError to
    /// decide whether to show "unreachable" vs normal storage line.
    @Published var syncDeviceLastOK: [String: Date] = [:]

    /// Known on-device storage capacities in bytes, keyed by product
    /// short-name. Sourced from HiDock's published product specs and
    /// user-verified on a real device. Used to compute free space since
    /// the USB protocol we've implemented doesn't expose a capacity query.
    /// 1 GB = 1,073,741,824 bytes (binary GB, matching what the vendor
    /// lists as "32 GB" / "64 GB" on the product page).
    private static let knownCapacityBytes: [String: Int64] = [
        "H1":  32 * 1_073_741_824,
        "H1E": 32 * 1_073_741_824,
        "P1":  64 * 1_073_741_824,
    ]

    /// Human-readable summary shown in the status header, e.g.
    /// "H1: 4.4 / 32 GB (28 free) · P1: 1.1 / 64 GB (63 free)".
    /// Empty string if nothing usable.
    var storageSummary: String {
        let parts = syncPairedDevices.compactMap { device -> String? in
            // If the device's last query failed AND hasn't recovered since,
            // surface that prominently. Preserves the last-known stats as
            // context so the user knows what state the catalogue is frozen
            // at — not a silent staleness.
            if let (errMsg, errAt) = syncDeviceLastError[device.deviceId],
               (syncDeviceLastOK[device.deviceId].map { $0 < errAt } ?? true) {
                let f = DateFormatter()
                f.dateFormat = "HH:mm"
                let when = f.string(from: errAt)
                let short = errMsg.split(separator: "—").first.map(String.init)?
                    .trimmingCharacters(in: .whitespaces) ?? errMsg
                if let stats = syncDeviceStorage[device.deviceId] {
                    let gb = Double(stats.totalBytesReturned) / 1_073_741_824
                    return "\(device.shortName): ⚠ \(short) @ \(when) — last seen \(String(format: "%.1f GB", gb)) (\(stats.totalFiles) files)"
                }
                return "\(device.shortName): ⚠ \(short) @ \(when)"
            }
            guard let stats = syncDeviceStorage[device.deviceId] else { return nil }
            let usedBytes = Int64(stats.totalBytesReturned)
            let usedGB = Double(usedBytes) / 1_073_741_824
            let usedMB = Double(usedBytes) / 1_048_576
            let usedStr = usedGB >= 1 ? String(format: "%.1f", usedGB) : String(format: "%.0f MB", usedMB)
            let truncFlag = stats.truncated ? "+" : ""

            // If we know the device's capacity, show used / total (free).
            // Otherwise fall back to just used-size so we never show
            // misleading free-space numbers.
            if let cap = Self.knownCapacityBytes[device.shortName] {
                let capGB = Double(cap) / 1_073_741_824
                let freeGB = max(0, capGB - usedGB)
                // When the list is truncated, actual usage is higher — so
                // free is an upper bound. Flag with '≤'.
                let freeStr = stats.truncated
                    ? String(format: "≤%.0f GB free", freeGB)
                    : String(format: "%.0f GB free", freeGB)
                return String(
                    format: "%@: %@%@ / %.0f GB (%@, %d files)",
                    device.shortName, usedStr, truncFlag, capGB, freeStr, stats.totalFiles
                )
            } else {
                return "\(device.shortName): \(usedStr)\(truncFlag) GB (\(stats.totalFiles) files)"
            }
        }
        return parts.joined(separator: " · ")
    }

    var onRefreshSync: () -> Void = {}
    var onImportAudioFile: () -> Void = {}
    var onRemoveImport: (String) -> Void = { _ in }
    /// Transcribe a single recording with a user-supplied expected speaker
    /// count. Overrides the pipeline's automatic density-prior estimate for
    /// recordings where the user knows how many voices to expect (e.g.
    /// "this is a 1:1", "this is a 6-person hackathon panel").
    var onTranscribeWithSpeakerCount: (String, Int) -> Void = { _, _ in }
    /// Delete the locally-downloaded MP3 for a recording (keeps the device
    /// copy intact). Next device refresh will show it as "On device" again.
    var onDeleteLocalCopy: (String) -> Void = { _ in }
    /// Unified Remove for the currently-checked selection. Imports are
    /// removed entirely (file + JSON entry); downloaded HiDock recordings
    /// have their local MP3 deleted but the device copy is preserved.
    var onRemoveSelected: () -> Void = {}
    /// Force a fresh status query against one specific device. Clears
    /// any stale 'unreachable' flag and re-runs the extractor. Useful
    /// when a device recovers from a USB stall and the user wants to
    /// pick it up without waiting for the next auto-refresh cycle.
    var onReconnectDevice: (String) -> Void = { _ in }
    var onPairDock: () -> Void = {}
    var onUnpairDock: () -> Void = {}
    var onChooseRecordingsFolder: () -> Void = {}
    var onChooseTranscriptFolder: () -> Void = {}
    var onDownloadSelected: () -> Void = {}
    var onStopDownload: () -> Void = {}
    var onMarkDownloaded: () -> Void = {}
    var onSelectAll: () -> Void = {}
    var onSelectNone: () -> Void = {}
    var onSelectNotDownloaded: () -> Void = {}
    var onFilterByDevice: (String?) -> Void = { _ in }
    var onToggleChecked: (String, Bool) -> Void = { _, _ in }  // name, shiftHeld
    var onUnmarkDownloaded: () -> Void = {}
    var onToggleAutoDownload: () -> Void = {}
    var onToggleAutoTranscribe: () -> Void = {}
    var onToggleAutoSummarise: () -> Void = {}
    var onToggleMergeExpand: (String) -> Void = { _ in }
    var onTranscribeSelected: () -> Void = {}
    var onSummariseSelected: () -> Void = {}
    var onToggleDiarize: () -> Void = {}
    var onRevealRecording: (String) -> Void = { _ in }
    var onRevealTranscript: (String) -> Void = { _ in }
    var onExportSRT: (String) -> Void = { _ in }
    var onOpenTranscriptViewer: (String) -> Void = { _ in }
    /// Summarisation actions (Phase 1). Summarise = one-shot typed summary via
    /// Claude Code; AskClaude = open the embedded terminal on the transcript;
    /// ViewSummary = open the produced summary file.
    var onSummariseRecording: (HiDockSyncRecordingEntry) -> Void = { _ in }
    var onAskClaudeRecording: (HiDockSyncRecordingEntry) -> Void = { _ in }
    var onViewSummary: (String) -> Void = { _ in }
    var onSendFeedback: () -> Void = {}
    var onShowFeedbackHistory: () -> Void = {}
    var onOpenInObsidian: (String) -> Void = { _ in }
    var onMergeSelected: () -> Void = {}
    var onTrimRecording: (String) -> Void = { _ in }
    var onCheckForUpdates: () -> Void = {}
    var onShowVoiceLibrary: () -> Void = {}
    var onShowVoiceTraining: () -> Void = {}

    // MARK: - Onboarding State
    @Published var showOnboarding = false
    @Published var modelReady = false
    @Published var modelDownloadProgress: Double = 0
    @Published var modelDownloadStatus: String = ""
    @Published var modelDownloading = false
    var onDownloadModel: () -> Void = {}
    var onCancelModelDownload: () -> Void = {}
    var onCompleteOnboarding: () -> Void = {}

    // MARK: - Model Manager
    @Published var modelStatuses: [String: ModelStatus] = [:]
    var onDownloadModelByKey: (String) -> Void = { _ in }
    var onDeleteModelByKey: (String) -> Void = { _ in }
    var onRefreshModelStatuses: () -> Void = {}
    /// Set the given model as the active backend for its stage.
    /// The registry entry's stage metadata determines what gets
    /// persisted; only one model per stage can be active at a time.
    var onSetActiveModelByKey: (String) -> Void = { _ in }
    var onShowModelManager: () -> Void = {}

    // MARK: - AI summariser engine
    /// Which CLI runs Summarise / Ask AI (claude/codex/gemini/ollama/auto).
    /// Mirrors the menu-bar provider submenu; also surfaced in the Models window.
    @Published var summarizeEngine: String = "auto"
    let summarizeEngineChoices: [(id: String, label: String)] = [
        ("auto", "Auto (detect)"),
        ("claude", "Claude"),
        ("codex", "Codex"),
        ("gemini", "Gemini"),
        ("ollama", "Ollama (local)"),
    ]
    var onSetSummarizeEngine: (String) -> Void = { _ in }
    /// When true (default), summarising auto-opens the CLI pane so the user
    /// sees progress. Off = summaries run silently (still works — the headless
    /// `claude --print` uses the one-time global login, no pane needed).
    @Published var showCLIWhileSummarising: Bool = true
    var onSetShowCLIWhileSummarising: (Bool) -> Void = { _ in }

    // MARK: - Summary Templates Manager
    var onShowTemplatesManager: () -> Void = {}
    /// Open Claude Code in the CLI pane to refine an existing template file.
    var onIterateTemplate: (URL) -> Void = { _ in }
    /// Open Claude Code in the CLI pane to author a new template.
    var onCreateTemplate: () -> Void = {}

    // MARK: - Device Manager
    var onShowDeviceManager: () -> Void = {}
    var onForgetDevice: (HiDockPairedDevice) -> Void = { _ in }
    var onPairVolume: (String, String?) -> Void = { _, _ in } // volumeName, subpath
    var onPairPlaud: (String) -> Void = { _ in } // region
    var onSignOutPlaud: (HiDockPairedDevice) -> Void = { _ in } // clear session, keep device
    var onScanVolumes: (@escaping ([VolumeScanResult]) -> Void) -> Void = { $0([]) }

    // MARK: - Session activity badge
    /// Count of recordings transcribed while the main window wasn't focused —
    /// shown as a Dock badge (and menu-bar fallback). Cleared when the user
    /// focuses the main window. Session-scoped (resets on relaunch).
    @Published var sessionTranscribedCount: Int = 0

    // MARK: - Notification Preferences
    @Published var notifyTranscriptionComplete: Bool = true
    @Published var notifyDownloadComplete: Bool = true
    @Published var notifyMicChanges: Bool = true
    var onToggleNotifyTranscription: () -> Void = {}
    var onToggleNotifyDownload: () -> Void = {}
    var onToggleNotifyMicChanges: () -> Void = {}

    // MARK: - Update Status
    @Published var updateStatusText: String = ""

    // MARK: - Appearance
    @Published var appearanceMode: String = "auto"  // "dark", "light", "auto"
    var onSetAppearance: (String) -> Void = { _ in }
}

extension HiDockViewModel {
    var appearanceIcon: String {
        switch appearanceMode {
        case "dark": return "moon.fill"
        case "light": return "sun.max.fill"
        default: return "circle.lefthalf.filled"
        }
    }

    var appearanceLabel: String {
        switch appearanceMode {
        case "dark": return "Dark"
        case "light": return "Light"
        default: return "Auto"
        }
    }
}
