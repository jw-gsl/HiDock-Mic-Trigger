import AppKit
import SwiftUI
import AVFoundation

// MARK: - Audio Player

class SegmentAudioPlayer: ObservableObject {
    @Published var playingSegmentId: String?
    /// Index of the word currently being spoken in the playing segment (for the
    /// karaoke highlight). Uses the recognizer's absolute word timestamps when
    /// available, with a proportional fallback for legacy sidecars.
    @Published var playingWordIndex: Int = 0
    private var player: AVAudioPlayer?
    private var stopTimer: Timer?
    private var progressTimer: Timer?
    private var playBaseline: Double = 0   // player.currentTime at the segment's start
    private var playDuration: Double = 1
    private var playWordCount: Int = 0
    private var playTimelineStart: Double = 0
    private var playTimelineEnd: Double = 0
    private var playWordTimings: [DiarizedWord] = []
    private var decodeProcess: Process?
    private var tempURL: URL?
    /// Bumped on every play()/stop() so a slow ffmpeg decode that finishes after
    /// the user moved on doesn't start playing the wrong clip.
    private var generation = 0

    /// ffmpeg locations, in preference order. Plaud recordings are Opus muxed in
    /// Ogg but named ".mp3", which Core Audio (AVAudioPlayer) cannot decode — we
    /// fall back to ffmpeg to extract the segment.
    private static let ffmpegCandidates = [
        "/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/usr/bin/ffmpeg",
    ]
    private static var ffmpegPath: String? {
        ffmpegCandidates.first { FileManager.default.isExecutableFile(atPath: $0) }
    }

    func play(audioPath: String, start: Double, end: Double, segmentId: String,
              wordCount: Int = 0, wordTimings: [DiarizedWord]? = nil) {
        stop()
        guard FileManager.default.fileExists(atPath: audioPath) else {
            NSLog("SegmentAudioPlayer: audio file not found at \(audioPath)")
            NSSound.beep()
            return
        }

        // Fast path: real mp3/wav/m4a open directly and seek cleanly.
        // fileURLWithPath, not URL(string: "file://…") — the latter returns nil
        // for any path containing a space, silently breaking playback.
        let url = URL(fileURLWithPath: audioPath)
        if let player = try? AVAudioPlayer(contentsOf: url) {
            self.player = player
            player.prepareToPlay()      // without this the first play() can no-op
            player.currentTime = max(0, start)
            player.play()
            playingSegmentId = segmentId
            // Direct path plays from `start`; retain the absolute timeline so
            // word timings remain correct even when the segment starts late.
            startProgressTracking(
                localBaseline: max(0, start),
                timelineStart: max(0, start),
                timelineEnd: end,
                wordCount: wordCount,
                wordTimings: wordTimings
            )
            armStopTimer(after: end - start)
            return
        }

        // Fallback: Core Audio couldn't open it (e.g. Opus). Use ffmpeg to
        // decode just this segment to a temp WAV, then play that from 0.
        decodeAndPlayViaFFmpeg(
            audioPath: audioPath,
            start: start,
            end: end,
            segmentId: segmentId,
            wordCount: wordCount,
            wordTimings: wordTimings
        )
    }

    /// Drive the karaoke word cursor from playback position. Publishes
    /// `playingWordIndex` only when the word changes (a few Hz at most).
    private func startProgressTracking(
        localBaseline: Double,
        timelineStart: Double,
        timelineEnd: Double,
        wordCount: Int,
        wordTimings: [DiarizedWord]?
    ) {
        progressTimer?.invalidate()
        playBaseline = localBaseline
        playDuration = max(0.001, timelineEnd - timelineStart)
        playTimelineStart = timelineStart
        playTimelineEnd = timelineEnd
        playWordTimings = wordTimings ?? []
        playWordCount = wordTimings?.count ?? wordCount
        playingWordIndex = 0
        guard playWordCount > 0 else { return }
        let t = Timer(timeInterval: 0.05, repeats: true) { [weak self] _ in
            guard let self = self, let p = self.player else { return }
            let timelinePosition = self.playTimelineStart
                + p.currentTime - self.playBaseline
            let idx: Int
            if !self.playWordTimings.isEmpty {
                // The word timestamps are absolute audio positions. During a
                // small pause between words, keep the last spoken word lit;
                // once playback reaches a new word its highlight advances at
                // the exact recognizer boundary.
                idx = self.playWordTimings.lastIndex(where: {
                    timelinePosition >= $0.start
                }) ?? 0
            } else {
                let frac = min(max((timelinePosition - self.playTimelineStart) / self.playDuration, 0), 1)
                idx = min(self.playWordCount - 1, Int(frac * Double(self.playWordCount)))
            }
            if idx != self.playingWordIndex { self.playingWordIndex = idx }
        }
        RunLoop.main.add(t, forMode: .common)
        progressTimer = t
    }

    private func decodeAndPlayViaFFmpeg(
        audioPath: String,
        start: Double,
        end: Double,
        segmentId: String,
        wordCount: Int = 0,
        wordTimings: [DiarizedWord]? = nil
    ) {
        guard let ffmpeg = Self.ffmpegPath else {
            NSLog("SegmentAudioPlayer: cannot decode \(audioPath) and no ffmpeg found")
            NSSound.beep()
            return
        }
        let duration = max(0.1, end - start)
        let out = FileManager.default.temporaryDirectory
            .appendingPathComponent("hidock-seg-\(UUID().uuidString).wav")
        tempURL = out

        generation += 1
        let gen = generation
        playingSegmentId = segmentId   // optimistic — shows the stop icon while decoding

        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: ffmpeg)
        // -ss/-t before -i = fast input seek; downmix to 16k mono (voice preview).
        proc.arguments = [
            "-y", "-nostdin",
            "-ss", String(format: "%.3f", max(0, start)),
            "-t", String(format: "%.3f", duration),
            "-i", audioPath,
            "-ar", "16000", "-ac", "1",
            out.path,
        ]
        proc.standardOutput = FileHandle.nullDevice
        proc.standardError = FileHandle.nullDevice
        proc.terminationHandler = { [weak self] p in
            DispatchQueue.main.async {
                guard let self = self, gen == self.generation else {
                    try? FileManager.default.removeItem(at: out)   // stale — clean up
                    return
                }
                self.decodeProcess = nil
                guard p.terminationStatus == 0,
                      let player = try? AVAudioPlayer(contentsOf: out) else {
                    NSLog("SegmentAudioPlayer: ffmpeg decode failed for \(audioPath)")
                    self.playingSegmentId = nil
                    NSSound.beep()
                    return
                }
                self.player = player
                player.prepareToPlay()
                player.play()
                // Temp WAV holds just this segment, so it plays from t=0.
                self.startProgressTracking(
                    localBaseline: 0,
                    timelineStart: start,
                    timelineEnd: end,
                    wordCount: wordCount,
                    wordTimings: wordTimings
                )
                self.armStopTimer(after: duration)
            }
        }
        do {
            try proc.run()
            decodeProcess = proc
        } catch {
            NSLog("SegmentAudioPlayer: failed to launch ffmpeg: \(error)")
            playingSegmentId = nil
            NSSound.beep()
        }
    }

    private func armStopTimer(after duration: Double) {
        stopTimer = Timer.scheduledTimer(withTimeInterval: max(0.1, duration), repeats: false) { [weak self] _ in
            self?.stop()
        }
    }

    func stop() {
        generation += 1               // invalidate any in-flight decode
        player?.stop()
        player = nil
        stopTimer?.invalidate()
        stopTimer = nil
        progressTimer?.invalidate()
        progressTimer = nil
        playWordTimings = []
        playTimelineStart = 0
        playTimelineEnd = 0
        if let proc = decodeProcess, proc.isRunning { proc.terminate() }
        decodeProcess = nil
        if let t = tempURL { try? FileManager.default.removeItem(at: t); tempURL = nil }
        playingSegmentId = nil
        playingWordIndex = 0
    }
}

// MARK: - Data Models

/// Word alignment emitted by Parakeet and persisted in diarized sidecars.
/// Older sidecars may use `text` rather than `word`, so decoding accepts both.
struct DiarizedWord: Codable, Hashable {
    let text: String
    let start: Double
    let end: Double
    let confidence: Double?

    enum CodingKeys: String, CodingKey {
        case word, text, start, end, confidence
    }

    init(text: String, start: Double, end: Double, confidence: Double? = nil) {
        self.text = text
        self.start = start
        self.end = end
        self.confidence = confidence
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        if let word = try? c.decode(String.self, forKey: .word) {
            text = word
        } else {
            text = try c.decode(String.self, forKey: .text)
        }
        start = try c.decode(Double.self, forKey: .start)
        end = try c.decode(Double.self, forKey: .end)
        confidence = try c.decodeIfPresent(Double.self, forKey: .confidence)
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(text, forKey: .word)
        try c.encode(start, forKey: .start)
        try c.encode(end, forKey: .end)
        try c.encodeIfPresent(confidence, forKey: .confidence)
    }
}

/// Per-speaker provenance + review state, mirrored from the Python sidecar
/// (`speaker_meta`). See PLAN-speaker-tagging-loop.md.
struct SpeakerMeta: Codable {
    /// "auto" (voice-library match) | "user" (typed/confirmed) | "unknown"
    /// (acknowledged guest) | "generic" (untouched "Speaker N") |
    /// "legacy"/"legacy_import" (timestamped historical naming evidence).
    var source: String
    var confidence: Double?
    var verified: Bool
}

/// Margin-based voice-match result for one speaker (from the `speaker-confidence`
/// CLI). The margin — how clearly the assigned name beats the next-best enrolled
/// voice — is the real signal; a raw cosine looks high even when wrong.
struct SpeakerScore: Codable {
    var assigned: String?
    var score: Double?          // cosine to the assigned voice (nil if not enrolled)
    var best: String?           // closest enrolled voice overall
    var bestScore: Double?
    var runnerUp: String?       // best enrolled voice other than the assigned name
    var runnerUpScore: Double?
    var margin: Double?         // score - runnerUpScore (negative ⇒ another voice fits better)
}

/// A no-write identity proposal from the isolated WeSpeaker candidate library.
/// The app only displays this evidence; a user action is required before a
/// transcript name or voice profile can change.
struct SpeakerSuggestion: Codable {
    var currentName: String?
    var proposedName: String?
    var similarity: Double?
    var runnerUp: String?
    var runnerUpSimilarity: Double?
    var margin: Double?
    var scorer: String?
    var supportingMeetings: Int?
    var decision: String
    var reviewOnly: Bool
    var reasons: [String]
    var acousticQuality: Double?
    var audioReason: String?
    /// Calendar-event support for the proposed name (nil = no calendar data).
    var inCalendar: Bool?

    enum CodingKeys: String, CodingKey {
        case currentName = "current_name"
        case proposedName = "proposed_name"
        case similarity
        case runnerUp = "runner_up"
        case runnerUpSimilarity = "runner_up_similarity"
        case margin, scorer
        case supportingMeetings = "supporting_meetings"
        case decision
        case reviewOnly = "review_only"
        case reasons
        case acousticQuality = "acoustic_quality"
        case audioReason = "audio_reason"
        case inCalendar = "in_calendar"
    }
}

struct DiarizedTranscript: Codable {
    var version: Int
    var audioFile: String
    var segments: [DiarizedSegment]
    var speakerNames: [String: String]
    /// Provenance/review state per speaker id. Optional — legacy sidecars omit it.
    var speakerMeta: [String: SpeakerMeta]?
    /// Per-speaker embeddings the diarizer stored for cheap re-matching. The
    /// viewer never reads these, but they MUST survive a save round-trip (an
    /// explicit CodingKeys list would otherwise drop them and break `rematch`).
    var speakerEmbeddings: [String: [Double]]?
    /// Which diarization backend produced the sidecar (e.g. "sortformer").
    /// Read by pipeline tooling; must survive a viewer save.
    var backend: String?
    /// Names preserved across the last rediarize (provenance for the merge
    /// tools). Pass-through only.
    var preservedSpeakerLabels: [String]?
    /// Cluster-merge lineage from label preservation. Pass-through only.
    var speakerLineage: [String: SpeakerLineageEntry]?

    enum CodingKeys: String, CodingKey {
        case version
        case audioFile = "audio_file"
        case segments
        case speakerNames = "speaker_names"
        case speakerMeta = "speaker_meta"
        case speakerEmbeddings = "speaker_embeddings"
        case backend
        case preservedSpeakerLabels = "preserved_speaker_labels"
        case speakerLineage = "speaker_lineage"
    }
}

/// Pass-through for the `speaker_lineage` sidecar map produced when labels
/// are preserved across a rediarize.
struct SpeakerLineageEntry: Codable {
    var sourceClusterIds: [String]?
    var survivingName: String?

    enum CodingKeys: String, CodingKey {
        case sourceClusterIds = "source_cluster_ids"
        case survivingName = "surviving_name"
    }
}

struct DiarizedSegment: Codable, Identifiable {
    var id: String { "\(speakerId)-\(start)" }
    let start: Double
    let end: Double
    var speakerId: Int
    var text: String
    var words: [DiarizedWord]?
    /// Display name at diarization time. Renderers fall back to resolving
    /// speaker_id when this is absent, but keeping it avoids the lookup.
    var speaker: String?
    /// Cluster id this segment belonged to before label preservation
    /// remapped it. Pass-through only.
    var sourceSpeakerId: String?

    enum CodingKeys: String, CodingKey {
        case start, end
        case speakerId = "speaker_id"
        case text, words
        case speaker
        case sourceSpeakerId = "source_speaker_id"
    }

    init(start: Double, end: Double, speakerId: Int = 0, text: String,
         words: [DiarizedWord]? = nil, speaker: String? = nil, sourceSpeakerId: String? = nil) {
        self.start = start
        self.end = end
        self.speakerId = speakerId
        self.text = text
        self.words = words
        self.speaker = speaker
        self.sourceSpeakerId = sourceSpeakerId
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        start = try c.decode(Double.self, forKey: .start)
        end = try c.decode(Double.self, forKey: .end)
        speakerId = (try? c.decode(Int.self, forKey: .speakerId)) ?? 0
        text = try c.decode(String.self, forKey: .text)
        words = try c.decodeIfPresent([DiarizedWord].self, forKey: .words)
        speaker = try c.decodeIfPresent(String.self, forKey: .speaker)
        // merge_speaker_labels writes source_speaker_id as a string, but be
        // tolerant of numeric sidecars.
        if let sid = try? c.decode(String.self, forKey: .sourceSpeakerId) {
            sourceSpeakerId = sid
        } else if let sid = try? c.decode(Int.self, forKey: .sourceSpeakerId) {
            sourceSpeakerId = String(sid)
        } else {
            sourceSpeakerId = nil
        }
    }
}

// MARK: - Speaker Colors

private let speakerColors: [Color] = [
    .blue, .green, .orange, .purple, .pink, .teal, .indigo, .mint
]

/// Keep transcript text in one consistent column regardless of the speaker
/// name's rendered width. This leaves enough room for the common named-speaker
/// pill while keeping the transcript readable in the narrow viewer window.
private let transcriptSpeakerColumnWidth: CGFloat = 112

private func colorForSpeaker(_ speakerId: Int) -> Color {
    speakerColors[abs(speakerId) % speakerColors.count]
}

// MARK: - Helpers

private func formatTime(seconds: Double) -> String {
    let totalSeconds = Int(seconds)
    let minutes = totalSeconds / 60
    let secs = totalSeconds % 60
    return String(format: "%02d:%02d", minutes, secs)
}

private func joinTranscriptWords(_ words: [String]) -> String {
    let noSpaceBefore = Set([",", ".", "!", "?", ";", ":", "%", ")", "]", "}"])
    let noSpaceAfter = Set(["(", "[", "{"])
    var result = ""
    for word in words where !word.isEmpty {
        if result.isEmpty || noSpaceBefore.contains(String(word.first!)) || noSpaceAfter.contains(String(result.last!)) {
            result += word
        } else {
            result += " " + word
        }
    }
    return result
}

