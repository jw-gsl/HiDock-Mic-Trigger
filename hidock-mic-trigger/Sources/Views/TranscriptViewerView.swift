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

// MARK: - TranscriptViewerView

struct TranscriptViewerView: View {
    @State var transcript: DiarizedTranscript
    @State var editingSpeakerId: Int? = nil
    @State var editingName: String = ""
    @State var rediarizeNSpeakers: Int = 2
    @State var transcriptHistory: [DiarizedTranscript] = []
    @StateObject var audioPlayer = SegmentAudioPlayer()
    let filePath: String
    let audioPath: String
    let onEnrollSpeaker: (String, String, Double, Double) -> Void
    var onRediarize: ((String, Int?) -> Void)?

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

                Button {
                    copyAllToClipboard()
                } label: {
                    Label("Copy All", systemImage: "doc.on.doc")
                }
                .buttonStyle(.bordered)
                .controlSize(.small)
                .keyboardShortcut("c", modifiers: [.command, .shift])

                Button {
                    saveTranscript()
                } label: {
                    Label("Save", systemImage: "square.and.arrow.down")
                }
                .buttonStyle(.bordered)
                .controlSize(.small)
                .help("Save speaker name changes to the transcript JSON file")
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 10)
            .background(.ultraThinMaterial)

            Divider()

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
