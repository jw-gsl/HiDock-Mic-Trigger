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
        guard let url = URL(string: "file://\(audioPath)"),
              let player = try? AVAudioPlayer(contentsOf: url) else { return }
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

struct DiarizedTranscript: Codable {
    var version: Int
    var audioFile: String
    var segments: [DiarizedSegment]
    var speakerNames: [String: String]

    enum CodingKeys: String, CodingKey {
        case version
        case audioFile = "audio_file"
        case segments
        case speakerNames = "speaker_names"
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

/// Word-token wrapping layout used by the split-segment sheet. macOS 13+
/// gets us the SwiftUI `Layout` protocol; this is the smallest version
/// we need: flow children left-to-right, wrap when the next child would
/// exceed the proposed width.
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

// MARK: - TranscriptViewerView

struct TranscriptViewerView: View {
    @State var transcript: DiarizedTranscript
    @State var editingSpeakerId: Int? = nil
    @State var editingName: String = ""
    @State var rediarizeNSpeakers: Int = 2
    @State var transcriptHistory: [DiarizedTranscript] = []
    /// Index of the segment the user is currently splitting (Layer 1).
    /// Drives `splitSheetVisible`. Cleared when the sheet dismisses.
    @State var splittingSegmentIndex: Int? = nil
    /// Word index inside the splitting segment that the user clicked.
    /// `nil` means the sheet is open but no word has been picked yet.
    @State var splitWordIndex: Int? = nil
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
                        Label("Re-cluster from my labels", systemImage: "person.crop.circle.badge.checkmark")
                    }
                    .buttonStyle(.bordered)
                    .controlSize(.small)
                    .help("Use the speakers you've named as anchors and re-assign every other segment to its closest match. The pieces of the conversation you've already corrected stay put.")
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