// MARK: - FlowLayout

/// Word-token wrapping layout. Originally used inside the split-segment
/// sheet (Layer 1 v1); now reused for the inline word-token row that
/// replaced it (Layer 1 v2). macOS 13+ Layout protocol — flow children
/// left-to-right, wrap when the next child would exceed the proposed
/// width.
struct FlowLayout: Layout {
    var spacing: CGFloat = 4
    var lineSpacing: CGFloat = 4

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let maxWidth = proposal.width ?? .infinity
        var x: CGFloat = 0
        var y: CGFloat = 0
        var rowHeight: CGFloat = 0
        var totalWidth: CGFloat = 0
        for sub in subviews {
            let s = sub.sizeThatFits(.unspecified)
            if x > 0, x + s.width > maxWidth {
                y += rowHeight + lineSpacing
                x = 0
                rowHeight = 0
            }
            x += s.width + spacing
            totalWidth = max(totalWidth, x)
            rowHeight = max(rowHeight, s.height)
        }
        return CGSize(width: totalWidth, height: y + rowHeight)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var x: CGFloat = bounds.minX
        var y: CGFloat = bounds.minY
        var rowHeight: CGFloat = 0
        for sub in subviews {
            let s = sub.sizeThatFits(.unspecified)
            if x > bounds.minX, x + s.width > bounds.maxX {
                y += rowHeight + lineSpacing
                x = bounds.minX
                rowHeight = 0
            }
            sub.place(at: CGPoint(x: x, y: y), proposal: ProposedViewSize(s))
            x += s.width + spacing
            rowHeight = max(rowHeight, s.height)
        }
    }
}

// MARK: - Layer 1 v2 word-range selection

/// Identifies one word in the transcript. Segment indices are the indices in
/// the diarized sidecar, so a selection can span several adjacent chunks.
struct WordPosition: Hashable {
    let segmentIndex: Int
    let wordIndex: Int
}

/// Identifies a word selection that may span multiple diarized segments.
struct SegmentSelection: Equatable, Identifiable {
    let anchor: WordPosition
    var focus: WordPosition

    var id: String {
        "\(anchor.segmentIndex):\(anchor.wordIndex)-\(focus.segmentIndex):\(focus.wordIndex)"
    }

    var start: WordPosition {
        isBeforeOrEqual(anchor, focus) ? anchor : focus
    }

    var end: WordPosition {
        isBeforeOrEqual(anchor, focus) ? focus : anchor
    }

    func contains(_ position: WordPosition) -> Bool {
        guard isBeforeOrEqual(start, position), isBeforeOrEqual(position, end) else {
            return false
        }
        return true
    }

    /// Returns the selected word range for one segment, or nil when the
    /// selection does not reach that segment.
    func wordRange(for segmentIndex: Int, wordCount: Int) -> ClosedRange<Int>? {
        guard wordCount > 0, segmentIndex >= start.segmentIndex, segmentIndex <= end.segmentIndex else {
            return nil
        }

        if start.segmentIndex == end.segmentIndex {
            let lower = max(0, min(start.wordIndex, wordCount - 1))
            let upper = max(lower, min(end.wordIndex, wordCount - 1))
            return lower...upper
        }

        if segmentIndex == start.segmentIndex {
            return max(0, min(start.wordIndex, wordCount - 1))...(wordCount - 1)
        }
        if segmentIndex == end.segmentIndex {
            return 0...max(0, min(end.wordIndex, wordCount - 1))
        }
        return 0...(wordCount - 1)
    }

    private func isBeforeOrEqual(_ lhs: WordPosition, _ rhs: WordPosition) -> Bool {
        lhs.segmentIndex < rhs.segmentIndex
            || (lhs.segmentIndex == rhs.segmentIndex && lhs.wordIndex <= rhs.wordIndex)
    }
}

/// Lets word-token views publish their frames in the transcript's common
/// coordinate space so one drag can continue across multiple rows.
private struct TranscriptWordFramesKey: PreferenceKey {
    static var defaultValue: [WordPosition: CGRect] = [:]
    static func reduce(value: inout [WordPosition: CGRect], nextValue: () -> [WordPosition: CGRect]) {
        value.merge(nextValue(), uniquingKeysWith: { $1 })
    }
}

/// Renders a segment's text as a flow of clickable word tokens with a
/// drag-to-select range. The parent owns the gesture so dragging can continue
/// into another diarized segment. The selected range tints blue.
private struct WordTokensView: View {
    let segmentIndex: Int
    let words: [String]
    let selection: SegmentSelection?
    /// Word currently being spoken (karaoke highlight), or nil when not playing.
    var playingWord: Int? = nil

