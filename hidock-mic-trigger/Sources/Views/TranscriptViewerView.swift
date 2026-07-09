import AppKit
import SwiftUI
import AVFoundation

// MARK: - Audio Player

class SegmentAudioPlayer: ObservableObject {
    @Published var playingSegmentId: String?
    private var player: AVAudioPlayer?
    private var stopTimer: Timer?

    func play(audioPath: String, start: Double, end: Double, segmentId: String) {
        stop()
        // fileURLWithPath, not URL(string: "file://…") — the latter returns
        // nil for any path containing a space (un-percent-encoded), silently
        // breaking playback for user-chosen folders like "My Recordings".
        let url = URL(fileURLWithPath: audioPath)
        guard let player = try? AVAudioPlayer(contentsOf: url) else { return }
        self.player = player
        player.currentTime = start
        player.play()
        playingSegmentId = segmentId
        let duration = end - start
        stopTimer = Timer.scheduledTimer(withTimeInterval: duration, repeats: false) { [weak self] _ in
            self?.stop()
        }
    }

    func stop() {
        player?.stop()
        player = nil
        stopTimer?.invalidate()
        stopTimer = nil
        playingSegmentId = nil
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
                let display = (i == words.count - 1) ? w : "\(w) "
                Text(display)
                    .font(.body)
                    .background(inRange ? Color.blue.opacity(0.28) : Color.clear)
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
                if !transcriptHistory.isEmpty {
                    Button {
                        undoMerge()
                    } label: {
                        Label("Undo", systemImage: "arrow.uturn.backward")
                    }
                    .buttonStyle(.bordered)
                    .controlSize(.small)
                    .keyboardShortcut("z", modifiers: .command)
                }

                if onRediarize != nil {
                    Stepper("Speakers: \(rediarizeNSpeakers)", value: $rediarizeNSpeakers, in: 2...8)
                        .font(.caption)
                        .frame(width: 140)

                    Button {
                        onRediarize?(filePath, rediarizeNSpeakers)
                    } label: {
                        Label("Re-diarize", systemImage: "person.2.wave.2")
                    }
                    .buttonStyle(.bordered)
                    .controlSize(.small)
                }

                // Layer 2 — re-cluster the rest of the transcript using
                // the segments the user has already named as anchors.
                // Only useful when at least one speaker has been
                // renamed away from the default "Speaker N", so we
                // hide the button otherwise.
                if onReclusterWithLabels != nil, hasUserNamedSpeakers {
                    Button {
                        onReclusterWithLabels?(filePath)
                    } label: {
                        Label("Re-cluster", systemImage: "person.crop.circle.badge.checkmark")
                    }
                    .buttonStyle(.bordered)
                    .controlSize(.small)
                    .fixedSize()
                    .help("Re-assign every un-named segment to its closest match, using the speakers you've named as anchors. Segments you've already corrected stay put.")
                }

                Button {
                    copyAllToClipboard()
                } label: {
                    Label("Copy All", systemImage: "doc.on.doc")
                }
                .buttonStyle(.bordered)
                .controlSize(.small)
                .keyboardShortcut("c", modifiers: [.command, .shift])

                Button {
                    // Reveal the markdown transcript file in Finder
                    let mdPath = filePath.replacingOccurrences(of: "_diarized.json", with: ".md")
                    NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: mdPath)])
                } label: {
                    Label("Show File", systemImage: "folder")
                }
                .buttonStyle(.bordered)
                .controlSize(.small)
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 10)
            .background(.ultraThinMaterial)

            Divider()

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
                                    if uniqueSpeakerIds.count > 1 {
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

            // Segments list
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 4) {
                    ForEach(Array(transcript.segments.enumerated()), id: \.element.id) { idx, segment in
                        segmentRow(segmentIndex: idx, segment: segment)
                    }
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 8)
            }
        }
        .frame(minWidth: 600, minHeight: 400)
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
        let startWord = wordRange.lowerBound
        let endWord = wordRange.upperBound
        guard startWord >= 0, endWord < words.count, startWord <= endWord else { return }

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

    // MARK: - Stats Header

    private var statsHeader: some View {
        HStack(spacing: 12) {
            Text(formatTime(seconds: totalDuration))
                .font(.caption.weight(.medium))
                .foregroundColor(.secondary)

            // Talk time split per speaker — visual bars
            let totalTalk = speakerStats.reduce(0.0) { $0 + $1.talkTime }
            ForEach(speakerStats, id: \.speakerId) { stat in
                let pct = totalTalk > 0 ? stat.talkTime / totalTalk * 100 : 0
                let color = colorForSpeaker(stat.speakerId)
                let wpm = stat.talkTime > 0 ? Int(Double(stat.wordCount) / (stat.talkTime / 60)) : 0
                HStack(spacing: 4) {
                    Circle().fill(color).frame(width: 6, height: 6)
                    Text("\(speakerName(for: stat.speakerId)) \(String(format: "%.0f%%", pct))  \(wpm)wpm")
                        .font(.caption)
                }
            }

            // Stacked bar showing the split visually
            GeometryReader { geo in
                HStack(spacing: 1) {
                    ForEach(speakerStats, id: \.speakerId) { stat in
                        let frac = totalTalk > 0 ? stat.talkTime / totalTalk : 0
                        RoundedRectangle(cornerRadius: 2)
                            .fill(colorForSpeaker(stat.speakerId).opacity(0.7))
                            .frame(width: max(2, geo.size.width * CGFloat(frac)))
                    }
                }
            }
            .frame(width: 120, height: 10)

            Spacer()
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
            }
            ForEach(uniqueSpeakerIds, id: \.self) { id in
                speakerVerifyRow(id: id)
            }
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

        HStack(spacing: 8) {
            speakerPill(speakerId: id, interactive: true)   // tap to rename/correct

            Text(prov.text)
                .font(.caption2.weight(.medium))
                .padding(.horizontal, 6)
                .padding(.vertical, 2)
                .background(prov.color.opacity(0.15), in: Capsule())
                .foregroundColor(prov.color)

            Spacer()

            if verified {
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
    }

    @ViewBuilder
    private func speakerPill(speakerId: Int, interactive: Bool) -> some View {
        let name = speakerName(for: speakerId)
        let color = colorForSpeaker(speakerId)

        if editingSpeakerId == speakerId {
            HStack(spacing: 4) {
                Circle()
                    .fill(color)
                    .frame(width: 8, height: 8)
                TextField("Name", text: $editingName, onCommit: {
                    commitRename(speakerId: speakerId)
                })
                .textFieldStyle(.roundedBorder)
                .frame(width: 120)
                .controlSize(.small)
            }
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .background(color.opacity(0.15))
            .cornerRadius(12)
        } else {
            Button {
                if interactive {
                    editingSpeakerId = speakerId
                    editingName = speakerName(for: speakerId)
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
                        audioPlayer.play(audioPath: audioPath, start: segment.start, end: segment.end, segmentId: segment.id)
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
                    speakerPill(speakerId: segment.speakerId, interactive: true)
                }

                WordTokensView(
                    words: words,
                    activeRange: activeRange,
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
        guard !trimmed.isEmpty else {
            editingSpeakerId = nil
            return
        }

        transcript.speakerNames["\(speakerId)"] = trimmed
        editingSpeakerId = nil

        // Typing a name IS confirming it — mark verified/user so the meeting
        // counts as reviewed and stops nagging.
        setMeta(speakerId, source: "user", verified: true, confidence: nil)

        // Find a segment from this speaker to use for enrollment
        if let segment = transcript.segments.first(where: { $0.speakerId == speakerId }) {
            onEnrollSpeaker(trimmed, audioPath, segment.start, segment.end)
        }

        saveTranscript()
    }

    // MARK: - Speaker verification (provenance + confirm loop)

    /// True for an untouched "Speaker N" label.
    private func isGenericName(_ name: String) -> Bool {
        name.range(of: #"^Speaker \d+$"#, options: .regularExpression) != nil
    }

    private func speakerMeta(for id: Int) -> SpeakerMeta? {
        transcript.speakerMeta?["\(id)"]
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

        if verified {
            return source == "unknown" ? ("unknown", .secondary) : ("confirmed", .green)
        }
        switch source {
        case "auto":
            if let c = m?.confidence { return ("auto \(Int((c * 100).rounded()))%", .blue) }
            return ("auto", .blue)
        case "unknown":
            return ("unknown", .secondary)
        default:
            return isGenericName(name) ? ("unnamed", .orange) : ("auto", .blue)
        }
    }

    /// Any multi-speaker meeting with an unverified speaker still to review.
    private var needsVerification: Bool {
        guard uniqueSpeakerIds.count > 1 else { return false }
        return uniqueSpeakerIds.contains { !(speakerMeta(for: $0)?.verified ?? false) }
    }

    /// Confirm the current (auto/typed) name — lock it in and reinforce the
    /// voice library so future meetings match this voice better.
    private func confirmSpeaker(_ id: Int) {
        let name = speakerName(for: id)
        guard !isGenericName(name) else { return }   // nothing to confirm without a name
        let existingSource = speakerMeta(for: id)?.source
        setMeta(id, source: existingSource == "user" ? "user" : "auto",
                verified: true, confidence: speakerMeta(for: id)?.confidence)
        if let segment = transcript.segments.first(where: { $0.speakerId == id }) {
            onEnrollSpeaker(name, audioPath, segment.start, segment.end)
        }
        saveTranscript()
    }

    /// Acknowledge a speaker the user genuinely can't name — counts as reviewed
    /// but is NOT enrolled into the voice library.
    private func markUnknown(_ id: Int) {
        setMeta(id, source: "unknown", verified: true, confidence: nil)
        saveTranscript()
    }

    private func copyAllToClipboard() {
        var lines: [String] = []
        for seg in transcript.segments {
            let ts = "[\(formatTime(seconds: seg.start))]"
            if hasSpeakers {
                let name = speakerName(for: seg.speakerId)
                lines.append("\(ts) \(name): \(seg.text)")
            } else {
                lines.append("\(ts) \(seg.text)")
            }
        }
        let text = lines.joined(separator: "\n\n")
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(text, forType: .string)
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

        // Re-merge consecutive same-speaker segments
        var merged: [DiarizedSegment] = []
        for seg in transcript.segments {
            if let last = merged.last, last.speakerId == seg.speakerId {
                var updated = merged.removeLast()
                updated.text += " " + seg.text
                // Can't mutate end directly on the struct — rebuild
                merged.append(DiarizedSegment(start: updated.start, end: seg.end, speakerId: updated.speakerId, text: updated.text))
            } else {
                merged.append(seg)
            }
        }
        transcript.segments = merged

        saveTranscript()
    }

    private func saveTranscript() {
        do {
            let encoder = JSONEncoder()
            encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
            let data = try encoder.encode(transcript)
            try data.write(to: URL(fileURLWithPath: filePath))
        } catch {
            print("Failed to save transcript: \(error)")
        }
    }
}