            // Segments list
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 4) {
                    ForEach(transcript.segments) { segment in
                        segmentRow(segment: segment)
                    }
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 8)
            }
        }
        .frame(minWidth: 600, minHeight: 400)
        .sheet(isPresented: Binding(
            get: { splittingSegmentIndex != nil },
            set: { if !$0 { splittingSegmentIndex = nil; splitWordIndex = nil } }
        )) {
            if let idx = splittingSegmentIndex, idx < transcript.segments.count {
                splitSegmentSheet(segmentIndex: idx)
            }
        }
    }

    // MARK: - Split-segment sheet (Layer 1)

    /// Sheet that lets the user mark the first word of a new speaker
    /// inside an existing segment. Two-step:
    ///   1. Click a word — that becomes the cut point. The word and
    ///      everything after it form the second sub-segment.
    ///   2. Pick the speaker for the second sub-segment (existing
    ///      speaker pill or "New speaker"). On confirm, we split
    ///      the segment, save, and trigger an enrolment sample for
    ///      the second sub-segment under the chosen speaker name.
    /// Time boundary is estimated by linear interpolation across the
    /// segment's words — see PLAN-voice-training-layers-2026-04-26.md
    /// for why this is good enough.
    @ViewBuilder
    private func splitSegmentSheet(segmentIndex idx: Int) -> some View {
        let segment = transcript.segments[idx]
        let words = segment.text.split(separator: " ", omittingEmptySubsequences: false).map(String.init)

        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Image(systemName: "scissors")
                    .foregroundColor(.blue)
                Text("Split segment at a word")
                    .font(.headline)
                Spacer()
                Button("Cancel") {
                    splittingSegmentIndex = nil
                    splitWordIndex = nil
                }
                .keyboardShortcut(.cancelAction)
            }

            Text("Click the first word that belongs to a different speaker. The picked word and everything after it become a new sub-segment.")
                .font(.caption)
                .foregroundColor(.secondary)

            // Original segment header (timestamp + current speaker)
            HStack(spacing: 8) {
                Text("[\(formatTime(seconds: segment.start)) – \(formatTime(seconds: segment.end))]")
                    .font(.system(.caption, design: .monospaced))
                    .foregroundColor(.secondary)
                speakerPill(speakerId: segment.speakerId, interactive: false)
            }

            // The word grid. Wraps via FlowLayout. Selected word has
            // a stronger blue tint; everything from it onward gets a
            // lighter tint to show the user what becomes the new
            // sub-segment.
            ScrollView {
                FlowLayout(spacing: 4, lineSpacing: 6) {
                    ForEach(Array(words.enumerated()), id: \.offset) { i, w in
                        let isPicked = (splitWordIndex == i)
                        let isInSecondHalf = (splitWordIndex.map { i >= $0 } ?? false)
                        Button {
                            // First word can't be the cut — that'd
                            // mean an empty first sub-segment.
                            guard i > 0 else { return }
                            splitWordIndex = i
                        } label: {
                            Text(w)
                                .font(.body)
                                .padding(.horizontal, 6)
                                .padding(.vertical, 2)
                                .background(
                                    isPicked
                                        ? Color.blue.opacity(0.35)
                                        : (isInSecondHalf ? Color.blue.opacity(0.12) : Color.clear)
                                )
                                .cornerRadius(4)
                        }
                        .buttonStyle(.plain)
                        .help(i == 0
                              ? "The first word can't be the cut point — pick word 2 or later."
                              : "Click to make '\(w)' the start of a new sub-segment")
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(8)
                .background(Color.secondary.opacity(0.06), in: RoundedRectangle(cornerRadius: 6))
            }
            .frame(maxHeight: 220)

            // Speaker picker — only revealed once the user has picked
            // a cut point. Blue title to nudge: pick word, then pick
            // speaker, then confirm.
            if let cut = splitWordIndex {
                Divider()
                Text("Assign words from '\(words[cut])' onward to:")
                    .font(.caption.weight(.medium))
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        ForEach(uniqueSpeakerIds, id: \.self) { sid in
                            Button {
                                applySplit(segmentIndex: idx, atWord: cut, secondHalfSpeakerId: sid)
                            } label: {
                                speakerPill(speakerId: sid, interactive: false)
                            }
                            .buttonStyle(.plain)
                        }
                        Button {
                            applySplit(segmentIndex: idx, atWord: cut, secondHalfSpeakerId: nextNewSpeakerId())
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
            }
        }
        .padding(16)
        .frame(minWidth: 540, idealWidth: 640, minHeight: 320)
    }

    /// Lowest unused speakerId — used when the user picks "New speaker"
    /// in the split sheet.
    private func nextNewSpeakerId() -> Int {
        let used = Set(transcript.segments.map(\.speakerId))
        var n = 0
        while used.contains(n) { n += 1 }
        return n
    }

    /// Layer 1 split implementation.
    /// Cuts `segments[idx]` at word `wordIndex` (1-indexed in user
    /// terms — word 0 stays in the first half because we reject
    /// cut-at-word-0 in the UI). Assigns the second half to
    /// `secondHalfSpeakerId`. Time boundary estimated by linear
    /// interpolation over word count: words [0..wordIndex) end at
    /// `start + (wordIndex/totalWords) * duration`.
    private func applySplit(segmentIndex idx: Int, atWord wordIndex: Int, secondHalfSpeakerId: Int) {
        let segment = transcript.segments[idx]
        let words = segment.text.split(separator: " ", omittingEmptySubsequences: false).map(String.init)
        guard wordIndex > 0, wordIndex < words.count else {
            splittingSegmentIndex = nil
            splitWordIndex = nil
            return
        }

        transcriptHistory.append(transcript)

        let duration = max(segment.end - segment.start, 0.001)
        let boundaryFrac = Double(wordIndex) / Double(words.count)
        let boundaryTime = segment.start + boundaryFrac * duration

        let firstText = words[0..<wordIndex].joined(separator: " ")
        let secondText = words[wordIndex..<words.count].joined(separator: " ")

        let firstHalf = DiarizedSegment(
            start: segment.start,
            end: boundaryTime,
            speakerId: segment.speakerId,
            text: firstText
        )
        let secondHalf = DiarizedSegment(
            start: boundaryTime,
            end: segment.end,
            speakerId: secondHalfSpeakerId,
            text: secondText
        )

        var updated = transcript.segments
        updated.remove(at: idx)
        updated.insert(secondHalf, at: idx)
        updated.insert(firstHalf, at: idx)
        transcript.segments = updated

        // Trigger enrolment for the second half — it's a freshly
        // labelled sample with cleaner provenance than mid-segment
        // contamination would give us.
        let secondName = speakerName(for: secondHalfSpeakerId)
        onEnrollSpeaker(secondName, audioPath, boundaryTime, segment.end)

        saveTranscript()
        splittingSegmentIndex = nil
        splitWordIndex = nil
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
    private func segmentRow(segment: DiarizedSegment) -> some View {
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

            Text(segment.text)
                .font(.body)
                .fixedSize(horizontal: false, vertical: true)
                .textSelection(.enabled)

            Spacer(minLength: 0)
        }
        .padding(.vertical, 2)
        .contextMenu {
            // Layer 1 — only offer mid-segment splitting when the
            // segment actually has multiple words. A one-word segment
            // can't be split meaningfully.
            if segment.text.split(separator: " ").count > 1,
               let idx = transcript.segments.firstIndex(where: { $0.start == segment.start && $0.speakerId == segment.speakerId }) {
                Button {
                    splittingSegmentIndex = idx
                    splitWordIndex = nil
                } label: {
                    Label("Split segment at a word…", systemImage: "scissors")
                }
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

        // Find a segment from this speaker to use for enrollment
        if let segment = transcript.segments.first(where: { $0.speakerId == speakerId }) {
            onEnrollSpeaker(trimmed, audioPath, segment.start, segment.end)
        }

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