    var body: some View {
        // Reads as a normal paragraph: each word carries its own
        // trailing space so the natural inter-word gap is the font's
        // own space-glyph width, not a padding constant. FlowLayout
        // spacing is 0 so adjacent highlighted words have backgrounds
        // that touch edge-to-edge (matching native text-selection).
        FlowLayout(spacing: 0, lineSpacing: 1) {
            ForEach(Array(words.enumerated()), id: \.offset) { i, w in
                let inRange = selection?.contains(WordPosition(segmentIndex: segmentIndex, wordIndex: i)) ?? false
                let isPlaying = (i == playingWord)
                let display = (i == words.count - 1) ? w : "\(w) "
                Text(display)
                    .font(.body)
                    .foregroundColor(isPlaying ? .primary : nil)
                    .background(
                        inRange ? Color.blue.opacity(0.28)
                            : (isPlaying ? Color.yellow.opacity(0.45) : Color.clear)
                    )
                    .background(
                        GeometryReader { proxy in
                            Color.clear.preference(
                                key: TranscriptWordFramesKey.self,
                                value: [
                                    WordPosition(segmentIndex: segmentIndex, wordIndex: i):
                                        proxy.frame(in: .named("transcriptWords"))
                                ]
                            )
                        }
                    )
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

struct TranscriptRediarizeSummary {
    let beforeSpeakerCount: Int
    let afterSpeakerCount: Int
    let changedSegmentAssignments: Int

    var hasChanges: Bool {
        beforeSpeakerCount != afterSpeakerCount || changedSegmentAssignments > 0
    }
}

/// A locally committed transcript snapshot. Audio is never versioned—only
/// the lightweight transcript/calendar artifacts are eligible for rollback.
struct TranscriptVersion: Identifiable {
    let id: String
    let title: String
}

enum TranscriptRediarizeStatus {
    case running
    case completed(TranscriptRediarizeSummary, DiarizedTranscript)
    case skipped(String)
    case failed(String)
}

private enum ReassignScope: String, CaseIterable, Identifiable {
    case allMeeting = "All meeting"
    case reviewedThrough = "Reviewed until…"
    var id: String { rawValue }
}

/// A calendar event offered as speaker-count context for this recording.
struct CalendarMeetingCandidate: Identifiable, Hashable {
    let id: String
    let title: String
    let start: Date
    let end: Date
    let attendeeNames: [String]

    var attendeeCount: Int { attendeeNames.count }
    var attendeeSummary: String {
        attendeeCount == 0 ? "invitees not returned" : "\(attendeeCount) invited"
    }
}

private struct CalendarAssistantMessage: Identifiable {
    enum Role { case user, assistant }
    let id = UUID()
    let role: Role
    let text: String
}

// MARK: - TranscriptViewerView

struct TranscriptViewerView: View {
    @State var transcript: DiarizedTranscript
    @State var editingSpeakerId: Int? = nil
    @State var editingName: String = ""
    /// Which pill location owns the active edit ("legend" / "verify"), so the
    /// TextField only appears where you clicked, not on every pill for that id.
    @State private var editingContext: String = "legend"
    /// Tracks focus of the inline name field so clicking anywhere else in the
    /// window commits the edit and deselects it (the field otherwise stays
    /// active with no way to dismiss it).
    @FocusState private var nameFieldFocused: Bool
    /// Live per-speaker confidence (id-string → 0–1) from the background CLI:
    /// how well each speaker's voice matches the enrolled voice of its name.
    @State private var liveConfidence: [String: SpeakerScore] = [:]
    @State private var liveSuggestions: [String: SpeakerSuggestion] = [:]
    @State private var suggestionsLoading = false
    /// A rename that collided with another speaker's name — pending the user's
    /// choice to merge the two speakers or cancel.
    @State private var pendingMerge: PendingMerge?
    /// When set, the segment list is narrowed to just this speaker so you can
    /// listen through their turns and check the voice is really theirs.
    @State private var speakerFilter: Int?
    /// Enrolled voice-library names, for the rename autocomplete. Picking one
    /// maps the speaker to that exact enrolled voice (so confirming reinforces
    /// the same centroid instead of fragmenting into near-duplicate names).
    @State private var libraryNames: [String] = []
    /// Weak ("closest voice") candidates stay hidden behind a click so they
    /// can't anchor the reviewer's judgement; this set tracks reveals.
    @State private var revealedWeakSuggestions: Set<String> = []
    /// When every speaker is verified the panel collapses to a one-line done
    /// state; this re-expands it for inspection.
    @State private var verifyPanelExpanded = false
    /// Confirm dialog for "Clear all" unconfirmed auto-matches in the verify panel.
    @State private var confirmClearAllSpeakers = false
    /// Confirm dialog for "Mark all unknown" (dismiss needs-tagging without names).
    @State private var confirmMarkAllUnknown = false

    struct PendingMerge: Identifiable {
        let id = UUID()
        let from: Int      // the speaker just renamed
        let to: Int        // the existing speaker that already has this name
        let name: String
    }
    @State var rediarizeNSpeakers: Int = 2
    @State private var rediarizeStatus: TranscriptRediarizeStatus?
    @State var transcriptHistory: [DiarizedTranscript] = []
    /// Layer 1 v2 — currently active word selection, which may span several
    /// diarized segments.
    @State var selection: SegmentSelection? = nil
    /// A selected range being named through the contextual menu. Keeping this
    /// separate from `selection` means the picker still acts on exactly the
    /// words the user right-clicked, even if the view refreshes underneath it.
    @State private var pendingNamedSelection: SegmentSelection?
    @State private var selectionPersonQuery = ""
    @State private var transcriptWordFrames: [WordPosition: CGRect] = [:]
    @State private var selectionDragStart: WordPosition? = nil
    /// Timestamp of the text just edited. Splitting a segment changes its view
    /// identity, so SwiftUI otherwise reconstructs the scroll view at the top.
    @State private var pendingTranscriptRestoreTime: Double?
    @State private var showReassignOptions = false
    @State private var showSpeakerToolsHelp = false
    @State private var reassignScope: ReassignScope = .allMeeting
    @State private var reviewedUntilMinutes = 0
    @State private var reviewedUntilSeconds = 0
    @State private var calendarCandidates: [CalendarMeetingCandidate] = []
    @State private var calendarLoading = false
    @State private var linkedCalendarEvent: CalendarMeetingCandidate?
    @State private var suggestedCalendarEvent: CalendarMeetingCandidate?
    @State private var calendarCheckRejected = false
    /// A result the reviewer has highlighted in the picker. Highlighting is
    /// deliberately separate from linking: calendar context affects speaker
    /// handling, so it always gets an explicit Confirm meeting action.
    @State private var selectedCalendarCandidate: CalendarMeetingCandidate?
    @State private var showCalendarPicker = false
    @State private var calendarAssistantExpanded = false
    @State private var calendarAssistantDraft = ""
    @State private var calendarAssistantMessages: [CalendarAssistantMessage] = []
    @State private var calendarAssistantSessionId: String?
    @State private var calendarAssistantRunning = false
    @State private var showTranscriptHistory = false
    @State private var transcriptVersions: [TranscriptVersion] = []
    @State private var pendingTranscriptRestore: TranscriptVersion?
    @StateObject var audioPlayer = SegmentAudioPlayer()
    let filePath: String
    let audioPath: String
    let onEnrollSpeaker: (String, String, Double, Double) -> Void
    var onRediarize: ((String, Int?, @escaping (TranscriptRediarizeStatus) -> Void) -> Void)?
    /// Layer 2 callback — fires `transcribe.py recluster-with-anchors`
    /// against the current diarized.json, treating every segment with
    /// a user-edited speaker name as an anchor centroid. Optional so
    /// older call-sites (rediarize-only flow) keep compiling.
    var onReclusterWithLabels: ((String, Double?) -> Void)?
    /// Re-match still-generic speakers in THIS transcript against the voice
    /// library (`rematch` verb). Optional so older call-sites keep compiling.
    var onRematch: ((String) -> Void)?
    /// Enrol a speaker from the diarized sidecar's stored centroid (name,
    /// jsonPath, speakerId) — a far better voiceprint than one short segment.
    /// Falls back to onEnrollSpeaker (audio) when nil.
    var onEnrollSpeakerFromDiarized: ((String, String, Int) -> Void)?
    /// Score each speaker's voice against its assigned name in the library
    /// (background CLI). Returns {speaker-id-string: confidence 0–1}. Optional
    /// so older call-sites keep compiling.
    var onScoreSpeakers: ((String, @escaping ([String: SpeakerScore]) -> Void) -> Void)?
    /// Re-embed unverified speakers with the isolated WeSpeaker candidate and
    /// return review-only proposals. The callback never mutates the sidecar.
    var onSuggestSpeakers: ((String, @escaping ([String: SpeakerSuggestion]) -> Void) -> Void)?
    /// Record the explicit human outcome and, for a confirmation, teach the
    /// isolated candidate library from the confirmed audio evidence.
    var onRecordSpeakerSuggestion: ((String, Int, String, String?, String?) -> Void)?
    /// Update an existing library identity when a user deliberately renames it.
    /// The backend treats a rename into an existing name as a merge, preserving
    /// both identities' samples under the surviving name.
    var onRenameVoiceLibrary: ((String, String) -> Void)? = nil
    /// Tell the recordings table that this transcript's speaker-review state
    /// changed. The table must not wait for the next full transcription-state
    /// refresh to move from the orange tag/blue match icon to the green tick.
    var onSpeakerReviewChanged: ((String) -> Void)? = nil
    /// Fetch the enrolled voice-library names (for the map-to-existing-speaker
    /// autocomplete). Optional so older call-sites keep compiling.
    var onListVoiceNames: ((@escaping ([String]) -> Void) -> Void)?
    /// After the diarized JSON is saved, regenerate the sibling .md so
    /// confirmed-only names hit disk (unconfirmed stay Speaker N).
    var onRewriteMarkdown: ((String) -> Void)?
    /// Persist the current on-disk artifacts before this view writes a speaker
    /// edit, so the previous state is always a one-click rollback target.
    var onSnapshotTranscript: ((String, String) -> Void)? = nil
    var onListTranscriptVersions: ((String) -> [TranscriptVersion])? = nil
    var onRestoreTranscriptVersion: ((String, String) -> Void)? = nil
    /// Search the user's locally synced macOS calendar around this recording.
    var onFindCalendarEvents: ((String, Double, @escaping ([CalendarMeetingCandidate]) -> Void) -> Void)?
    /// Query the already-authenticated Microsoft 365 MCP configured in Claude.
    var onFindClaudeCalendarEvents: ((String, Double, @escaping ([CalendarMeetingCandidate]) -> Void) -> Void)? = nil
    /// A previously saved automatic match.  It remains a suggestion until the
    /// reviewer confirms it in this view.
    var onLoadCalendarSuggestion: ((String) -> CalendarMeetingCandidate?)? = nil
    /// A reviewer explicitly rejected the saved suggestion.  This suppresses
    /// background MCP checks until they choose to check again.
    var onLoadCalendarRejection: ((String) -> Bool)? = nil
    var onClearCalendarRejection: ((String) -> Void)? = nil
    /// The durable, already-confirmed calendar link for this recording.
    var onLoadCalendarEvent: ((String) -> CalendarMeetingCandidate?)? = nil
    /// Persist a selected event as the recording's calendar-context sidecar.
    /// The final Boolean lets this view own the visible rediarisation update
    /// while table confirmation runs the same pass in the background.
    var onLinkCalendarEvent: ((String, Double, CalendarMeetingCandidate, Bool) -> Void)?
    /// Remove a previously confirmed calendar-context sidecar.
    var onRemoveCalendarEvent: ((String) -> Void)? = nil
    /// Reject the saved pending suggestion and continue with speaker matching
    /// without calendar context.  This is shared with the table action.
    var onRejectCalendarSuggestion: ((String) -> Void)? = nil
    /// Open the in-app terminal with MCP calendar onboarding instructions.
    var onOpenCalendarMCPOnboarding: (() -> Void)?
    /// A formatted, multi-turn Claude CLI/MCP calendar conversation. The UI is
    /// deliberately local to the transcript rather than a raw terminal window.
    var onCalendarAssistantTurn: ((String, Double, String, String?, @escaping (String, String?) -> Void) -> Void)? = nil

    private var uniqueSpeakerIds: [Int] {
        Array(Set(transcript.segments.map(\.speakerId))).sorted()
    }

    /// The re-detection control starts at the number of speakers in the
    /// transcript currently on screen. Keep the existing generous upper bound
    /// for recordings where the user wants to try a larger count.
    private var rediarizeSpeakerRange: ClosedRange<Int> {
        2...max(8, uniqueSpeakerIds.count)
    }

    private var isRediarizing: Bool {
        if case .running = rediarizeStatus { return true }
        return false
    }

    private func syncRediarizeSpeakerCount() {
        let detected = uniqueSpeakerIds.count
        guard detected >= rediarizeSpeakerRange.lowerBound else { return }
        rediarizeNSpeakers = min(max(detected, rediarizeSpeakerRange.lowerBound), rediarizeSpeakerRange.upperBound)
    }

    /// Remove names and provenance for speaker IDs that no longer have any
    /// segments. Merging speakers reassigns segments, so the old IDs must not
    /// linger in the sidecar and confuse later review or export logic.
    private func pruneInactiveSpeakerState() {
        let activeKeys = Set(transcript.segments.map { "\($0.speakerId)" })
        transcript.speakerNames = transcript.speakerNames.filter { activeKeys.contains($0.key) }
        if let meta = transcript.speakerMeta {
            transcript.speakerMeta = meta.filter { activeKeys.contains($0.key) }
        }
        // A merge can remove the currently filtered speaker entirely. Do not
        // leave the transcript looking empty; fall back to the full meeting.
        if let filteredId = speakerFilter,
           !transcript.segments.contains(where: { $0.speakerId == filteredId }) {
            speakerFilter = nil
        }
    }

    private var hasSpeakers: Bool {
        // Non-diarized transcripts have all speaker_id=0 and empty speaker_names
        uniqueSpeakerIds.count > 1 || !transcript.speakerNames.isEmpty
    }

    private func speakerName(for id: Int) -> String {
        transcript.speakerNames["\(id)"] ?? "Speaker \(id + 1)"
    }

    /// A named speaker is a safe reassignment anchor when it was explicitly
    /// confirmed, came from the timestamped legacy import, or predates the
    /// provenance fields entirely. Unverified automatic matches remain
    /// provisional and must not teach the meeting-level reassignment pass.
    private func isNamedAnchor(_ id: Int) -> Bool {
        let name = speakerName(for: id).trimmingCharacters(in: .whitespaces)
        guard !name.isEmpty, !isGenericName(name) else { return false }
        guard let meta = speakerMeta(for: id) else { return true }
        return meta.verified
            || meta.source == "user"
            || meta.source == "legacy"
            || meta.source == "legacy_import"
    }

    private var hasAnchorNamedSpeakers: Bool {
        uniqueSpeakerIds.contains(where: isNamedAnchor)
    }

    private func startRediarize(speakers: Int?) {
        rediarizeStatus = .running
        onRediarize?(filePath, speakers) { status in
            rediarizeStatus = status
            if case .completed(_, let updatedTranscript) = status {
                applyRediarizedTranscript(updatedTranscript)
            }
        }
    }

    // MARK: - Computed Stats

    private struct SpeakerStats {
        let speakerId: Int
        var talkTime: Double = 0
        var wordCount: Int = 0
        var turns: Int = 0
        var longestMonologue: Double = 0
    }

    private var speakerStats: [SpeakerStats] {
        var stats: [Int: SpeakerStats] = [:]
        var prevSpeaker: Int? = nil

        for seg in transcript.segments {
            let dur = seg.end - seg.start
            let words = seg.text.split(separator: " ").count
            let id = seg.speakerId

            if stats[id] == nil {
                stats[id] = SpeakerStats(speakerId: id)
            }
            stats[id]!.talkTime += dur
            stats[id]!.wordCount += words
            stats[id]!.longestMonologue = max(stats[id]!.longestMonologue, dur)
            if prevSpeaker != id {
                stats[id]!.turns += 1
            }
            prevSpeaker = id
        }

        return stats.values.sorted { $0.talkTime > $1.talkTime }
    }

    private var totalDuration: Double {
        guard let first = transcript.segments.first, let last = transcript.segments.last else { return 0 }
        return last.end - first.start
    }

    var body: some View {
        VStack(spacing: 0) {
            // Top bar
            HStack {
                Image(systemName: "waveform")
                    .foregroundColor(.secondary)
                Text(transcript.audioFile)
                    .font(.headline)
                    .lineLimit(1)
                Spacer()
                // Document-level actions only. Speaker tools live in their own
                // strip below so this bar doesn't get clunky.
                if !transcriptHistory.isEmpty {
                    Button {
                        undoMerge()
                    } label: {
                        Label("Undo", systemImage: "arrow.uturn.backward")
                    }
                    .buttonStyle(.bordered)
                    .controlSize(.small)
                    .keyboardShortcut("z", modifiers: .command)
                    .help("Undo the last speaker change (merge / re-assign).")
                }

                Button {
                    transcriptVersions = onListTranscriptVersions?(filePath) ?? []
                    showTranscriptHistory = true
                } label: {
                    Image(systemName: "clock.arrow.circlepath")
                }
                .buttonStyle(.bordered)
                .controlSize(.small)
                .help("Transcript history and rollback")
                .popover(isPresented: $showTranscriptHistory, arrowEdge: .bottom) {
                    transcriptHistoryPicker
                        .frame(width: 360)
                        .padding(12)
                }

                // Icon-only so they always fit the (narrow) pane.
                Button {
                    copyAllToClipboard()
                } label: {
                    Image(systemName: "doc.on.doc")
                }
                .buttonStyle(.bordered)
                .controlSize(.small)
                .keyboardShortcut("c", modifiers: [.command, .shift])
                .help("Copy All — whole transcript with timestamps. Unconfirmed speakers export as Speaker 1/2/… until you confirm their names.")

                Button {
                    let mdPath = filePath.replacingOccurrences(of: "_diarized.json", with: ".md")
                    NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: mdPath)])
                } label: {
                    Image(systemName: "folder")
                }
                .buttonStyle(.bordered)
                .controlSize(.small)
                .help("Show File — reveal the transcript's markdown file in Finder.")
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 10)
            .background(.ultraThinMaterial)

            Divider()

            // Speaker tools — grouped so the top bar stays clean.
            if hasSpeakers {
                speakerToolsBar
                Divider()
            }

            // Stats header
            if hasSpeakers && !speakerStats.isEmpty {
                statsHeader
                Divider()
            }

            // Speaker legend (only for diarized transcripts)
            if hasSpeakers {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        ForEach(uniqueSpeakerIds, id: \.self) { speakerId in
                            speakerPill(speakerId: speakerId, interactive: true)
                                .contextMenu {
                                    Button(speakerFilter == speakerId ? "Show all speakers" : "Show only this speaker") {
                                        speakerFilter = (speakerFilter == speakerId) ? nil : speakerId
                                    }
                                    if uniqueSpeakerIds.count > 1 {
                                        Divider()
                                        ForEach(uniqueSpeakerIds.filter { $0 != speakerId }, id: \.self) { targetId in
                                            Button("Merge into \(speakerName(for: targetId))") {
                                                mapSpeaker(from: speakerId, to: targetId)
                                            }
                                        }
                                    }
                                }
                        }
                    }
                    .padding(.horizontal, 16)
                    .padding(.vertical, 8)
                }

                Divider()
            }

            // Keep speaker verification and the transcript in separate panes.
            // VSplitView supplies a draggable divider so the review area can be
            // expanded when needed without pushing the transcript off-screen.
            // Once nothing needs review, the panel collapses to a one-line
            // done state instead of vanishing mid-interaction.
            if uniqueSpeakerIds.count > 1 {
                if needsVerification || verifyPanelExpanded {
                    VSplitView {
                        speakerVerifyPanel
                            .frame(
                                // The row list scrolls. Never make its calculated
                                // content height the minimum: meetings with many
                                // speakers would otherwise consume the full split
                                // and push the transcript out of sight.
                                minHeight: 128,
                                idealHeight: speakerVerifyPanelIdealHeight,
                                maxHeight: speakerVerifyPanelMaximumHeight
                            )
                            .layoutPriority(0)

                        transcriptContent
                            .frame(minHeight: 220, maxHeight: .infinity)
                            .layoutPriority(2)
                    }
                    // VSplitView remembers its divider position. Recreate the
                    // split when diarisation produces a different speaker count
                    // so the new ideal/minimum height is applied on first render.
                    .id("speaker-review-\(uniqueSpeakerIds.count)")
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else {
                    VStack(spacing: 0) {
                        allVerifiedBanner
                        Divider()
                        transcriptContent
                    }
                }
            } else {
                transcriptContent
            }
        }
        .frame(minWidth: 360, minHeight: 300)   // hosted in a resizable pane now
        .onChange(of: nameFieldFocused) { focused in
            // Clicking anywhere else in the window resigns the field's focus —
            // commit the pending edit so it doesn't stay stuck in edit mode.
            // Deferred so a click on an autocomplete suggestion (which also
            // resigns focus) can commit ITS name first and clear editing — the
            // deferred block then sees editing cleared and no-ops, instead of
            // committing the half-typed prefix.
            if !focused {
                let id = editingSpeakerId
                DispatchQueue.main.async {
                    if let id = id, editingSpeakerId == id {
                        commitRename(speakerId: id)
                    }
                }
            }
        }
        .onAppear {
            syncRediarizeSpeakerCount()
            refreshConfidence()
            refreshSuggestions()
            refreshLibraryNames()
            // Calendar matching should be a quiet background suggestion when
            // a transcript opens, not a separate task the reviewer must
            // remember to start.  It remains unlinked until confirmed.
            linkedCalendarEvent = onLoadCalendarEvent?(audioPath)
            suggestedCalendarEvent = linkedCalendarEvent == nil ? onLoadCalendarSuggestion?(audioPath) : nil
            calendarCheckRejected = linkedCalendarEvent == nil
                && suggestedCalendarEvent == nil
                && (onLoadCalendarRejection?(audioPath) ?? false)
            if linkedCalendarEvent == nil, suggestedCalendarEvent == nil, !calendarCheckRejected {
                loadCalendarCandidates()
            }
        }
        .popover(item: $pendingNamedSelection, arrowEdge: .bottom) { selected in
            selectionPersonPicker(selection: selected)
                .frame(width: 300)
                .padding(10)
        }
        .confirmationDialog(
            "Merge speakers?",
            isPresented: Binding(get: { pendingMerge != nil }, set: { if !$0 { pendingMerge = nil } }),
            presenting: pendingMerge
        ) { merge in
            Button("Merge into one speaker") { confirmMerge(merge); pendingMerge = nil }
            Button("Cancel", role: .cancel) { pendingMerge = nil }
        } message: { merge in
            Text("“\(merge.name)” is already assigned to \(speakerName(for: merge.to)). Two speakers can't share a name — merge them into one person? This reassigns \(speakerName(for: merge.from))'s segments to \(merge.name).")
        }
    }

    // MARK: - Inline word-range split (Layer 1 v2)

    /// Lowest unused speakerId — used when the user picks "New speaker"
    /// in the inline speaker bar.
    private func nextNewSpeakerId() -> Int {
        let used = Set(transcript.segments.map(\.speakerId))
        var n = 0
        while used.contains(n) { n += 1 }
        return n
    }

    /// Speaker bar for the active selection. It sits above the transcript so
    /// it remains available when the selection spans several rows.
    @ViewBuilder
    private func inlineSpeakerBar(selection: SegmentSelection) -> some View {
        let count = selectedWordCount(selection)
        let spansSegments = selection.start.segmentIndex != selection.end.segmentIndex
        HStack(spacing: 8) {
            Image(systemName: "scissors")
                .foregroundColor(.blue)
                .font(.caption)
            Text("Assign \(count) word\(count == 1 ? "" : "s")\(spansSegments ? " across chunks" : "") to:")
                .font(.caption.weight(.medium))
                .foregroundColor(.secondary)

            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 6) {
                    ForEach(uniqueSpeakerIds, id: \.self) { sid in
                        Button {
                            applySelection(selection: selection, newSpeakerId: sid)
                            self.selection = nil
                        } label: {
                            speakerPillLabel(speakerId: sid)
                        }
                        .buttonStyle(.plain)
                    }
                    Button {
                        applySelection(selection: selection, newSpeakerId: nextNewSpeakerId())
                        self.selection = nil
                    } label: {
                        HStack(spacing: 4) {
                            Image(systemName: "person.crop.circle.badge.plus")
                            Text("New speaker")
                                .font(.caption.weight(.medium))
                        }
                        .padding(.horizontal, 8)
                        .padding(.vertical, 4)
                        .background(Color.secondary.opacity(0.15))
                        .cornerRadius(12)
                    }
                    .buttonStyle(.plain)
                    Button {
                        selectionPersonQuery = ""
                        pendingNamedSelection = selection
                    } label: {
                        HStack(spacing: 4) {
                            Image(systemName: "person.crop.circle.badge.plus")
                            Text("Name new speaker")
                                .font(.caption.weight(.medium))
                        }
                        .padding(.horizontal, 8)
                        .padding(.vertical, 4)
                        .background(Color.accentColor.opacity(0.14))
                        .cornerRadius(12)
                    }
                    .buttonStyle(.plain)
                }
            }

            Spacer()

