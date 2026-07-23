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

    enum CodingKeys: String, CodingKey {
        case version
        case audioFile = "audio_file"
        case segments
        case speakerNames = "speaker_names"
        case speakerMeta = "speaker_meta"
        case speakerEmbeddings = "speaker_embeddings"
    }
}

struct DiarizedSegment: Codable, Identifiable {
    var id: String { "\(speakerId)-\(start)" }
    let start: Double
    let end: Double
    var speakerId: Int
    var text: String
    var words: [DiarizedWord]?

    enum CodingKeys: String, CodingKey {
        case start, end
        case speakerId = "speaker_id"
        case text, words
    }

    init(start: Double, end: Double, speakerId: Int = 0, text: String,
         words: [DiarizedWord]? = nil) {
        self.start = start
        self.end = end
        self.speakerId = speakerId
        self.text = text
        self.words = words
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        start = try c.decode(Double.self, forKey: .start)
        end = try c.decode(Double.self, forKey: .end)
        speakerId = (try? c.decode(Int.self, forKey: .speakerId)) ?? 0
        text = try c.decode(String.self, forKey: .text)
        words = try c.decodeIfPresent([DiarizedWord].self, forKey: .words)
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
struct SegmentSelection: Equatable {
    let anchor: WordPosition
    var focus: WordPosition

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

enum TranscriptRediarizeStatus {
    case running
    case completed(TranscriptRediarizeSummary, DiarizedTranscript)
    case failed(String)
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
    @State private var transcriptWordFrames: [WordPosition: CGRect] = [:]
    @State private var selectionDragStart: WordPosition? = nil
    @StateObject var audioPlayer = SegmentAudioPlayer()
    let filePath: String
    let audioPath: String
    let onEnrollSpeaker: (String, String, Double, Double) -> Void
    var onRediarize: ((String, Int?, @escaping (TranscriptRediarizeStatus) -> Void) -> Void)?
    /// Layer 2 callback — fires `transcribe.py recluster-with-anchors`
    /// against the current diarized.json, treating every segment with
    /// a user-edited speaker name as an anchor centroid. Optional so
    /// older call-sites (rediarize-only flow) keep compiling.
    var onReclusterWithLabels: ((String) -> Void)?
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
            if needsVerification {
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
        Text("Speakers")
            .font(.caption.weight(.semibold))
            .foregroundColor(.secondary)
            .padding(.horizontal, 16)

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

                    Stepper("\(rediarizeNSpeakers)", value: $rediarizeNSpeakers, in: rediarizeSpeakerRange)
                        .font(.caption)
                        .frame(width: 70)
                        .help("Expected speaker count for Redetect. The current transcript has \(uniqueSpeakerIds.count) detected speaker\(uniqueSpeakerIds.count == 1 ? "" : "s").")
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
                        onReclusterWithLabels?(filePath)
                    } label: {
                        Label("Reassign", systemImage: "person.crop.circle.badge.checkmark")
                    }
                    .fixedSize()
                    .help("Reassign remaining unconfirmed turns to the closest confirmed or legacy-named person in this meeting. Named anchors stay fixed; conservative similarity thresholds and a 30-second block cap protect against bad matches.")
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

    private var speakerVerifyPanel: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 6) {
                Image(systemName: "checkmark.seal")
                    .foregroundColor(.blue)
                Text("Verify speakers")
                    .font(.caption.weight(.semibold))
                Text("Confirm each voice to lock it in — this also teaches your voice library.")
                    .font(.caption2)
                    .foregroundColor(.secondary)
                Spacer()
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

    private var speakerVerifyPanelMaximumHeight: CGFloat { 260 }

    /// Size small meetings to their content, but cap larger meetings so the
    /// transcript always opens with useful space. Extra speaker rows remain
    /// available in the review panel's own ScrollView.
    private var speakerVerifyPanelIdealHeight: CGFloat {
        let count = max(1, uniqueSpeakerIds.count)
        let rowHeight: CGFloat = 68
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
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 4) {
                    ForEach(Array(transcript.segments.enumerated()), id: \.element.id) { idx, segment in
                        if speakerFilter == nil || segment.speakerId == speakerFilter {
                            segmentRow(segmentIndex: idx, segment: segment)
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

        VStack(alignment: .leading, spacing: 2) {
        HStack(spacing: 8) {
            speakerPill(speakerId: id, interactive: true, context: "verify")   // tap to rename/correct

            Text(prov.text)
                .font(.caption2.weight(.medium))
                .padding(.horizontal, 6)
                .padding(.vertical, 2)
                .background(prov.color.opacity(0.15), in: Capsule())
                .foregroundColor(prov.color)

            // Live voice-match confidence against the enrolled voice of this name.
            if let badge = confidenceBadge(for: id) {
                Text(badge.text)
                    .font(.caption2.weight(.medium))
                    .padding(.horizontal, 6)
                    .padding(.vertical, 2)
                    .background(badge.color.opacity(0.15), in: Capsule())
                    .foregroundColor(badge.color)
                    .help("How closely this speaker's voice matches the enrolled voice for \(speakerName(for: id)).")
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
            } else if verified {
                Label("Verified", systemImage: "checkmark.seal.fill")
                    .font(.caption2)
                    .foregroundColor(.green)
            } else {
                if !isGenericName(name) {
                    Button {
                        confirmSpeaker(id)
                    } label: {
                        Label("Confirm", systemImage: "checkmark")
                    }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.small)
                    .help("Accept this name, lock it in, and reinforce it in your voice library.")
                }
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
        if !verified, let suggestion = liveSuggestions["\(id)"] {
            speakerSuggestionRow(id: id, suggestion: suggestion)
        }
        }
    }

    @ViewBuilder
    private func speakerSuggestionRow(id: Int, suggestion: SpeakerSuggestion) -> some View {
        if let proposed = suggestion.proposedName {
            HStack(spacing: 7) {
                Image(systemName: suggestion.decision == "strong_review"
                      ? "person.crop.circle.badge.questionmark.fill"
                      : "person.crop.circle.badge.questionmark")
                    .foregroundColor(suggestion.decision == "strong_review" ? .green : .blue)
                Text(suggestion.decision == "strong_review"
                     ? "WeSpeaker suggests" : "Closest voice to review")
                    .font(.caption2)
                    .foregroundColor(.secondary)
                Text(proposed)
                    .font(.caption.weight(.semibold))
                if let score = suggestion.similarity {
                    Text("\(Int((score * 100).rounded()))%")
                        .font(.caption2.monospacedDigit())
                        .foregroundColor(.secondary)
                }
                if let margin = suggestion.margin {
                    Text("+\(Int((margin * 100).rounded())) margin")
                        .font(.caption2.monospacedDigit())
                        .foregroundColor(.secondary)
                }
                if let meetings = suggestion.supportingMeetings {
                    Text("\(meetings) meeting\(meetings == 1 ? "" : "s")")
                        .font(.caption2)
                        .foregroundColor(.secondary)
                }
                Spacer()
                Button(suggestion.decision == "strong_review" ? "Confirm suggestion" : "Confirm this name") {
                    confirmCandidateSuggestion(id, suggestion: suggestion)
                }
                .buttonStyle(.bordered)
                .controlSize(.small)
                .help("You are confirming this identity. The model cannot apply it by itself.")
            }
            .padding(.leading, 26)
            .help(suggestionHelp(suggestion))
        } else if suggestion.decision == "hold" {
            HStack(spacing: 6) {
                Image(systemName: "waveform.badge.exclamationmark")
                    .foregroundColor(.secondary)
                Text("WeSpeaker held this speaker for manual review")
                    .font(.caption2)
                    .foregroundColor(.secondary)
            }
            .padding(.leading, 26)
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

    private func saveTranscript() {
        do {
            let encoder = JSONEncoder()
            encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
            let data = try encoder.encode(transcript)
            try data.write(to: URL(fileURLWithPath: filePath), options: .atomic)
            // Persist first, then notify the owner. The owner re-reads the
            // sidecar and updates the table's cached icon immediately.
            onSpeakerReviewChanged?(filePath)
        } catch {
            print("Failed to save transcript: \(error)")
        }
    }
}
