import AppKit
import SwiftUI
import AVFoundation

// MARK: - Audio Player

class SegmentAudioPlayer: ObservableObject {
    @Published var playingSegmentId: String?
    /// Index of the word currently being spoken in the playing segment (for the
    /// karaoke highlight). Approximated from elapsed/duration since the diarized
    /// segments carry no per-word timing. Only re-published when it changes, so
    /// it doesn't re-render the transcript on every timer tick.
    @Published var playingWordIndex: Int = 0
    private var player: AVAudioPlayer?
    private var stopTimer: Timer?
    private var progressTimer: Timer?
    private var playBaseline: Double = 0   // player.currentTime at the segment's start
    private var playDuration: Double = 1
    private var playWordCount: Int = 0
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

    func play(audioPath: String, start: Double, end: Double, segmentId: String, wordCount: Int = 0) {
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
            // Direct path plays from `start`, so the segment's t=0 is at currentTime `start`.
            startProgressTracking(baseline: max(0, start), duration: end - start, wordCount: wordCount)
            armStopTimer(after: end - start)
            return
        }

        // Fallback: Core Audio couldn't open it (e.g. Opus). Use ffmpeg to
        // decode just this segment to a temp WAV, then play that from 0.
        decodeAndPlayViaFFmpeg(audioPath: audioPath, start: start, end: end, segmentId: segmentId, wordCount: wordCount)
    }

    /// Drive the karaoke word cursor from playback position. Publishes
    /// `playingWordIndex` only when the word changes (a few Hz at most).
    private func startProgressTracking(baseline: Double, duration: Double, wordCount: Int) {
        progressTimer?.invalidate()
        playBaseline = baseline
        playDuration = max(0.001, duration)
        playWordCount = wordCount
        playingWordIndex = 0
        guard wordCount > 0 else { return }
        let t = Timer(timeInterval: 0.05, repeats: true) { [weak self] _ in
            guard let self = self, let p = self.player else { return }
            let frac = min(max((p.currentTime - self.playBaseline) / self.playDuration, 0), 1)
            let idx = min(self.playWordCount - 1, Int(frac * Double(self.playWordCount)))
            if idx != self.playingWordIndex { self.playingWordIndex = idx }
        }
        RunLoop.main.add(t, forMode: .common)
        progressTimer = t
    }

    private func decodeAndPlayViaFFmpeg(audioPath: String, start: Double, end: Double, segmentId: String, wordCount: Int = 0) {
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
                self.startProgressTracking(baseline: 0, duration: duration, wordCount: wordCount)
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
        if let proc = decodeProcess, proc.isRunning { proc.terminate() }
        decodeProcess = nil
        if let t = tempURL { try? FileManager.default.removeItem(at: t); tempURL = nil }
        playingSegmentId = nil
        playingWordIndex = 0
    }
}

// MARK: - Data Models

/// Per-speaker provenance + review state, mirrored from the Python sidecar
/// (`speaker_meta`). See PLAN-speaker-tagging-loop.md.
struct SpeakerMeta: Codable {
    /// "auto" (voice-library match) | "user" (typed/confirmed) | "unknown"
    /// (acknowledged guest) | "generic" (untouched "Speaker N").
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

    enum CodingKeys: String, CodingKey {
        case start, end
        case speakerId = "speaker_id"
        case text
    }

    init(start: Double, end: Double, speakerId: Int = 0, text: String) {
        self.start = start
        self.end = end
        self.speakerId = speakerId
        self.text = text
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        start = try c.decode(Double.self, forKey: .start)
        end = try c.decode(Double.self, forKey: .end)
        speakerId = (try? c.decode(Int.self, forKey: .speakerId)) ?? 0
        text = try c.decode(String.self, forKey: .text)
    }
}

// MARK: - Speaker Colors

private let speakerColors: [Color] = [
    .blue, .green, .orange, .purple, .pink, .teal, .indigo, .mint
]

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

/// Identifies which segment currently has an active word-range
/// selection and what that range is. Only one segment can have a
/// selection at a time — starting a drag in another segment moves the
/// selection there.
struct SegmentSelection: Equatable {
    let segmentIndex: Int
    var range: ClosedRange<Int>
}