            Button {
                self.selection = nil
            } label: {
                Image(systemName: "xmark.circle.fill")
                    .foregroundColor(.secondary)
                    .font(.body)
            }
            .buttonStyle(.plain)
            .help("Cancel selection")
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 6)
        .background(Color.blue.opacity(0.06), in: RoundedRectangle(cornerRadius: 6))
        .padding(.leading, 76)
        .padding(.trailing, 4)
    }

    private func selectedWordCount(_ selection: SegmentSelection) -> Int {
        var total = 0
        for idx in selection.start.segmentIndex...selection.end.segmentIndex {
            let words = transcript.segments[idx].words?.map(\.text)
                ?? transcript.segments[idx].text
                    .split(separator: " ", omittingEmptySubsequences: false)
                    .map(String.init)
            if let range = selection.wordRange(for: idx, wordCount: words.count) {
                total += range.upperBound - range.lowerBound + 1
            }
        }
        return total
    }

    /// Assign just the selected words to a fresh speaker ID, then give that
    /// local speaker the identity explicitly chosen by the reviewer. This is
    /// intentionally different from renaming a speaker pill, which changes
    /// every turn currently carrying that speaker ID.
    private func assignSelectionToNamedSpeaker(_ selection: SegmentSelection, name: String) {
        let trimmed = name.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        let newId = nextNewSpeakerId()
        applySelection(selection: selection, newSpeakerId: newId)
        guard transcript.segments.contains(where: { $0.speakerId == newId }) else { return }
        transcript.speakerNames["\(newId)"] = trimmed
        setMeta(newId, source: "user", verified: true, confidence: nil)
        saveTranscript()
        // This was an explicit human identification, so it is trusted training
        // evidence as well as a correction to this transcript. The sidecar has
        // just been saved, allowing the enrolment path to use the selected
        // segment as its fallback when this fresh local speaker has no stored
        // diarizer centroid yet.
        enrollConfirmed(trimmed, speakerId: newId)
        self.selection = nil
        refreshConfidence()
        refreshLibraryNames()
    }

    @ViewBuilder
    private func selectionPersonPicker(selection: SegmentSelection) -> some View {
        let query = selectionPersonQuery.trimmingCharacters(in: .whitespacesAndNewlines)
        let invitedLibraryNames = calendarInvitedLibraryNames
        let orderedNames = invitedLibraryNames + libraryNames.filter { !invitedLibraryNames.contains($0) }
        let matches = orderedNames
            .filter { query.isEmpty || $0.localizedCaseInsensitiveContains(query) }
            .prefix(12)

        VStack(alignment: .leading, spacing: 8) {
            Text("Name selected text")
                .font(.headline)
            TextField("Search Voice Library or enter a name", text: $selectionPersonQuery)
                .textFieldStyle(.roundedBorder)
            if !matches.isEmpty {
                Text(invitedLibraryNames.isEmpty ? "Voice Library" : "Invited people in Voice Library first")
                    .font(.caption)
                    .foregroundColor(.secondary)
                ForEach(Array(matches), id: \.self) { name in
                    Button(name) {
                        assignSelectionToNamedSpeaker(selection, name: name)
                        pendingNamedSelection = nil
                    }
                    .buttonStyle(.plain)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.vertical, 3)
                }
            }
            if !query.isEmpty {
                Divider()
                Button("Use \"\(query)\" as a new person") {
                    assignSelectionToNamedSpeaker(selection, name: query)
                    pendingNamedSelection = nil
                }
            }
        }
    }

    /// Assign the selection to a speaker, splitting only the boundary
    /// segments and reassigning any wholly selected chunks in between.
    private func applySelection(selection: SegmentSelection, newSpeakerId: Int) {
        guard !transcript.segments.isEmpty,
              selection.start.segmentIndex >= 0,
              selection.end.segmentIndex < transcript.segments.count else { return }

        let originalSegments = transcript.segments
        var updatedSegments: [DiarizedSegment] = []
        var changed = false
        var sampleStart: Double?
        var sampleEnd: Double?

        for (idx, segment) in originalSegments.enumerated() {
            let timedWords = segment.words
            let words = timedWords?.map(\.text)
                ?? segment.text
                    .split(separator: " ", omittingEmptySubsequences: false)
                    .map(String.init)
            guard let wordRange = selection.wordRange(for: idx, wordCount: words.count) else {
                updatedSegments.append(segment)
                continue
            }

            // Selecting text already assigned to the chosen speaker should not
            // create needless zero-information subsegments.
            if segment.speakerId == newSpeakerId {
                updatedSegments.append(segment)
                continue
            }

            updatedSegments.append(contentsOf: splitSegment(
                segment,
                words: words,
                timedWords: timedWords,
                wordRange: wordRange,
                newSpeakerId: newSpeakerId
            ))
            changed = true

            let startTime: Double
            let endTime: Double
            if let timedWords, !timedWords.isEmpty {
                startTime = timedWords[wordRange.lowerBound].start
                endTime = timedWords[wordRange.upperBound].end
            } else {
                let duration = max(segment.end - segment.start, 0.001)
                let totalWords = Double(words.count)
                startTime = segment.start + (Double(wordRange.lowerBound) / totalWords) * duration
                endTime = segment.start + (Double(wordRange.upperBound + 1) / totalWords) * duration
            }
            sampleStart = min(sampleStart ?? startTime, startTime)
            sampleEnd = max(sampleEnd ?? endTime, endTime)
        }

        guard changed else { return }

        transcriptHistory.append(transcript)
        transcript.segments = updatedSegments
        pendingTranscriptRestoreTime = sampleStart
        pruneInactiveSpeakerState()
        syncRediarizeSpeakerCount()

        // A multi-chunk selection is discontinuous in the audio whenever
        // there are intervening turns, so only enrol a single contiguous chunk.
        // The user can still confirm the named speaker afterward to enrol its
        // stored diarized centroid safely.
        if selection.start.segmentIndex == selection.end.segmentIndex,
           let start = sampleStart,
           let end = sampleEnd,
           !isGenericName(speakerName(for: newSpeakerId)) {
            onEnrollSpeaker(speakerName(for: newSpeakerId), audioPath, start, end)
        }

        saveTranscript()
    }

    /// Split one diarized segment around a selected word range. New sidecars
    /// carry exact word timings; legacy sidecars retain the old proportional
    /// fallback until that recording is retranscribed.
    private func splitSegment(
        _ segment: DiarizedSegment,
        words: [String],
        timedWords: [DiarizedWord]?,
        wordRange: ClosedRange<Int>,
        newSpeakerId: Int
    ) -> [DiarizedSegment] {
        guard !words.isEmpty else { return [segment] }
        let startWord = max(0, min(wordRange.lowerBound, words.count - 1))
        let endWord = max(startWord, min(wordRange.upperBound, words.count - 1))

        let exactWords = timedWords?.count == words.count ? timedWords : nil
        let rangeStartTime: Double
        let rangeEndTime: Double
        if let exactWords {
            rangeStartTime = exactWords[startWord].start
            rangeEndTime = exactWords[endWord].end
        } else {
            let duration = max(segment.end - segment.start, 0.001)
            let totalWords = Double(words.count)
            rangeStartTime = segment.start + (Double(startWord) / totalWords) * duration
            rangeEndTime = segment.start + (Double(endWord + 1) / totalWords) * duration
        }

        func makeSegment(start: Double, end: Double, speakerId: Int,
                         wordRange: Range<Int>) -> DiarizedSegment {
            let text = joinTranscriptWords(Array(words[wordRange]))
            let wordSlice = exactWords.map { Array($0[wordRange]) }
            return DiarizedSegment(
                start: start,
                end: end,
                speakerId: speakerId,
                text: text,
                words: wordSlice
            )
        }

        var replacement: [DiarizedSegment] = []
        if startWord > 0 {
            replacement.append(makeSegment(
                start: segment.start, end: rangeStartTime,
                speakerId: segment.speakerId, wordRange: 0..<startWord
            ))
        }
        replacement.append(makeSegment(
            start: rangeStartTime, end: rangeEndTime,
            speakerId: newSpeakerId, wordRange: startWord..<(endWord + 1)
        ))
        if endWord < words.count - 1 {
            replacement.append(makeSegment(
                start: rangeEndTime, end: segment.end,
                speakerId: segment.speakerId,
                wordRange: (endWord + 1)..<words.count
            ))
        }
        return replacement
    }

    // MARK: - Speaker tools

    /// Secondary strip grouping the speaker-fixing actions, each shown only when
    /// it applies, with plain-language tooltips.
    private var speakerToolsBar: some View {
      VStack(alignment: .leading, spacing: 6) {
        HStack(spacing: 5) {
            Text("Speakers")
                .font(.subheadline.weight(.semibold))
            Button {
                showSpeakerToolsHelp = true
            } label: {
                Image(systemName: "questionmark.circle")
                    .font(.subheadline)
            }
            .buttonStyle(.plain)
            .help("What do Refine, Redetect, Rematch, and Reassign do?")
            .popover(isPresented: $showSpeakerToolsHelp, arrowEdge: .bottom) {
                speakerToolsHelp
                    .frame(width: 360)
                    .padding(12)
            }
            if onRediarize != nil {
                Stepper("\(rediarizeNSpeakers)", value: $rediarizeNSpeakers, in: rediarizeSpeakerRange)
                    .font(.subheadline.weight(.medium))
                    .frame(width: 58)
                    .help("Number of speakers to use when you press Redetect. Adjust it if the detected count is wrong.")
            }
            Button {
                showCalendarPicker = true
                if calendarCandidates.isEmpty && suggestedCalendarEvent == nil && !calendarCheckRejected {
                    loadCalendarCandidates()
                }
            } label: {
                Label(
                    linkedCalendarEvent?.title ?? (suggestedCalendarEvent.map { "Suggested: \($0.title)" }) ?? "Match meeting",
                    systemImage: linkedCalendarEvent == nil ? "calendar.badge.plus" : "calendar.badge.checkmark"
                )
                    .font(.caption.weight(.medium))
                    .lineLimit(1)
                    .layoutPriority(1)
                    .foregroundColor(linkedCalendarEvent == nil ? .primary : .green)
            }
            .buttonStyle(.borderless)
            .help(linkedCalendarEvent == nil ? "Match this recording to a calendar event" : "Calendar: \(linkedCalendarEvent!.title)")
            .popover(isPresented: $showCalendarPicker, arrowEdge: .bottom) {
                calendarPicker
                    .frame(width: 360)
                    .padding(12)
            }
            if calendarLoading {
                HStack(spacing: 4) {
                    ProgressView()
                        .controlSize(.small)
                    Text("Checking calendar…")
                        .font(.caption2)
                        .foregroundColor(.secondary)
                }
                .fixedSize()
            }
            if let suggested = suggestedCalendarEvent, linkedCalendarEvent == nil {
                Button { confirmCalendarMeeting(suggested) } label: {
                    Image(systemName: "checkmark.circle.fill")
                        .foregroundColor(.green)
                }
                .buttonStyle(.plain)
                .help("Confirm suggested meeting: \(suggested.title)")
                Button {
                    onRejectCalendarSuggestion?(audioPath)
                    suggestedCalendarEvent = nil
                    calendarCheckRejected = true
                    selectedCalendarCandidate = nil
                    showCalendarPicker = true
                } label: {
                    Image(systemName: "xmark.circle.fill")
                        .foregroundColor(.red)
                }
                .buttonStyle(.plain)
                .help("This is not the right meeting — choose another or ask Calendar Assistant")
            }
            Spacer()
        }
        .padding(.horizontal, 16)

        if calendarAssistantExpanded {
            calendarAssistantPanel
                .padding(.horizontal, 16)
                .transition(.opacity.combined(with: .move(edge: .top)))
        }

        if let rediarizeStatus {
            rediarizeStatusView(rediarizeStatus)
                .padding(.horizontal, 16)
        }

        // Keep the heading separate from the controls so the actions remain
        // visible in the narrow transcript pane. The order mirrors the
        // workflow: improve the current result, explicitly redetect, then
        // apply library or meeting-specific identity work.
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                if onRediarize != nil {
                    Button {
                        startRediarize(speakers: nil)
                    } label: {
                        Label("Refine", systemImage: "wand.and.stars")
                    }
                    .fixedSize()
                    .disabled(isRediarizing)
                    .help("Refine this transcript with the configured automatic diarizer. Existing confirmed and legacy-named people stay anchored; generic or provisional parts may be re-split, and oversized blocks are capped for readability.")

                    Button {
                        startRediarize(speakers: rediarizeNSpeakers)
                    } label: {
                        Label(isRediarizing ? "Redetecting…" : "Redetect", systemImage: "person.2.wave.2")
                    }
                    .fixedSize()
                    .disabled(isRediarizing)
                    .help("Redetect speakers from the audio using the expected count shown in the stepper. Confirmed and legacy-named people are preserved where the timestamps support them; other assignments may change.")

                }

                if onRematch != nil {
                    Button {
                        onRematch?(filePath)
                    } label: {
                        Label("Rematch", systemImage: "sparkle.magnifyingglass")
                    }
                    .fixedSize()
                    .help("Rematch only generic, unconfirmed speakers against the saved Voice Library. It uses stored embeddings when available, re-embeds older sidecars when needed, and leaves confirmed or named people unchanged.")
                }

                if onReclusterWithLabels != nil, hasAnchorNamedSpeakers {
                    Button {
                        showReassignOptions = true
                    } label: {
                        Label("Reassign", systemImage: "person.crop.circle.badge.checkmark")
                    }
                    .fixedSize()
                    .help("Use confirmed people in this meeting to correct unconfirmed turns. Choose the whole meeting or only the reviewed portion as evidence.")
                    .popover(isPresented: $showReassignOptions, arrowEdge: .bottom) {
                        reassignOptions
                            .frame(width: 315)
                            .padding(12)
                    }
                }
            }
            .buttonStyle(.bordered)
            .controlSize(.small)
            .padding(.horizontal, 16)
        }
      }
      .padding(.vertical, 6)
      .frame(maxWidth: .infinity, alignment: .leading)
      .background(Color.secondary.opacity(0.04))
    }

    private var calendarAssistantPanel: some View {
        VStack(spacing: 0) {
            HStack(spacing: 6) {
                Image(systemName: "calendar.badge.clock")
                Text("Calendar assistant")
                    .font(.subheadline.weight(.semibold))
                Text("Uses your connected CLI calendar MCP")
                    .font(.caption)
                    .foregroundColor(.secondary)
                Spacer()
                Button { calendarAssistantExpanded = false } label: {
                    Image(systemName: "xmark.circle.fill").foregroundColor(.secondary)
                }
                .buttonStyle(.plain)
                .help("Hide calendar assistant")
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 7)
            Divider()
            ScrollView {
                VStack(alignment: .leading, spacing: 8) {
                    if calendarAssistantMessages.isEmpty {
                        Text("Ask naturally—for example, “It was the BT Stream Leeds weekly meeting” or “show events around 3pm”.")
                            .font(.caption)
                            .foregroundColor(.secondary)
                            .padding(.vertical, 4)
                    }
                    ForEach(calendarAssistantMessages) { message in
                        Text(message.text)
                            .font(.caption)
                            .textSelection(.enabled)
                            .padding(7)
                            .background(message.role == .user ? Color.accentColor.opacity(0.13) : Color.secondary.opacity(0.10))
                            .clipShape(RoundedRectangle(cornerRadius: 7))
                            .frame(maxWidth: .infinity, alignment: message.role == .user ? .trailing : .leading)
                    }
                    if calendarAssistantRunning {
                        ProgressView("Searching calendar…")
                            .controlSize(.small)
                    }
                }
                .padding(8)
            }
            .frame(height: 155)
            Divider()
            HStack(spacing: 6) {
                TextField("Ask the calendar assistant…", text: $calendarAssistantDraft, axis: .vertical)
                    .textFieldStyle(.roundedBorder)
                    .lineLimit(1...3)
                    .onSubmit { sendCalendarAssistantMessage() }
                Button(action: sendCalendarAssistantMessage) {
                    Image(systemName: "arrow.up.circle.fill").font(.title3)
                }
                .buttonStyle(.plain)
                .disabled(calendarAssistantRunning || calendarAssistantDraft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
            .padding(8)
        }
        .background(Color(NSColor.controlBackgroundColor))
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.secondary.opacity(0.18)))
    }

    private func sendCalendarAssistantMessage() {
        let message = calendarAssistantDraft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !message.isEmpty, !calendarAssistantRunning, let turn = onCalendarAssistantTurn else { return }
        calendarAssistantDraft = ""
        calendarAssistantMessages.append(CalendarAssistantMessage(role: .user, text: message))
        calendarAssistantRunning = true
        turn(audioPath, Double(transcriptDurationSeconds), message, calendarAssistantSessionId) { reply, sessionId in
            DispatchQueue.main.async {
                calendarAssistantMessages.append(CalendarAssistantMessage(role: .assistant, text: reply))
                calendarAssistantSessionId = sessionId ?? calendarAssistantSessionId
                calendarAssistantRunning = false
            }
        }
    }

    private static let timeComponentFormatter: NumberFormatter = {
        let formatter = NumberFormatter()
        formatter.numberStyle = .none
        formatter.allowsFloats = false
        formatter.minimum = 0
        return formatter
    }()

    private var transcriptDurationSeconds: Int {
        Int(ceil(transcript.segments.map(\.end).max() ?? 0))
    }

    private var reviewedUntilValue: Double {
        Double(reviewedUntilMinutes * 60 + reviewedUntilSeconds)
    }

    private var reviewedUntilIsValid: Bool {
        reviewedUntilValue > 0 && reviewedUntilValue <= Double(transcriptDurationSeconds)
    }

    private var speakerToolsHelp: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Speaker tools")
                .font(.headline)
            Text("Refine").font(.subheadline.weight(.semibold))
            Text("Reruns automatic speaker detection while preserving confirmed labels where possible.")
                .font(.caption)
            Text("Redetect").font(.subheadline.weight(.semibold))
            Text("Reruns detection using the selected expected speaker count. Use only when the speaker breaks themselves need redoing.")
                .font(.caption)
            Text("Rematch").font(.subheadline.weight(.semibold))
            Text("Matches generic speakers against the saved Voice Library; confirmed labels stay unchanged.")
                .font(.caption)
            Text("Reassign").font(.subheadline.weight(.semibold))
            Text("Uses confirmed people in this meeting to correct other turns. In Reviewed until mode, every later label is cleared and reassessed.")
                .font(.caption)
        }
    }

    private func loadCalendarCandidates() {
        guard !calendarLoading else { return }
        calendarLoading = true
        calendarCandidates = []
        // Microsoft 365 is the source the user connected for this workflow.
        // The previous dual Mac-Calendar/MCP fan-out could paint the local
        // empty result while the MCP response was still on its way, hiding a
        // valid M365 event. Keep one authoritative request for this compact
        // picker; the assistant remains available for broader searches.
        guard let search = onFindClaudeCalendarEvents else {
            calendarLoading = false
            return
        }
        search(audioPath, Double(transcriptDurationSeconds)) { events in
            DispatchQueue.main.async {
                calendarCandidates = events.sorted { $0.start < $1.start }
                // Existing links created before attendee enrichment may have
                // only a title/time saved.  Refresh their visible invitees
                // when the popup opens, without changing the user's chosen
                // meeting or launching another diarisation run.
                if let linked = linkedCalendarEvent,
                   let enriched = events.first(where: {
                       $0.title.caseInsensitiveCompare(linked.title) == .orderedSame
                           && !$0.attendeeNames.isEmpty
                   }) {
                    linkedCalendarEvent = CalendarMeetingCandidate(
                        id: linked.id,
                        title: linked.title,
                        start: linked.start,
                        end: linked.end,
                        attendeeNames: enriched.attendeeNames
                    )
                }
                // A single time-overlapping result is useful as a suggestion,
                // never as an automatic link. Multiple possibilities keep the
                // neutral Match meeting label until the reviewer chooses one.
                if events.count == 1, linkedCalendarEvent == nil {
                    suggestedCalendarEvent = events[0]
                }
                calendarLoading = false
            }
        }
    }

    private func confirmCalendarMeeting(_ event: CalendarMeetingCandidate) {
        linkedCalendarEvent = event
        suggestedCalendarEvent = event
        selectedCalendarCandidate = nil
        if event.attendeeCount >= 2 { rediarizeNSpeakers = event.attendeeCount }
        // Save the calendar context before the speaker pass.  The sidecar
        // owns this particular run so its `@State` transcript updates in
        // place rather than leaving the reviewer with stale speaker blocks.
        onLinkCalendarEvent?(audioPath, Double(transcriptDurationSeconds), event, false)
        if allSpeakersConfirmed {
            rediarizeStatus = .skipped("Calendar linked — all speakers are confirmed, so their assignments were left untouched.")
        } else {
            startRediarize(speakers: event.attendeeCount >= 2 ? event.attendeeCount : nil)
        }
        showCalendarPicker = false
    }

    @ViewBuilder
    private var calendarPicker: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Match this meeting")
                .font(.headline)
            Text("Looking in your connected Microsoft 365 calendar. Attendees are only a speaker-count hint—you stay in control.")
                .font(.caption)
                .foregroundColor(.secondary)
            if let linked = linkedCalendarEvent {
                VStack(alignment: .leading, spacing: 6) {
                    Label("Meeting confirmed", systemImage: "checkmark.circle.fill")
                        .font(.caption.weight(.semibold))
                        .foregroundColor(.green)
                    Text(linked.title)
                        .font(.subheadline.weight(.semibold))
                    Text("\(linked.start.formatted(date: .omitted, time: .shortened)) – \(linked.end.formatted(date: .omitted, time: .shortened)) · \(linked.attendeeSummary)")
                        .font(.caption)
                        .foregroundColor(.secondary)
                    if !linked.attendeeNames.isEmpty {
                        Text("Invited: \(linked.attendeeNames.joined(separator: ", "))")
                            .font(.caption)
                            .foregroundColor(.secondary)
                            .lineLimit(2)
                    }
                    Button("Change meeting…") {
                        linkedCalendarEvent = nil
                        suggestedCalendarEvent = nil
                        selectedCalendarCandidate = nil
                    }
                    .font(.caption)
                    Button("Remove meeting", role: .destructive) {
                        onRemoveCalendarEvent?(audioPath)
                        linkedCalendarEvent = nil
                        suggestedCalendarEvent = nil
                        selectedCalendarCandidate = nil
                    }
                    .font(.caption)
                }
            } else {
            if let suggested = suggestedCalendarEvent {
                VStack(alignment: .leading, spacing: 7) {
                    Label("Suggested match", systemImage: "sparkles")
                        .font(.caption.weight(.semibold))
                        .foregroundColor(.secondary)
                    Text(suggested.title)
                        .font(.subheadline.weight(.semibold))
                    Text("\(suggested.start.formatted(date: .omitted, time: .shortened)) – \(suggested.end.formatted(date: .omitted, time: .shortened)) · \(suggested.attendeeSummary)")
                        .font(.caption)
                        .foregroundColor(.secondary)
                    if !suggested.attendeeNames.isEmpty {
                        Text("Invited: \(suggested.attendeeNames.joined(separator: ", "))")
                            .font(.caption)
                            .foregroundColor(.secondary)
                            .lineLimit(2)
                    }
                    HStack {
                        Button("Confirm meeting") { confirmCalendarMeeting(suggested) }
                            .buttonStyle(.borderedProminent)
                        Button("Find another") {
                            suggestedCalendarEvent = nil
                            selectedCalendarCandidate = nil
                        }
                    }
                    Text("Confirmation saves this event as context and starts the speaker pass using its attendee hint.")
                        .font(.caption2)
                        .foregroundColor(.secondary)
                    Divider().padding(.top, 1)
                }
            }
            if calendarLoading {
                HStack(spacing: 6) {
                    ProgressView()
                        .controlSize(.small)
                    Text("Checking calendar…")
                        .font(.caption2)
                        .foregroundColor(.secondary)
                }
            } else if calendarCandidates.isEmpty {
                if calendarCheckRejected {
                    Label("Calendar checked — no meeting linked", systemImage: "calendar.badge.exclamationmark")
                        .font(.caption.weight(.medium))
                        .foregroundColor(.secondary)
                    Text("Speaker matching has already continued without calendar context.")
                        .font(.caption2)
                        .foregroundColor(.secondary)
                    Button("Check calendar again") {
                        onClearCalendarRejection?(audioPath)
                        calendarCheckRejected = false
                        calendarCandidates = []
                        loadCalendarCandidates()
                    }
                    .buttonStyle(.bordered)
                } else {
                    Text("No matching event found.")
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
                Button("Ask Calendar assistant…") {
                    showCalendarPicker = false
                    calendarAssistantExpanded = true
                }
            } else {
                ForEach(calendarCandidates.filter { $0.id != suggestedCalendarEvent?.id }) { event in
                    Button {
                        selectedCalendarCandidate = event
                    } label: {
                        HStack(spacing: 8) {
                            Image(systemName: selectedCalendarCandidate?.id == event.id ? "checkmark.circle.fill" : "circle")
                                .foregroundColor(selectedCalendarCandidate?.id == event.id ? .accentColor : .secondary)
                            VStack(alignment: .leading, spacing: 2) {
                                Text(event.title.isEmpty ? "Untitled event" : event.title)
                                    .font(.subheadline.weight(.medium))
                                Text("\(event.start.formatted(date: .omitted, time: .shortened)) – \(event.end.formatted(date: .omitted, time: .shortened)) · \(event.attendeeSummary)")
                                    .font(.caption)
                                    .foregroundColor(.secondary)
                            }
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.vertical, 4)
                    }
                    .buttonStyle(.plain)
                }
                if let chosen = selectedCalendarCandidate {
                    Divider()
                    HStack {
                        Text("Selected: \(chosen.title)")
                            .font(.caption)
                            .lineLimit(1)
                        Spacer()
                        Button("Confirm meeting") { confirmCalendarMeeting(chosen) }
                            .buttonStyle(.borderedProminent)
                    }
                }
                Divider()
                Button("Ask Calendar assistant…") {
                    showCalendarPicker = false
                    calendarAssistantExpanded = true
                }
                .font(.caption)
            }
            }
        }
        .onAppear {
            linkedCalendarEvent = onLoadCalendarEvent?(audioPath)
            suggestedCalendarEvent = linkedCalendarEvent == nil ? onLoadCalendarSuggestion?(audioPath) : nil
            calendarCheckRejected = linkedCalendarEvent == nil
                && suggestedCalendarEvent == nil
                && (onLoadCalendarRejection?(audioPath) ?? false)
            if linkedCalendarEvent == nil && suggestedCalendarEvent == nil && !calendarCheckRejected && calendarCandidates.isEmpty {
                loadCalendarCandidates()
            }
        }
    }

    @ViewBuilder
    private var reassignOptions: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Reassign unconfirmed turns")
                .font(.headline)
            Text("Choose which reviewed labels should guide the rest of this meeting.")
                .font(.caption)
                .foregroundColor(.secondary)
            Picker("Evidence", selection: $reassignScope) {
                ForEach(ReassignScope.allCases) { scope in
                    Text(scope.rawValue).tag(scope)
                }
            }
            .pickerStyle(.radioGroup)

            if reassignScope == .reviewedThrough {
                HStack(spacing: 6) {
                    Text("Reviewed until")
                    TextField("0", value: $reviewedUntilMinutes, formatter: Self.timeComponentFormatter)
                        .textFieldStyle(.roundedBorder)
                        .frame(width: 38)
                    Text("min")
                        .font(.caption)
                    Stepper("", value: $reviewedUntilMinutes, in: 0...max(0, transcriptDurationSeconds / 60))
                        .labelsHidden()
                        .controlSize(.small)
                    TextField("0", value: $reviewedUntilSeconds, formatter: Self.timeComponentFormatter)
                        .textFieldStyle(.roundedBorder)
                        .frame(width: 38)
                    Text("sec")
                        .font(.caption)
                    Stepper("", value: $reviewedUntilSeconds, in: 0...59)
                        .labelsHidden()
                        .controlSize(.small)
                }
                Text("Labels up to this time stay fixed. All later labels are treated as unreviewed, then reassessed against them.")
                    .font(.caption2)
                    .foregroundColor(.secondary)
                if !reviewedUntilIsValid {
                    Text("Choose a time within this recording.")
                        .font(.caption2)
                        .foregroundColor(.red)
                }
            }

            HStack {
                Spacer()
                Button("Cancel") { showReassignOptions = false }
                Button("Reassign") {
                    let cutoff = reassignScope == .reviewedThrough ? reviewedUntilValue : nil
                    onReclusterWithLabels?(filePath, cutoff)
                    showReassignOptions = false
                }
                .disabled(reassignScope == .reviewedThrough && !reviewedUntilIsValid)
                .keyboardShortcut(.defaultAction)
            }
        }
    }

    @ViewBuilder
    private func rediarizeStatusView(_ status: TranscriptRediarizeStatus) -> some View {
        switch status {
        case .running:
            HStack(spacing: 6) {
                ProgressView()
                    .controlSize(.small)
                Text("Re-diarising… the transcript will update here when it finishes.")
            }
            .font(.caption)
            .foregroundColor(.secondary)
        case .completed(let summary, _):
            Label {
                if summary.hasChanges {
                    Text("Re-diarisation complete · \(summary.beforeSpeakerCount) → \(summary.afterSpeakerCount) speakers · \(summary.changedSegmentAssignments) segment assignments changed")
                } else {
                    Text("Re-diarisation complete · no changes")
                }
            } icon: {
                Image(systemName: summary.hasChanges ? "checkmark.circle.fill" : "equal.circle.fill")
            }
            .font(.caption)
            .foregroundColor(summary.hasChanges ? .green : .secondary)
        case .skipped(let message):
            Label(message, systemImage: "checkmark.shield.fill")
                .font(.caption)
                .foregroundColor(.green)
        case .failed(let message):
            Label(message.isEmpty ? "Re-diarisation failed" : "Re-diarisation failed: \(message)", systemImage: "exclamationmark.triangle.fill")
                .font(.caption)
                .foregroundColor(.red)
                .lineLimit(2)
        }
    }

    private func applyRediarizedTranscript(_ updatedTranscript: DiarizedTranscript) {
        audioPlayer.stop()
        transcript = updatedTranscript
        transcriptHistory.removeAll()
        speakerFilter = nil
        selection = nil
        editingSpeakerId = nil
        syncRediarizeSpeakerCount()
        refreshConfidence()
        refreshLibraryNames()
    }

    // MARK: - Stats Header

    private var statsHeader: some View {
        // Compact: duration + a full-width talk-time proportion bar + speaker
        // count. (The old per-speaker "dot + name %/wpm" list overflowed into a
        // meaningless row of dots when a meeting had many detected speakers.)
        let totalTalk = speakerStats.reduce(0.0) { $0 + $1.talkTime }
        // Duration + speaker count take their intrinsic width FIRST (fixedSize),
        // then the proportion bar fills whatever's left — GeometryReader is
        // greedy, so if it came first it swallowed the row and the count
        // overlapped it.
        return HStack(spacing: 10) {
            Text(formatTime(seconds: totalDuration))
                .font(.caption.weight(.medium))
                .foregroundColor(.secondary)
                .fixedSize()

            Text("\(speakerStats.count) speaker\(speakerStats.count == 1 ? "" : "s")")
                .font(.caption)
                .foregroundColor(.secondary)
                .fixedSize()

            GeometryReader { geo in
                HStack(spacing: 1) {
                    ForEach(speakerStats, id: \.speakerId) { stat in
                        let frac = totalTalk > 0 ? stat.talkTime / totalTalk : 0
                        RoundedRectangle(cornerRadius: 2)
                            .fill(colorForSpeaker(stat.speakerId).opacity(0.75))
                            .frame(width: max(1, geo.size.width * CGFloat(frac)))
                    }
                }
            }
            .frame(height: 8)
            .help("Talk-time split by speaker")
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 6)
    }

    // MARK: - Subviews

    /// One-line done state shown in place of the verification panel once
    /// nothing needs review. "Review" re-expands the full panel.
    private var allVerifiedBanner: some View {
        HStack(spacing: 6) {
            Image(systemName: "checkmark.seal.fill")
                .foregroundColor(.green)
            Text("All speakers verified")
                .font(.caption.weight(.semibold))
            Button("Review") { verifyPanelExpanded = true }
                .font(.caption2)
                .buttonStyle(.borderless)
                .foregroundColor(.accentColor)
                .help("Show the speaker verification panel again")
            Spacer()
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 6)
        .background(Color.green.opacity(0.06))
    }

    private var speakerVerifyPanel: some View {
        let clearable = clearableUnverifiedSpeakerIds
        return VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 6) {
                Image(systemName: "checkmark.seal")
                    .foregroundColor(.blue)
                Text("Verify speakers")
                    .font(.caption.weight(.semibold))
                Text("Confirm each voice to lock it in — this also teaches your voice library.")
                    .font(.caption2)
                    .foregroundColor(.secondary)
                    .lineLimit(1)
                Spacer(minLength: 8)
                if strongSuggestionIds.count >= 2 {
                    Button {
                        // Snapshot first: each confirmation mutates liveSuggestions.
                        let batch = strongSuggestionIds
                        for id in batch {
                            if let s = liveSuggestions["\(id)"] {
                                confirmCandidateSuggestion(id, suggestion: s)
                            }
                        }
                    } label: {
                        Label("Confirm \(strongSuggestionIds.count) strong", systemImage: "checkmark.circle.fill")
                    }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.small)
                    .tint(.green)
                    .help("Accept every gate-passing suggestion in one click. Weaker closest-voice candidates still need individual review.")
                }
                // Bulk undo for bad auto-matches — only when 2+ unverified
                // speakers can be cleared (single-row × is enough otherwise).
                if clearable.count >= 2 {
                    Button {
                        confirmClearAllSpeakers = true
                    } label: {
                        Label("Clear all", systemImage: "xmark.circle")
                            .font(.caption)
                    }
                    .buttonStyle(.borderless)
                    .controlSize(.small)
                    .help("Clear all unconfirmed names — revert each to Speaker N. Confirmed speakers are left alone.")
                    .confirmationDialog(
                        "Clear all unconfirmed speakers?",
                        isPresented: $confirmClearAllSpeakers,
                        titleVisibility: .visible
                    ) {
                        Button("Clear all", role: .destructive) {
                            clearAllUnverifiedSpeakers()
                        }
                        Button("Cancel", role: .cancel) {}
                    } message: {
                        Text("Revert \(clearable.count) unconfirmed auto-matches to Speaker 1/2/…. Confirmed names stay locked in.")
                    }
                }
                // Finish review without naming: marks every still-open speaker
                // as verified-unknown (Speaker N). That sets speakersTagged so
                // the orange "needs tagging" nag clears. Does not enroll.
                if canMarkAllUnknown {
                    Button {
                        confirmMarkAllUnknown = true
                    } label: {
                        Label("Mark all unknown", systemImage: "person.crop.circle.badge.questionmark")
                            .font(.caption)
                    }
                    .buttonStyle(.borderless)
                    .controlSize(.small)
                    .help("Everyone left is a guest — mark the meeting reviewed without names. Clears the orange needs-tagging badge. Does not add to your voice library.")
                    .confirmationDialog(
                        "Mark all remaining speakers unknown?",
                        isPresented: $confirmMarkAllUnknown,
                        titleVisibility: .visible
                    ) {
                        Button("Mark all unknown", role: .destructive) {
                            markAllSpeakersUnknown()
                        }
                        Button("Cancel", role: .cancel) {}
                    } message: {
                        Text("Sets every unconfirmed speaker to Speaker N and marks them reviewed. Confirmed real names stay. Nothing is enrolled in the voice library.")
                    }
                }
                if !needsVerification {
                    Button("Collapse") { verifyPanelExpanded = false }
                        .font(.caption2)
                        .buttonStyle(.borderless)
                        .foregroundColor(.accentColor)
                        .help("Back to the one-line verified summary")
                }
                if suggestionsLoading {
                    ProgressView()
                        .controlSize(.small)
                    Text("Checking voices…")
                        .font(.caption2)
                        .foregroundColor(.secondary)
                }
            }
            // The surrounding VSplitView controls the panel's height. Keep the
            // rows scrollable within that user-sized pane for larger meetings.
            ScrollView {
                VStack(alignment: .leading, spacing: 4) {
                    ForEach(uniqueSpeakerIds, id: \.self) { id in
                        speakerVerifyRow(id: id)
                    }
                }
            }
            .frame(maxHeight: .infinity)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 8)
        .background(Color.blue.opacity(0.04))
    }

    /// Unverified speakers whose suggestion passed the conservative gate —
    /// the ones the bulk-confirm button accepts in one click.
    private var strongSuggestionIds: [Int] {
        uniqueSpeakerIds.filter { id in
            guard !(speakerMeta(for: id)?.verified ?? false),
                  let s = liveSuggestions["\(id)"],
                  s.decision == "strong_review",
                  s.proposedName != nil else { return false }
            return true
        }
    }

    private var speakerVerifyPanelMaximumHeight: CGFloat { 260 }

    /// Size small meetings to their content, but cap larger meetings so the
    /// transcript always opens with useful space. Extra speaker rows remain
    /// available in the review panel's own ScrollView.
    private var speakerVerifyPanelIdealHeight: CGFloat {
        let count = max(1, uniqueSpeakerIds.count)
        let rowHeight: CGFloat = 38
        let rowSpacing: CGFloat = 4
        let headerAndPadding: CGFloat = 54
        return min(
            speakerVerifyPanelMaximumHeight,
            max(
                128,
            CGFloat(count) * rowHeight
                + CGFloat(max(0, count - 1)) * rowSpacing
                + headerAndPadding
            )
        )
    }

    @ViewBuilder
    private var transcriptContent: some View {
        VStack(spacing: 0) {
            // "Listening to one speaker" banner — click Show all to clear.
            if let f = speakerFilter {
                HStack(spacing: 8) {
                    Image(systemName: "waveform.circle.fill")
                        .foregroundColor(colorForSpeaker(f))
                    Text("Showing only \(speakerName(for: f)) — play through to check the voice")
                        .font(.caption)
                        .foregroundColor(.secondary)
                    Spacer()
                    Button("Show all") { speakerFilter = nil }
                        .controlSize(.small)
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 6)
                .background(colorForSpeaker(f).opacity(0.08))
                Divider()
            }

            if let activeSelection = selection {
                inlineSpeakerBar(selection: activeSelection)
                Divider()
            }

            // Segments list (narrowed to one speaker when a filter is active).
            // A word-range edit replaces the affected segment(s), invalidating
            // SwiftUI's native scroll-position retention. Restore to the
            // edited timestamp rather than unexpectedly jumping to the start.
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 4) {
                        ForEach(Array(transcript.segments.enumerated()), id: \.element.id) { idx, segment in
                            if speakerFilter == nil || segment.speakerId == speakerFilter {
                                segmentRow(segmentIndex: idx, segment: segment)
                                    .id(segment.id)
                            }
                        }
                    }
                    .padding(.horizontal, 16)
                    .padding(.vertical, 8)
                    .onPreferenceChange(TranscriptWordFramesKey.self) { frames in
                        transcriptWordFrames = frames
                    }
                    .simultaneousGesture(transcriptSelectionGesture)
                }
                .coordinateSpace(name: "transcriptWords")
                .onChange(of: pendingTranscriptRestoreTime) { restoreTime in
                    guard let restoreTime else { return }
                    let visible = transcript.segments.filter {
                        speakerFilter == nil || $0.speakerId == speakerFilter
                    }
                    guard let nearest = visible.min(by: {
                        abs($0.start - restoreTime) < abs($1.start - restoreTime)
                    }) else { return }
                    // Let the split rows enter the layout before scrolling to
                    // their fresh identity.
                    DispatchQueue.main.async {
                        proxy.scrollTo(nearest.id, anchor: .center)
                        pendingTranscriptRestoreTime = nil
                    }
                }
            }
            .contextMenu {
                if let selected = selection {
                    Button("New speaker") {
                        applySelection(selection: selected, newSpeakerId: nextNewSpeakerId())
                        selection = nil
                    }
                    Button("Name new speaker…") {
                        selectionPersonQuery = ""
                        pendingNamedSelection = selected
                    }
                    Menu("Assign to existing speaker") {
                        ForEach(uniqueSpeakerIds, id: \.self) { speakerId in
                            Button(speakerName(for: speakerId)) {
                                applySelection(selection: selected, newSpeakerId: speakerId)
                                selection = nil
                            }
                        }
                    }
                } else {
                    Text("Select transcript words first")
                }
            }
        }
    }

    private var transcriptSelectionGesture: some Gesture {
        DragGesture(minimumDistance: 0, coordinateSpace: .named("transcriptWords"))
            .onChanged { value in
                guard let position = transcriptWordPosition(at: value.location) else { return }
                if selectionDragStart == nil {
                    selectionDragStart = position
                }
                selection = SegmentSelection(anchor: selectionDragStart!, focus: position)
            }
            .onEnded { _ in
                selectionDragStart = nil
            }
    }

    private func transcriptWordPosition(at point: CGPoint) -> WordPosition? {
        transcriptWordFrames.first(where: { $0.value.contains(point) })?.key
    }

    @ViewBuilder
    private func speakerVerifyRow(id: Int) -> some View {
        let name = speakerName(for: id)
        let prov = provenance(for: id)
        let verified = speakerMeta(for: id)?.verified ?? false

        HStack(spacing: 8) {
            speakerPill(speakerId: id, interactive: true, context: "verify")   // tap to rename/correct

            // "Speaker N" already communicates that this is unnamed; repeating
            // that as a second chip costs space without adding information.
            if !isGenericName(name) {
                Text(prov.text)
                    .font(.caption2.weight(.medium))
                    .padding(.horizontal, 6)
                    .padding(.vertical, 2)
                    .background(prov.color.opacity(0.15), in: Capsule())
                    .foregroundColor(prov.color)
            }

            // Live voice-match confidence against the enrolled voice of this name.
            // Once a person is confirmed, their identity is the decision — a
            // competing "sounds like" hint is noise and can undermine it.
            if !verified, !isGenericName(name), let badge = confidenceBadge(for: id) {
                Text(badge.text)
                    .font(.caption2.weight(.medium))
                    .padding(.horizontal, 6)
                    .padding(.vertical, 2)
                    .background(badge.color.opacity(0.15), in: Capsule())
                    .foregroundColor(badge.color)
                    .help("How closely this speaker's voice matches the enrolled voice for \(speakerName(for: id)).")
            }

            if !verified, let suggestion = liveSuggestions["\(id)"] {
                compactSuggestionEvidence(suggestion)
            } else if !verified && suggestionsLoading {
                Text("Checking WeSpeaker…")
                    .font(.caption2)
                    .foregroundColor(.secondary)
            }

            Spacer()

            // Listen to just this speaker to check the voice is really theirs.
            Button {
                speakerFilter = (speakerFilter == id) ? nil : id
            } label: {
                Image(systemName: speakerFilter == id ? "waveform.circle.fill" : "waveform.circle")
            }
            .buttonStyle(.plain)
            .foregroundColor(speakerFilter == id ? .accentColor : .secondary)
            .help("Show only \(speakerName(for: id))'s segments so you can play through and check the voice.")

            // Clear: undo auto/typed assignment → back to "Speaker N", drop
            // provenance + confidence chips. (Unlike the old "Mark unknown",
            // this does not count as reviewed — rematch/confirm can run again.)
            if canClearSpeaker(id) {
                Button {
                    clearSpeakerAssignment(id)
                } label: {
                    Image(systemName: "xmark.circle")
                }
                .buttonStyle(.plain)
                .foregroundColor(.secondary)
                .help("Clear this name — revert to Speaker \(id + 1) and remove the auto-match.")
            }

            if let partner = duplicatePartner(for: id) {
                // Same name as an earlier speaker — offer to merge them into one.
                Button {
                    pendingMerge = PendingMerge(from: id, to: partner, name: speakerName(for: id))
                } label: {
                    Label("Merge duplicate", systemImage: "arrow.triangle.merge")
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.small)
                .tint(.orange)
                .help("This name is assigned to two speakers — merge them into one person.")
                speakerActionsMenu(id: id)
            } else if verified {
                Label("Verified", systemImage: "checkmark.seal.fill")
                    .font(.caption2)
                    .foregroundColor(.green)
            } else if let suggestion = liveSuggestions["\(id)"], suggestion.proposedName != nil {
                Button("Confirm this name") {
                    confirmCandidateSuggestion(id, suggestion: suggestion)
                }
                .buttonStyle(.bordered)
                .controlSize(.small)
                .help("You are confirming this identity. The model cannot apply it by itself.")
            } else if !isGenericName(name) {
                Button {
                    confirmSpeaker(id)
                } label: {
                    Label("Confirm", systemImage: "checkmark")
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.small)
                .help("Accept this name, lock it in, and reinforce it in your voice library.")
                speakerActionsMenu(id: id)
            } else {
                Button {
                    markUnknown(id)
                } label: {
                    Text("Mark unknown")
                }
                .buttonStyle(.bordered)
                .controlSize(.small)
                .help("Acknowledge an unknown/guest speaker — counts as reviewed, not added to your voice library. Rename via the pill if you know who it is.")
            }
        }
    }

    /// One compact, informational treatment of WeSpeaker output. The primary
    /// decision is rendered separately at the far right of the row.
    @ViewBuilder
    private func compactSuggestionEvidence(_ suggestion: SpeakerSuggestion) -> some View {
        if suggestion.decision == "hold" {
            Text("WeSpeaker: manual review")
                .font(.caption2)
                .foregroundColor(.secondary)
                .help(suggestionHelp(suggestion))
        } else if let proposed = suggestion.proposedName {
            HStack(spacing: 4) {
                Image(systemName: "person.crop.circle.badge.questionmark")
                    .font(.caption2)
                    .foregroundColor(suggestion.decision == "strong_review" ? .green : .blue)
                Text(proposed)
                    .font(.caption.weight(.semibold))
                calendarBadge(suggestion.inCalendar)
                if let score = suggestion.similarity {
                    Text("\(Int((score * 100).rounded()))%")
                        .font(.caption2.monospacedDigit())
                        .foregroundColor(.secondary)
                }
            }
            .help(suggestionHelp(suggestion))
        }
    }

    /// Secondary per-speaker actions behind a ⋯ menu, so each row carries a
    /// single primary action instead of a row of competing buttons.
    private func speakerActionsMenu(id: Int) -> some View {
        Menu {
            Button("Mark unknown") { markUnknown(id) }
        } label: {
            Image(systemName: "ellipsis.circle")
        }
        .menuStyle(.borderlessButton)
        .menuIndicator(.hidden)
        .fixedSize()
        .foregroundColor(.secondary)
        .help("More actions for \(speakerName(for: id))")
    }

    /// Calendar support for a proposed name: plain calendar when the event's
    /// attendee list includes them, warning mark when it doesn't.
    private func calendarBadge(_ inCalendar: Bool?) -> some View {
        Group {
            if let inCalendar {
                Image(systemName: inCalendar ? "calendar" : "calendar.badge.exclamationmark")
                    .font(.caption2)
                    .foregroundColor(inCalendar ? .accentColor : .orange)
                    .help(inCalendar
                        ? "In the meeting's calendar event"
                        : "Not in the meeting's attendee list — held for your review")
            }
        }
    }

    @ViewBuilder
    private func speakerSuggestionRow(id: Int, suggestion: SpeakerSuggestion) -> some View {
        if suggestion.decision == "hold" {
            HStack(spacing: 6) {
                Image(systemName: "waveform.badge.exclamationmark")
                    .foregroundColor(.secondary)
                Text("WeSpeaker held this speaker for manual review")
                    .font(.caption2)
                    .foregroundColor(.secondary)
            }
            .padding(.leading, 26)
        } else if let proposed = suggestion.proposedName {
            if suggestion.decision == "strong_review" {
                // Gate-passing suggestion: name + confidence + confirm. Margin,
                // meetings, and runner-up stay in the tooltip (suggestionHelp).
                HStack(spacing: 7) {
                    Image(systemName: "person.crop.circle.badge.questionmark.fill")
                        .foregroundColor(.green)
                    Text(proposed)
                        .font(.caption.weight(.semibold))
                    calendarBadge(suggestion.inCalendar)
                    if let score = suggestion.similarity {
                        Text("\(Int((score * 100).rounded()))%")
                            .font(.caption2.monospacedDigit())
                            .foregroundColor(.secondary)
                    }
                    Spacer()
                    Button("Confirm suggestion") {
                        confirmCandidateSuggestion(id, suggestion: suggestion)
                    }
                    .buttonStyle(.bordered)
                    .controlSize(.small)
                    .help("You are confirming this identity. The model cannot apply it by itself.")
                }
                .padding(.leading, 26)
                .help(suggestionHelp(suggestion))
            } else {
                // Weak candidates stay hidden until asked for — an unverified
                // name on screen can anchor the reviewer into confirming it.
                // Exception: when the quick library guess on this row names
                // someone DIFFERENT, the contradiction is exactly what the
                // reviewer needs to see, so show it (and the badge is
                // suppressed, leaving one name on screen).
                let contradictsLibraryGuess: Bool = {
                    guard let best = liveConfidence["\(id)"]?.best else { return false }
                    return best.caseInsensitiveCompare(proposed) != .orderedSame
                }()
                if contradictsLibraryGuess || revealedWeakSuggestions.contains("\(id)") {
                    HStack(spacing: 7) {
                        Image(systemName: "person.crop.circle.badge.questionmark")
                            .foregroundColor(.blue)
                        Text(proposed)
                            .font(.caption.weight(.semibold))
                        calendarBadge(suggestion.inCalendar)
                        if let score = suggestion.similarity {
                            Text("\(Int((score * 100).rounded()))%")
                                .font(.caption2.monospacedDigit())
                                .foregroundColor(.secondary)
                        }
                        Spacer()
                        Button("Confirm this name") {
                            confirmCandidateSuggestion(id, suggestion: suggestion)
                        }
                        .buttonStyle(.bordered)
                        .controlSize(.small)
                        .help("You are confirming this identity. The model cannot apply it by itself.")
                    }
                    .padding(.leading, 26)
                    .help(suggestionHelp(suggestion))
                } else {
                    HStack(spacing: 6) {
                        Image(systemName: "person.crop.circle.badge.questionmark")
                            .foregroundColor(.blue)
                        Button("Closest match found — view") {
                            revealedWeakSuggestions.insert("\(id)")
                        }
                        .font(.caption2)
                        .buttonStyle(.borderless)
                        .foregroundColor(.accentColor)
                    }
                    .padding(.leading, 26)
                    .help("Did not pass the conservative gate. Reveal the candidate only if you want it — it is hidden so it cannot anchor your judgement.")
                }
            }
        }
    }

    private func suggestionHelp(_ suggestion: SpeakerSuggestion) -> String {
        var parts = [suggestion.decision == "strong_review"
                     ? "Passed the conservative review gate — no name has been applied."
                     : "Did not pass the conservative gate; shown only as the closest candidate for manual review."]
        if let runnerUp = suggestion.runnerUp {
            parts.append("Runner-up: \(runnerUp).")
        }
        if !suggestion.reasons.isEmpty {
            parts.append("Warnings: \(suggestion.reasons.joined(separator: ", ")).")
        }
        if let reason = suggestion.audioReason {
            parts.append("Audio: \(reason).")
        }
        return parts.joined(separator: " ")
    }

    @ViewBuilder
    private func speakerPill(speakerId: Int, interactive: Bool, context: String = "legend") -> some View {
        let color = colorForSpeaker(speakerId)

        // Only the pill in the SAME place you clicked shows the editor — without
        // the context check, editingSpeakerId matched every pill for that
        // speaker (legend + each transcript row), so the field appeared down in
        // the transcript instead of where you tapped.
        if editingSpeakerId == speakerId && editingContext == context {
            // Keep the editor at the place where the user clicked. The
            // suggestions themselves live in a popover so they float above
            // the transcript rather than consuming space inside its scroll
            // view (especially important for speaker pills in segment rows).
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 4) {
                    Circle()
                        .fill(color)
                        .frame(width: 8, height: 8)
                    TextField("Name", text: $editingName, onCommit: {
                        commitRename(speakerId: speakerId)
                    })
                    .textFieldStyle(.roundedBorder)
                    .frame(width: 140)
                    .controlSize(.small)
                    .focused($nameFieldFocused)
                    .onAppear { nameFieldFocused = true }
                }
                .padding(.horizontal, 8)
                .padding(.vertical, 4)
                .background(color.opacity(0.15))
                .cornerRadius(12)
            }
            .popover(
                isPresented: Binding(
                    get: { editingSpeakerId == speakerId && editingContext == context },
                    set: { presented in
                        if !presented, editingSpeakerId == speakerId {
                            // Dismissing the popover is equivalent to clicking
                            // away from the editor: save the current choice and
                            // close the inline editor as before.
                            commitRename(speakerId: speakerId)
                        }
                    }
                ),
                arrowEdge: .top
            ) {
                nameSuggestions(for: speakerId)
                    .frame(minWidth: 260, alignment: .leading)
                    .padding(4)
            }
        } else if interactive {
            Button {
                editingSpeakerId = speakerId
                editingContext = context
                editingName = speakerName(for: speakerId)
                nameFieldFocused = true
                // Refresh enrolled names when opening the editor so the
                // dropdown is current (library may have grown since appear).
                refreshLibraryNames()
            } label: {
                speakerPillLabel(speakerId: speakerId)
            }
            .buttonStyle(.plain)
        } else {
            // This form is used inside an outer assignment button. It must be
            // a label, not another Button, otherwise the inner control eats
            // the click and the assignment action never runs.
            speakerPillLabel(speakerId: speakerId)
        }
    }

    private func speakerPillLabel(speakerId: Int) -> some View {
        let name = speakerName(for: speakerId)
        let color = colorForSpeaker(speakerId)
        return HStack(spacing: 4) {
            Circle()
                .fill(color)
                .frame(width: 8, height: 8)
            Text(name)
                .font(.caption)
                .fontWeight(.medium)
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 4)
        .background(color.opacity(0.15))
        .cornerRadius(12)
    }

    @ViewBuilder
    private func segmentRow(segmentIndex idx: Int, segment: DiarizedSegment) -> some View {
        let timedWords = segment.words
        let words = timedWords?.map(\.text)
            ?? segment.text.split(separator: " ", omittingEmptySubsequences: false).map(String.init)

        VStack(alignment: .leading, spacing: 2) {
            HStack(alignment: .top, spacing: 8) {
                // Play button
                Button {
                    if audioPlayer.playingSegmentId == segment.id {
                        audioPlayer.stop()
                    } else {
                        audioPlayer.play(
                            audioPath: audioPath,
                            start: segment.start,
                            end: segment.end,
                            segmentId: segment.id,
                            wordCount: words.count,
                            wordTimings: timedWords
                        )
                    }
                } label: {
                    Image(systemName: audioPlayer.playingSegmentId == segment.id ? "stop.circle.fill" : "play.circle")
                        .foregroundColor(audioPlayer.playingSegmentId == segment.id ? .blue : .secondary)
                }
                .buttonStyle(.plain)
                .frame(width: 18)

                Text("[\(formatTime(seconds: segment.start))]")
                    .font(.system(.caption, design: .monospaced))
                    .foregroundColor(.secondary)
                    .frame(width: 50, alignment: .leading)

                if hasSpeakers {
                    // Editable here too — a unique per-row context means the
                    // editor opens on THIS pill, not the legend or another row.
                    speakerPill(speakerId: segment.speakerId, interactive: true, context: "segment-\(idx)")
                        .frame(width: transcriptSpeakerColumnWidth, alignment: .leading)
                        .clipped()

                }

                WordTokensView(
                    segmentIndex: idx,
                    words: words,
                    selection: selection,
                    playingWord: audioPlayer.playingSegmentId == segment.id ? audioPlayer.playingWordIndex : nil
                )

                Spacer(minLength: 0)
            }
            .padding(.vertical, 2)
        }
        // Detail tabs are removed from the hierarchy when their sidebar tab is
        // closed. Stop both direct AVAudioPlayer playback and any in-flight
        // ffmpeg preview at that lifecycle boundary.
        .onDisappear {
            audioPlayer.stop()
        }
    }

    // MARK: - Actions

    private func commitRename(speakerId: Int) {
        let previousName = speakerName(for: speakerId)
        let previousMeta = speakerMeta(for: speakerId)
        let suggestion = liveSuggestions["\(speakerId)"]
        let trimmed = editingName.trimmingCharacters(in: .whitespaces)
        // Empty, or unchanged from the current name → just close the editor
        // (no save/enroll). Unchanged matters because clicking off to deselect
        // routes through here and shouldn't re-enroll the same voice.
        guard !trimmed.isEmpty, trimmed != speakerName(for: speakerId) else {
            editingSpeakerId = nil
            nameFieldFocused = false
            return
        }

        // Two speakers can't share a name — that's almost always one person
        // split into two clusters. If the typed name already belongs to another
        // speaker, offer to merge them instead of creating a duplicate.
        if let otherId = uniqueSpeakerIds.first(where: {
            $0 != speakerId && speakerName(for: $0).caseInsensitiveCompare(trimmed) == .orderedSame
        }) {
            editingSpeakerId = nil
            nameFieldFocused = false
            pendingMerge = PendingMerge(from: speakerId, to: otherId, name: trimmed)
            return
        }

        transcript.speakerNames["\(speakerId)"] = trimmed
        editingSpeakerId = nil
        nameFieldFocused = false

        // Typing a name IS confirming it — mark verified/user so the meeting
        // counts as reviewed and stops nagging.
        setMeta(speakerId, source: "user", verified: true, confidence: nil)
        // An unverified auto-name is only a proposal. Correcting it must not
        // rename that person's established live voice profile.
        let renameFrom = previousMeta?.verified == true ? previousName : nil
        enrollConfirmed(trimmed, speakerId: speakerId, previousName: renameFrom)

        saveTranscript()
        if let suggestion, let proposed = suggestion.proposedName {
            let action = proposed.caseInsensitiveCompare(trimmed) == .orderedSame
                ? "confirmed" : "rejected"
            onRecordSpeakerSuggestion?(
                filePath, speakerId, action, proposed, trimmed
            )
            liveSuggestions.removeValue(forKey: "\(speakerId)")
        }
        refreshConfidence()
        refreshLibraryNames()
    }

    // MARK: - Speaker verification (provenance + confirm loop)

    /// True for an untouched "Speaker N" label.
    private func isGenericName(_ name: String) -> Bool {
        name.range(of: #"^Speaker \d+$"#, options: .regularExpression) != nil
    }

    private func speakerMeta(for id: Int) -> SpeakerMeta? {
        transcript.speakerMeta?["\(id)"]
    }

    /// Enrol a confirmed speaker into the voice library. Prefers the diarizer's
    /// stored centroid (robust, multi-segment) over one short audio segment.
    private func enrollConfirmed(_ name: String, speakerId: Int, previousName: String? = nil) {
        // A deliberate rename of an enrolled identity should keep the existing
        // voice samples under the new name. The backend rename is also the
        // merge primitive, so Adam → Adam Gardner removes the old key and
        // folds its exemplars into the surviving library entry when the target
        // already exists.
        if let previousName,
           !isGenericName(previousName),
           previousName.caseInsensitiveCompare(name) != .orderedSame {
            onRenameVoiceLibrary?(previousName, name)
        }

        if let fromDiarized = onEnrollSpeakerFromDiarized {
            fromDiarized(name, filePath, speakerId)
        } else if let segment = transcript.segments.first(where: { $0.speakerId == speakerId }) {
            onEnrollSpeaker(name, audioPath, segment.start, segment.end)
        }
    }

    private func setMeta(_ id: Int, source: String, verified: Bool, confidence: Double?) {
        var m = transcript.speakerMeta ?? [:]
        m["\(id)"] = SpeakerMeta(source: source, confidence: confidence, verified: verified)
        transcript.speakerMeta = m
    }

    /// The provenance chip shown next to each speaker in the verify panel.
    private func provenance(for id: Int) -> (text: String, color: Color) {
        let name = speakerName(for: id)
        let m = speakerMeta(for: id)
        let verified = m?.verified ?? false
        // Legacy sidecars have no meta — infer from the name.
        let source = m?.source ?? (isGenericName(name) ? "generic" : "auto")

        // Source word only — the numeric confidence lives in the separate
        // "match NN%" badge, so we don't show the same percentage twice.
        if verified {
            return source == "unknown" ? ("unknown", .secondary) : ("confirmed", .green)
        }
        switch source {
        case "auto":
            return ("auto", .blue)
        case "unknown":
            return ("unknown", .secondary)
        default:
            return isGenericName(name) ? ("unnamed", .orange) : ("auto", .blue)
        }
    }

    /// Any multi-speaker meeting with an unverified speaker still to review, or
    /// one where two speakers share a name (a duplicate to merge).
    private var needsVerification: Bool {
        guard uniqueSpeakerIds.count > 1 else { return false }
        if hasDuplicateNames { return true }
        return uniqueSpeakerIds.contains { !(speakerMeta(for: $0)?.verified ?? false) }
    }

    /// Calendar evidence is useful while speaker identity is unresolved. Once
    /// every active cluster has an explicit human outcome (including an
    /// intentional Unknown), reclustering could only risk moving confirmed
    /// turns between those identities, so automatic calendar matching stops.
    private var allSpeakersConfirmed: Bool {
        !uniqueSpeakerIds.isEmpty
            && uniqueSpeakerIds.allSatisfy { speakerMeta(for: $0)?.verified == true }
    }

    /// True when a real (non-generic) name is assigned to more than one speaker —
    /// e.g. the auto-matcher mapped one person's two clusters to the same voice.
    private var hasDuplicateNames: Bool {
        let named = uniqueSpeakerIds
            .map { speakerName(for: $0).lowercased() }
            .filter { !isGenericName($0) }
        return Set(named).count != named.count
    }

    /// The earliest OTHER speaker that shares this speaker's (non-generic) name,
    /// if any. Returned only for the later of the pair so a "merge" affordance
    /// shows once, and the merge folds the later speaker into the earlier one.
    private func duplicatePartner(for id: Int) -> Int? {
        let name = speakerName(for: id)
        guard !isGenericName(name) else { return nil }
        return uniqueSpeakerIds.first {
            $0 < id && speakerName(for: $0).caseInsensitiveCompare(name) == .orderedSame
        }
    }

    /// Confirm the current (auto/typed) name — lock it in and reinforce the
    /// voice library so future meetings match this voice better.
    private func confirmSpeaker(_ id: Int) {
        let name = speakerName(for: id)
        guard !isGenericName(name) else { return }   // nothing to confirm without a name
        let suggestion = liveSuggestions["\(id)"]
        let existingSource = speakerMeta(for: id)?.source
        setMeta(id, source: existingSource == "user" ? "user" : "auto",
                verified: true, confidence: speakerMeta(for: id)?.confidence)
        enrollConfirmed(name, speakerId: id)
        saveTranscript()
        if let suggestion, let proposed = suggestion.proposedName {
            let action = proposed.caseInsensitiveCompare(name) == .orderedSame
                ? "confirmed" : "rejected"
            onRecordSpeakerSuggestion?(filePath, id, action, proposed, name)
            liveSuggestions.removeValue(forKey: "\(id)")
        }
        refreshConfidence()
        refreshLibraryNames()
    }

    /// Apply a candidate name only after the reviewer presses the explicit
    /// confirmation button. Both the live TitaNet library and the isolated
    /// WeSpeaker library learn from that human-confirmed identity.
    private func confirmCandidateSuggestion(_ id: Int, suggestion: SpeakerSuggestion) {
        guard let proposed = suggestion.proposedName, !isGenericName(proposed) else { return }
        transcript.speakerNames["\(id)"] = proposed
        setMeta(id, source: "user", verified: true, confidence: suggestion.similarity)
        enrollConfirmed(proposed, speakerId: id)
        saveTranscript()
        onRecordSpeakerSuggestion?(filePath, id, "confirmed", proposed, proposed)
        liveSuggestions.removeValue(forKey: "\(id)")
        refreshConfidence()
        refreshLibraryNames()
    }

    /// Acknowledge a speaker the user genuinely can't name — counts as reviewed
    /// but is NOT enrolled into the voice library.
    private func markUnknown(_ id: Int) {
        let suggestion = liveSuggestions["\(id)"]
        setMeta(id, source: "unknown", verified: true, confidence: nil)
        saveTranscript()
        if let proposed = suggestion?.proposedName {
            onRecordSpeakerSuggestion?(filePath, id, "unknown", proposed, nil)
            liveSuggestions.removeValue(forKey: "\(id)")
        }
    }

    /// True when there's something to clear: a non-generic name and/or
    /// auto/unknown meta (not a pristine "Speaker N").
    private func canClearSpeaker(_ id: Int) -> Bool {
        if !isGenericName(speakerName(for: id)) { return true }
        if let m = speakerMeta(for: id), m.source != "generic" { return true }
        if liveConfidence["\(id)"] != nil { return true }
        return false
    }

    /// Unverified speakers that `canClearSpeaker` — used by Clear all (never
    /// bulk-undo locked-in / confirmed names).
    private var clearableUnverifiedSpeakerIds: [Int] {
        uniqueSpeakerIds.filter { id in
            canClearSpeaker(id) && !(speakerMeta(for: id)?.verified ?? false)
        }
    }

    /// Undo assignment: name → "Speaker N", meta → generic/unverified, drop
    /// confidence chip. Does not enroll and does not count as reviewed.
    private func clearSpeakerAssignment(_ id: Int, save: Bool = true) {
        let generic = "Speaker \(id + 1)"
        transcript.speakerNames["\(id)"] = generic
        setMeta(id, source: "generic", verified: false, confidence: nil)
        liveConfidence.removeValue(forKey: "\(id)")
        if speakerFilter == id { speakerFilter = nil }
        if editingSpeakerId == id {
            editingSpeakerId = nil
            nameFieldFocused = false
        }
        if save { saveTranscript() }
    }

    /// Clear every unconfirmed auto/typed assignment in one pass. Confirmed
    /// speakers are left alone.
    private func clearAllUnverifiedSpeakers() {
        let ids = clearableUnverifiedSpeakerIds
        guard !ids.isEmpty else { return }
        for id in ids {
            clearSpeakerAssignment(id, save: false)
        }
        saveTranscript()
    }

    /// True when this multi-speaker meeting still has anyone not fully
    /// reviewed — so "Mark all unknown" can close the tagging loop.
    private var canMarkAllUnknown: Bool {
        guard uniqueSpeakerIds.count > 1 else { return false }
        return uniqueSpeakerIds.contains { id in
            !(speakerMeta(for: id)?.verified ?? false)
        }
    }

    /// Mark every unverified speaker as a reviewed guest: name → Speaker N,
    /// meta → unknown + verified. Confirmed real names are untouched.
    /// Result: ≥1 verified speaker ⇒ speakersTagged / orange nag clears.
    ///
    /// Iterates **all** `speakerNames` keys (not only segment speaker ids) so
    /// orphan name-table rows don't leave the meeting in "needs tagging".
    private func markAllSpeakersUnknown() {
        var changed = false
        let ids: [Int] = {
            var set = Set(uniqueSpeakerIds)
            for key in transcript.speakerNames.keys {
                if let n = Int(key) { set.insert(n) }
            }
            return set.sorted()
        }()
        for id in ids {
            if speakerMeta(for: id)?.verified == true,
               !isGenericName(speakerName(for: id)) {
                continue   // keep locked-in people
            }
            let generic = "Speaker \(id + 1)"
            if speakerName(for: id) != generic
                || speakerMeta(for: id)?.source != "unknown"
                || speakerMeta(for: id)?.verified != true {
                transcript.speakerNames["\(id)"] = generic
                setMeta(id, source: "unknown", verified: true, confidence: nil)
                liveConfidence.removeValue(forKey: "\(id)")
                changed = true
            }
        }
        if speakerFilter != nil { speakerFilter = nil }
        if editingSpeakerId != nil {
            editingSpeakerId = nil
            nameFieldFocused = false
        }
        if changed { saveTranscript() }
    }

    /// Merge the just-renamed speaker into the existing speaker that already has
    /// that name (they're the same person split across two clusters).
    private func confirmMerge(_ merge: PendingMerge) {
        let previousName = speakerName(for: merge.from)
        mapSpeaker(from: merge.from, to: merge.to)   // reassigns segments + saves
        // The surviving speaker now carries a confirmed, user-set identity.
        setMeta(merge.to, source: "user", verified: true, confidence: nil)
        enrollConfirmed(merge.name, speakerId: merge.to, previousName: previousName)
        saveTranscript()
        refreshConfidence()
        refreshLibraryNames()
    }

    /// Ask the background CLI to score each speaker's voice against its assigned
    /// name in the voice library, and cache the result for the verify chips.
    private func refreshConfidence() {
        onScoreSpeakers?(filePath) { scores in
            self.liveConfidence = scores
        }
    }

    /// Ask the isolated candidate model for evidence. Missing configuration,
    /// a recording in progress, or any inference failure simply yields no
    /// proposals and leaves the established review screen unchanged.
    private func refreshSuggestions() {
        guard !suggestionsLoading, let onSuggestSpeakers else { return }
        suggestionsLoading = true
        onSuggestSpeakers(filePath) { suggestions in
            self.liveSuggestions = suggestions
            self.suggestionsLoading = false
        }
    }

    /// Load the enrolled voice names for the rename autocomplete.
    private func refreshLibraryNames() {
        onListVoiceNames?() { names in self.libraryNames = names }
    }

    /// Confirmed/suggested calendar people who have an enrolled voice. These
    /// are promoted in the selected-text person picker without excluding the
    /// rest of the library, so calendar evidence guides rather than dictates.
    private var calendarInvitedLibraryNames: [String] {
        let invited = (linkedCalendarEvent ?? suggestedCalendarEvent)?.attendeeNames ?? []
        guard !invited.isEmpty else { return [] }
        return libraryNames.filter { libraryName in
            invited.contains { invitedName in
                libraryName.caseInsensitiveCompare(invitedName) == .orderedSame
            }
        }
    }

    /// Distinct real names already assigned to speakers in this meeting, in
    /// speaker order. These are the most useful mapping targets when correcting
    /// an over-split meeting.
    private var meetingMappedNames: [String] {
        var seen = Set<String>()
        return uniqueSpeakerIds.compactMap { id in
            let name = speakerName(for: id).trimmingCharacters(in: .whitespaces)
            let key = name.lowercased()
            guard !name.isEmpty, !isGenericName(name), !seen.contains(key) else { return nil }
            seen.insert(key)
            return name
        }
    }

    /// Voice-library names not already shown in the meeting-mapped section.
    /// Keep the library order stable, while removing case-insensitive duplicates.
    private var otherVoiceLibraryNames: [String] {
        var seen = Set(meetingMappedNames.map { $0.lowercased() })
        return libraryNames.compactMap { rawName in
            let name = rawName.trimmingCharacters(in: .whitespaces)
            let key = name.lowercased()
            guard !name.isEmpty, !seen.contains(key) else { return nil }
            seen.insert(key)
            return name
        }
    }

    /// Autocomplete suggestions for the speaker currently being renamed.
    /// Meeting-mapped names are shown first; typing filters both sections and
    /// still reaches every other enrolled Voice Library name.
    @ViewBuilder
    private func nameSuggestions(for id: Int) -> some View {
        let typed = editingName.trimmingCharacters(in: .whitespaces)
        let q = typed.lowercased()
        let current = speakerName(for: id).lowercased()
        // A mapped speaker opens with its current name in the field. Treat that
        // as browse mode so the user immediately sees the other people in this
        // meeting, while any newly typed text becomes a normal search query.
        let browse = typed.isEmpty || isGenericName(typed) || typed.lowercased() == current
        let meetingMatches = meetingMappedNames
            .filter { browse || $0.lowercased().contains(q) }
            .filter { $0.lowercased() != current }
        let remainingSlots = max(0, 8 - meetingMatches.count)
        let libraryMatches = otherVoiceLibraryNames
            .filter { browse || $0.lowercased().contains(q) }
            .prefix(remainingSlots)

        if !meetingMatches.isEmpty || !libraryMatches.isEmpty {
            VStack(alignment: .leading, spacing: 0) {
                if !meetingMatches.isEmpty {
                    Text("Already mapped in this meeting")
                        .font(.caption2)
                        .foregroundColor(.secondary)
                        .padding(.horizontal, 8)
                        .padding(.top, 4)
                    suggestionRows(meetingMatches, speakerId: id)
                }

                if !libraryMatches.isEmpty {
                    Text(meetingMatches.isEmpty ? "Voice Library" : "Other voices")
                        .font(.caption2)
                        .foregroundColor(.secondary)
                        .padding(.horizontal, 8)
                        .padding(.top, meetingMatches.isEmpty ? 4 : 8)
                    suggestionRows(Array(libraryMatches), speakerId: id)
                }
            }
            .padding(.vertical, 2)
            .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 6))
            .overlay(RoundedRectangle(cornerRadius: 6).stroke(.quaternary))
            .frame(maxWidth: 280, alignment: .leading)
            .zIndex(1)
        } else if libraryNames.isEmpty {
            Text("No voices in library yet")
                .font(.caption2)
                .foregroundColor(.secondary)
                .padding(.horizontal, 8)
                .padding(.vertical, 4)
        }
    }

    @ViewBuilder
    private func suggestionRows(_ names: [String], speakerId: Int) -> some View {
        ForEach(names, id: \.self) { name in
            Button {
                editingName = name
                commitRename(speakerId: speakerId)   // map to this exact voice
            } label: {
                HStack(spacing: 6) {
                    Image(systemName: "person.crop.circle.fill.badge.checkmark")
                        .foregroundColor(.accentColor)
                    Text(name).font(.caption)
                    Spacer()
                }
                .padding(.horizontal, 8)
                .padding(.vertical, 4)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
        }
    }

    /// A voice-match badge based on the MARGIN over other enrolled voices, not a
    /// raw cosine (which looks high even when the match is wrong). Flags the case
    /// the user hit — another enrolled voice fits better than the assigned name.
    private func confidenceBadge(for id: Int) -> (text: String, color: Color)? {
        // One name per speaker: when WeSpeaker proposes something different
        // from the quick library guess, its suggestion row carries the naming
        // decision and this badge stays out of the way (the two models
        // disagreeing on screen was read as one confused signal).
        if let suggestion = liveSuggestions["\(id)"],
           let proposed = suggestion.proposedName,
           let best = liveConfidence["\(id)"]?.best,
           best.caseInsensitiveCompare(proposed) != .orderedSame {
            return nil
        }
        guard let s = liveConfidence["\(id)"] else { return nil }
        // Assigned name isn't enrolled yet — hint at the closest known voice.
        guard let assignedScore = s.score else {
            if let best = s.best { return ("sounds like \(best)", .secondary) }
            return nil
        }
        // Another enrolled voice matches better than the assigned name → suspect.
        if let best = s.best, best != s.assigned,
           let bestScore = s.bestScore, bestScore > assignedScore + 0.02 {
            return ("looks more like \(best)", .red)
        }
        // Assigned IS the closest — how clearly does it beat the runner-up?
        guard let margin = s.margin else {
            return ("only voice enrolled", .secondary)   // nothing to compare against
        }
        if margin >= 0.08 { return ("clear match", .green) }
        if let ru = s.runnerUp {
            return margin >= 0.03 ? ("close vs \(ru)", .orange) : ("ambiguous vs \(ru)", .red)
        }
        return ("weak match", .orange)
    }

    private func copyAllToClipboard() {
        var lines: [String] = []
        for seg in transcript.segments {
            let ts = "[\(formatTime(seconds: seg.start))]"
            if hasSpeakers {
                // Until a speaker is user-confirmed, export as "Speaker N" so
                // shaky auto-matches don't land in the clipboard / saved text.
                lines.append("\(ts) \(copySpeakerLabel(for: seg.speakerId)): \(seg.text)")
            } else {
                lines.append("\(ts) \(seg.text)")
            }
        }
        let text = lines.joined(separator: "\n\n")
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(text, forType: .string)
    }

    /// Name used when copying the transcript. Confirmed speakers keep their
    /// real name; everything else (auto-match, generic, unverified) becomes
    /// "Speaker 1", "Speaker 2", … so the paste is stable until tagging is done.
    private func copySpeakerLabel(for id: Int) -> String {
        if speakerMeta(for: id)?.verified == true {
            return speakerName(for: id)
        }
        return "Speaker \(id + 1)"
    }

    private func undoMerge() {
        guard let previous = transcriptHistory.popLast() else { return }
        transcript = previous
        saveTranscript()
    }

    private func mapSpeaker(from sourceId: Int, to targetId: Int) {
        // Save current state for undo
        transcriptHistory.append(transcript)

        // Reassign all segments from sourceId to targetId
        for i in transcript.segments.indices {
            if transcript.segments[i].speakerId == sourceId {
                transcript.segments[i].speakerId = targetId
                transcript.segments[i].text = transcript.segments[i].text // trigger update
            }
        }
        // Remove the old speaker name
        transcript.speakerNames.removeValue(forKey: "\(sourceId)")
        pruneInactiveSpeakerState()
        syncRediarizeSpeakerCount()

        // NOTE: deliberately DON'T concatenate consecutive same-speaker segments
        // here. The old re-merge glued every adjacent turn into one unbounded
        // block (the diarizer caps block length; this didn't), which turned a
        // clean transcript into a wall of text and broke word-range selection.
        // Leaving the segments as-is keeps the readable per-turn blocks.

        saveTranscript()
    }

    @ViewBuilder
    private var transcriptHistoryPicker: some View {
        VStack(alignment: .leading, spacing: 9) {
            Text("Transcript history")
                .font(.headline)
            Text("Snapshots are local to this Mac. Audio is never included.")
                .font(.caption)
                .foregroundColor(.secondary)
            if transcriptVersions.isEmpty {
                Text("No earlier snapshot yet. HiDock saves one before each speaker-changing action from now on.")
                    .font(.caption)
                    .foregroundColor(.secondary)
            } else {
                ForEach(transcriptVersions) { version in
                    HStack(spacing: 8) {
                        Text(version.title)
                            .font(.caption)
                            .lineLimit(2)
                        Spacer()
                        Button("Restore") {
                            pendingTranscriptRestore = version
                        }
                        .buttonStyle(.bordered)
                        .controlSize(.small)
                    }
                }
            }
        }
        .confirmationDialog(
            "Restore this transcript version?",
            isPresented: Binding(
                get: { pendingTranscriptRestore != nil },
                set: { if !$0 { pendingTranscriptRestore = nil } }
            ),
            presenting: pendingTranscriptRestore
        ) { version in
            Button("Restore", role: .destructive) {
                onRestoreTranscriptVersion?(filePath, version.id)
                pendingTranscriptRestore = nil
                showTranscriptHistory = false
            }
            Button("Cancel", role: .cancel) { pendingTranscriptRestore = nil }
        } message: { version in
            Text("Restore \(version.title)? The current state is first saved as a new rollback point.")
        }
    }

    private func saveTranscript() {
        do {
            onSnapshotTranscript?(filePath, "Before speaker edit")
            let encoder = JSONEncoder()
            encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
            let data = try encoder.encode(transcript)
            try data.write(to: URL(fileURLWithPath: filePath), options: .atomic)
            // Persist first, then notify the owner. The owner re-reads the
            // sidecar and updates the table's cached icon immediately.
            onSpeakerReviewChanged?(filePath)
            // Keep the sibling .md in sync: confirmed names only on disk.
            onRewriteMarkdown?(filePath)
        } catch {
            print("Failed to save transcript: \(error)")
        }
    }
}