/// Lets word-token views inside a row publish their frames (in the
/// row's local coordinate space) so a single drag gesture on the
/// container can hit-test which word the pointer is over.
private struct WordFramesKey: PreferenceKey {
    static var defaultValue: [Int: CGRect] = [:]
    static func reduce(value: inout [Int: CGRect], nextValue: () -> [Int: CGRect]) {
        value.merge(nextValue(), uniquingKeysWith: { $1 })
    }
}

/// Renders a segment's text as a flow of clickable word tokens with a
/// drag-to-select range. A single tap selects one word; dragging
/// extends the range. The selected range tints blue. Pure UI — the
/// caller owns the selection state via `activeRange` / `onRangeChange`.
private struct WordTokensView: View {
    let words: [String]
    let activeRange: ClosedRange<Int>?
    /// Word currently being spoken (karaoke highlight), or nil when not playing.
    var playingWord: Int? = nil
    let onRangeChange: (ClosedRange<Int>) -> Void

    @State private var wordFrames: [Int: CGRect] = [:]
    @State private var dragStart: Int? = nil

    var body: some View {
        // Reads as a normal paragraph: each word carries its own
        // trailing space so the natural inter-word gap is the font's
        // own space-glyph width, not a padding constant. FlowLayout
        // spacing is 0 so adjacent highlighted words have backgrounds
        // that touch edge-to-edge (matching native text-selection).
        FlowLayout(spacing: 0, lineSpacing: 1) {
            ForEach(Array(words.enumerated()), id: \.offset) { i, w in
                let inRange = activeRange.map { $0.contains(i) } ?? false
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
                                key: WordFramesKey.self,
                                value: [i: proxy.frame(in: .named("wordFlow"))]
                            )
                        }
                    )
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .coordinateSpace(name: "wordFlow")
        .contentShape(Rectangle())
        .onPreferenceChange(WordFramesKey.self) { wordFrames = $0 }
        .gesture(
            DragGesture(minimumDistance: 0, coordinateSpace: .named("wordFlow"))
                .onChanged { value in
                    guard let idx = wordIndex(at: value.location) else { return }
                    if dragStart == nil { dragStart = idx }
                    let lower = min(dragStart!, idx)
                    let upper = max(dragStart!, idx)
                    onRangeChange(lower...upper)
                }
                .onEnded { _ in
                    dragStart = nil
                }
        )
    }

    private func wordIndex(at point: CGPoint) -> Int? {
        wordFrames.first(where: { $0.value.contains(point) })?.key
    }
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
    @State var transcriptHistory: [DiarizedTranscript] = []
    /// Layer 1 v2 — currently active mid-segment word-range selection.
    /// Drives the inline speaker bar that appears below the affected
    /// segment row. Only one segment can have a selection at a time.
    @State var selection: SegmentSelection? = nil
    @StateObject var audioPlayer = SegmentAudioPlayer()
    let filePath: String
    let audioPath: String
    let onEnrollSpeaker: (String, String, Double, Double) -> Void
    var onRediarize: ((String, Int?) -> Void)?
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
    /// Fetch the enrolled voice-library names (for the map-to-existing-speaker
    /// autocomplete). Optional so older call-sites keep compiling.
    var onListVoiceNames: ((@escaping ([String]) -> Void) -> Void)?
    /// After the diarized JSON is saved, regenerate the sibling .md so
    /// confirmed-only names hit disk (unconfirmed stay Speaker N).
    var onRewriteMarkdown: ((String) -> Void)?

    private var uniqueSpeakerIds: [Int] {
        Array(Set(transcript.segments.map(\.speakerId))).sorted()
    }

    private var hasSpeakers: Bool {
        // Non-diarized transcripts have all speaker_id=0 and empty speaker_names
        uniqueSpeakerIds.count > 1 || !transcript.speakerNames.isEmpty
    }

    private func speakerName(for id: Int) -> String {
        transcript.speakerNames["\(id)"] ?? "Speaker \(id + 1)"
    }

    /// True when at least one speaker has been renamed away from the
    /// auto-generated "Speaker N" — the signal we use to know we have
    /// anchor labels worth re-clustering against (Layer 2).
    private var hasUserNamedSpeakers: Bool {
        for (idStr, name) in transcript.speakerNames {
            guard let id = Int(idStr) else { continue }
            let trimmed = name.trimmingCharacters(in: .whitespaces)
            if trimmed.isEmpty { continue }
            if trimmed == "Speaker \(id + 1)" { continue }
            return true
        }
        return false
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

            // Verify speakers — auto-matched voices to confirm/correct so the
            // meeting counts as reviewed and the voice library keeps improving.
            if needsVerification {
                speakerVerifyPanel
                Divider()
            }

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
        .onAppear { refreshConfidence(); refreshLibraryNames() }
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

    /// Inline speaker bar that appears directly below a segment row when
    /// the user has selected a word range inside it. Click a speaker
    /// pill to assign the range to that speaker; the segment splits
    /// into up to three pieces depending on whether the range hits the
    /// start, middle, or end of the segment.
    @ViewBuilder
    private func inlineSpeakerBar(segmentIndex idx: Int, range: ClosedRange<Int>) -> some View {
        let count = range.upperBound - range.lowerBound + 1
        HStack(spacing: 8) {
            Image(systemName: "scissors")
                .foregroundColor(.blue)
                .font(.caption)
            Text("Assign \(count) word\(count == 1 ? "" : "s") to:")
                .font(.caption.weight(.medium))
                .foregroundColor(.secondary)

            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 6) {
                    ForEach(uniqueSpeakerIds, id: \.self) { sid in
                        Button {
                            applyRangeSplit(segmentIndex: idx, wordRange: range, newSpeakerId: sid)
                            selection = nil
                        } label: {
                            speakerPill(speakerId: sid, interactive: false)
                        }
                        .buttonStyle(.plain)
                    }
                    Button {
                        applyRangeSplit(segmentIndex: idx, wordRange: range, newSpeakerId: nextNewSpeakerId())
                        selection = nil
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
                selection = nil
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

    /// Layer 1 v2 split. Takes a closed word range inside `segments[idx]`
    /// and assigns those words to `newSpeakerId`. Up to three pieces:
    /// optional head (original speaker, words before the range),
    /// the range itself (new speaker), optional tail (original speaker,
    /// words after the range). Time boundaries via linear interpolation
    /// over word count — same approach as the original sheet-based
    /// Layer 1, kept because it's cheap and TitaNet's effective
    /// resolution swallows the per-word imprecision.
    private func applyRangeSplit(segmentIndex idx: Int, wordRange: ClosedRange<Int>, newSpeakerId: Int) {
        guard idx >= 0, idx < transcript.segments.count else { return }
        let segment = transcript.segments[idx]
        let words = segment.text.split(separator: " ", omittingEmptySubsequences: false).map(String.init)
        guard !words.isEmpty else { return }
        // Clamp to valid indices rather than bailing — a selection that runs to
        // the very end of a block could produce an out-of-range upper bound,
        // which silently did nothing when the user clicked a speaker.
        let startWord = max(0, min(wordRange.lowerBound, words.count - 1))
        let endWord = max(startWord, min(wordRange.upperBound, words.count - 1))

        transcriptHistory.append(transcript)

        let duration = max(segment.end - segment.start, 0.001)
        let totalWords = Double(words.count)
        let rangeStartTime = segment.start + (Double(startWord) / totalWords) * duration
        let rangeEndTime = segment.start + (Double(endWord + 1) / totalWords) * duration

        var replacement: [DiarizedSegment] = []
        if startWord > 0 {
            let headText = words[0..<startWord].joined(separator: " ")
            replacement.append(DiarizedSegment(
                start: segment.start,
                end: rangeStartTime,
                speakerId: segment.speakerId,
                text: headText
            ))
        }
        let rangeText = words[startWord...endWord].joined(separator: " ")
        replacement.append(DiarizedSegment(
            start: rangeStartTime,
            end: rangeEndTime,
            speakerId: newSpeakerId,
            text: rangeText
        ))
        if endWord < words.count - 1 {
            let tailText = words[(endWord + 1)..<words.count].joined(separator: " ")
            replacement.append(DiarizedSegment(
                start: rangeEndTime,
                end: segment.end,
                speakerId: segment.speakerId,
                text: tailText
            ))
        }

        var updated = transcript.segments
        updated.remove(at: idx)
        for (offset, seg) in replacement.enumerated() {
            updated.insert(seg, at: idx + offset)
        }
        transcript.segments = updated

        // Enrol the range as a sample for the new speaker — cleaner
        // provenance than the whole-segment sample we used to take
        // from the second half of a single-cut split.
        let speakerNameForEnrol = speakerName(for: newSpeakerId)
        onEnrollSpeaker(speakerNameForEnrol, audioPath, rangeStartTime, rangeEndTime)

        saveTranscript()
    }

    // MARK: - Speaker tools

    /// Secondary strip grouping the speaker-fixing actions, each shown only when
    /// it applies, with plain-language tooltips.
    private var speakerToolsBar: some View {
      ScrollView(.horizontal, showsIndicators: false) {
        HStack(spacing: 8) {
            Text("Speakers")
                .font(.caption.weight(.semibold))
                .foregroundColor(.secondary)

            if onRematch != nil {
                Button {
                    onRematch?(filePath)
                } label: {
                    Label("Rematch Speakers", systemImage: "sparkle.magnifyingglass")
                }
                .fixedSize()
                .help("Fill in any unnamed speakers by matching their voice against your saved Voice Library. Won't touch ones you've already confirmed.")
            }

            if onReclusterWithLabels != nil, hasUserNamedSpeakers {
                Button {
                    onReclusterWithLabels?(filePath)
                } label: {
                    Label("Reassign Speakers", systemImage: "person.crop.circle.badge.checkmark")
                }
                .fixedSize()
                .help("Use the speakers you've named as anchors and re-assign the still-unnamed bits to whichever of them they sound closest to. This meeting only — nothing you've corrected moves.")
            }

            if onRediarize != nil {
                Divider().frame(height: 14)
                Stepper("Count: \(rediarizeNSpeakers)", value: $rediarizeNSpeakers, in: 2...8)
                    .font(.caption)
                    .frame(width: 120)
                    .help("How many speakers to detect when re-detecting.")
                Button {
                    onRediarize?(filePath, rediarizeNSpeakers)
                } label: {
                    Label("Detect Speakers", systemImage: "person.2.wave.2")
                }
                .fixedSize()
                .help("Start over — detect the speakers again from scratch (using the count on the left). Discards the current split and any names.")
            }
        }
        .buttonStyle(.bordered)
        .controlSize(.small)
        .padding(.horizontal, 16)
        .padding(.vertical, 6)
      }
      .frame(maxWidth: .infinity, alignment: .leading)
      .background(Color.secondary.opacity(0.04))
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
            }
            // Bound the height and scroll — with many speakers this used to grow
            // unbounded and push the transcript off-screen.
            ScrollView {
                VStack(alignment: .leading, spacing: 4) {
                    ForEach(uniqueSpeakerIds, id: \.self) { id in
                        speakerVerifyRow(id: id)
                    }
                }
            }
            .frame(maxHeight: uniqueSpeakerIds.count > 4 ? 170 : .infinity)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 8)
        .background(Color.blue.opacity(0.04))
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
            } else if verified {
                Label("Verified", systemImage: "checkmark.seal.fill")
                    .font(.caption2)
                    .foregroundColor(.green)
            } else if !isGenericName(name) {
                Button {
                    confirmSpeaker(id)
                } label: {
                    Label("Confirm", systemImage: "checkmark")
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.small)
                .help("Accept this name, lock it in, and reinforce it in your voice library.")
            }
        }
        }
    }

    @ViewBuilder
    private func speakerPill(speakerId: Int, interactive: Bool, context: String = "legend") -> some View {
        let name = speakerName(for: speakerId)
        let color = colorForSpeaker(speakerId)

        // Only the pill in the SAME place you clicked shows the editor — without
        // the context check, editingSpeakerId matched every pill for that
        // speaker (legend + each transcript row), so the field appeared down in
        // the transcript instead of where you tapped.
        if editingSpeakerId == speakerId && editingContext == context {
            // Text field + voice-library autocomplete under it (verify panel,
            // legend, and in-transcript pills all share this path).
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

                nameSuggestions(for: speakerId)
            }
        } else {
            Button {
                if interactive {
                    editingSpeakerId = speakerId
                    editingContext = context
                    editingName = speakerName(for: speakerId)
                    nameFieldFocused = true
                    // Refresh enrolled names when opening the editor so the
                    // dropdown is current (library may have grown since appear).
                    refreshLibraryNames()
                }
            } label: {
                HStack(spacing: 4) {
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
            .buttonStyle(.plain)
        }
    }

    @ViewBuilder
    private func segmentRow(segmentIndex idx: Int, segment: DiarizedSegment) -> some View {
        let words = segment.text.split(separator: " ", omittingEmptySubsequences: false).map(String.init)
        let activeRange: ClosedRange<Int>? = (selection?.segmentIndex == idx) ? selection?.range : nil

        VStack(alignment: .leading, spacing: 2) {
            HStack(alignment: .top, spacing: 8) {
                // Play button
                Button {
                    if audioPlayer.playingSegmentId == segment.id {
                        audioPlayer.stop()
                    } else {
                        audioPlayer.play(audioPath: audioPath, start: segment.start, end: segment.end, segmentId: segment.id, wordCount: words.count)
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
                }

                WordTokensView(
                    words: words,
                    activeRange: activeRange,
                    playingWord: audioPlayer.playingSegmentId == segment.id ? audioPlayer.playingWordIndex : nil,
                    onRangeChange: { newRange in
                        selection = SegmentSelection(segmentIndex: idx, range: newRange)
                    }
                )

                Spacer(minLength: 0)
            }
            .padding(.vertical, 2)

            if let active = activeRange {
                inlineSpeakerBar(segmentIndex: idx, range: active)
            }
        }
    }

    // MARK: - Actions

    private func commitRename(speakerId: Int) {
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
        enrollConfirmed(trimmed, speakerId: speakerId)

        saveTranscript()
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
    private func enrollConfirmed(_ name: String, speakerId: Int) {
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
        let existingSource = speakerMeta(for: id)?.source
        setMeta(id, source: existingSource == "user" ? "user" : "auto",
                verified: true, confidence: speakerMeta(for: id)?.confidence)
        enrollConfirmed(name, speakerId: id)
        saveTranscript()
        refreshConfidence()
        refreshLibraryNames()
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
        mapSpeaker(from: merge.from, to: merge.to)   // reassigns segments + saves
        // The surviving speaker now carries a confirmed, user-set identity.
        setMeta(merge.to, source: "user", verified: true, confidence: nil)
        enrollConfirmed(merge.name, speakerId: merge.to)
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

    /// Load the enrolled voice names for the rename autocomplete.
    private func refreshLibraryNames() {
        onListVoiceNames?() { names in self.libraryNames = names }
    }

    /// Autocomplete suggestions for the speaker currently being renamed: enrolled
    /// voices matching what's typed. Picking one maps to that exact voice.
    /// Shown under the pill in every edit context (verify / legend / segment).
    @ViewBuilder
    private func nameSuggestions(for id: Int) -> some View {
        let typed = editingName.trimmingCharacters(in: .whitespaces)
        let q = typed.lowercased()
        let current = speakerName(for: id).lowercased()
        // Browse the full library while the field is empty or still the generic
        // "Speaker N" — otherwise filter as the user types a real query.
        let browse = typed.isEmpty || isGenericName(typed)
        let matches = libraryNames
            .filter { browse || $0.lowercased().contains(q) }
            .filter { $0.lowercased() != current }
            .prefix(8)
        if !matches.isEmpty {
            VStack(alignment: .leading, spacing: 0) {
                Text("Map to an existing voice")
                    .font(.caption2)
                    .foregroundColor(.secondary)
                    .padding(.horizontal, 8)
                    .padding(.top, 4)
                ForEach(Array(matches), id: \.self) { name in
                    Button {
                        editingName = name
                        commitRename(speakerId: id)   // synchronous → maps to this exact voice
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
            try data.write(to: URL(fileURLWithPath: filePath))
            // Keep the sibling .md in sync: confirmed names only on disk.
            onRewriteMarkdown?(filePath)
        } catch {
            print("Failed to save transcript: \(error)")
        }
    }
}
