import CryptoKit
import SwiftUI

/// What `transcribe.py voice-check` proposes for one transcript.
struct VoiceCheckResult: Decodable {
    struct Line: Decodable, Identifiable {
        let index: Int
        let start: Double
        let end: Double
        let text: String
        let fromName: String
        let toName: String
        let score: Double
        /// Nil when the current name has no saved voice to compare with.
        let currentScore: Double?
        var id: Int { index }
    }

    struct Speaker: Decodable, Identifiable {
        struct Move: Decodable { let index: Int; let start: Double }
        let speakerId: String
        let name: String
        let suggested: String
        let score: Double
        let currentScore: Double?
        let lines: Int
        /// The lines a rename moves: all of the speaker's, except `kept`.
        let moves: [Move]
        /// Lines that clearly are the current name, left where they are.
        let kept: [Int]
        var id: String { speakerId }
    }

    var people: [String]?
    var voicesKnown: [String]?
    /// Named people with no saved voice outside this recording: their lines
    /// aren't checked, because there is nothing to compare them with.
    var noVoice: [String]?
    var lines: [Line]?
    var speakers: [Speaker]?
    var checked: Int?
    var skippedShort: Int?
    var note: String?
    var error: String?

    static func failure(_ message: String) -> VoiceCheckResult {
        VoiceCheckResult(error: message)
    }

    /// Whether there is anything for the reviewer to look at.
    var hasSuggestions: Bool { !(lines ?? []).isEmpty || !(speakers ?? []).isEmpty }

    /// The suggestions the pipeline saved beside this transcript
    /// (`voice-check --precompute`), or nil when there are none or the
    /// transcript has changed since they were computed.
    static func saved(forTranscript jsonPath: String) -> VoiceCheckResult? {
        let cachePath = jsonPath.replacingOccurrences(of: "_diarized.json", with: "_refine.json")
        guard cachePath != jsonPath,
              let cache = FileManager.default.contents(atPath: cachePath),
              let transcript = FileManager.default.contents(atPath: jsonPath),
              let payload = try? JSONSerialization.jsonObject(with: cache) as? [String: Any],
              let digest = payload["transcript_sha256"] as? String,
              let result = payload["result"],
              digest == SHA256.hash(data: transcript).map({ String(format: "%02x", $0) }).joined(),
              let resultData = try? JSONSerialization.data(withJSONObject: result)
        else { return nil }
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try? decoder.decode(VoiceCheckResult.self, from: resultData)
    }
}

extension Notification.Name {
    /// Posted with the transcript's `_diarized.json` path once the pipeline
    /// has saved (or failed to save) Refine suggestions for it.
    static let refineSuggestionsUpdated = Notification.Name("HiDockRefineSuggestionsUpdated")
}

/// One accepted correction, as `voice-check --apply` reads it.
struct VoiceCheckMove: Encodable {
    let index: Int
    let toName: String
    let start: Double
    /// Recorded with the decision, so Refine's accuracy can be measured.
    var fromName: String? = nil
    var score: Double? = nil
    var currentScore: Double? = nil
    var kind: String = "line"
}

/// Refine's suggestions, for the reviewer to check and apply.
///
/// Each suggestion shows the line, who it would move from and to, how strongly
/// the voice matches each, a play button, and Apply / Skip. "Apply all" takes
/// whatever is left. Applied and skipped rows leave the list; the panel closes
/// itself when nothing is left. A speaker-level suggestion moves that speaker's
/// lines except those that clearly are the current name.
///
/// The list used to sit in a ScrollView with only a maximum height, which
/// collapsed to nothing inside the sidecar: the reviewer saw "14 changes" and
/// no way to see what they were.
struct VoiceCheckPanel: View {
    let result: VoiceCheckResult
    /// Play one suggestion's line (by its transcript index).
    let onPlay: ((VoiceCheckResult.Line) -> Void)?
    /// Watched so the playing line's button becomes Stop.
    @ObservedObject var player: SegmentAudioPlayer
    /// The playback id a line runs under — the transcript row's own id, so the
    /// row below highlights as it plays.
    let playbackId: (VoiceCheckResult.Line) -> String
    /// Scroll the transcript to this time.
    let onShow: ((Double) -> Void)?
    let onApply: ([VoiceCheckMove]) -> Void
    /// Skipped suggestions, remembered so they aren't offered again.
    let onSkip: ([VoiceCheckMove]) -> Void
    let onClose: () -> Void

    @State private var lines: [VoiceCheckResult.Line]
    @State private var speakers: [VoiceCheckResult.Speaker]
    @State private var applied = 0

    init(result: VoiceCheckResult,
         player: SegmentAudioPlayer,
         playbackId: @escaping (VoiceCheckResult.Line) -> String,
         onPlay: ((VoiceCheckResult.Line) -> Void)? = nil,
         onShow: ((Double) -> Void)? = nil,
         onApply: @escaping ([VoiceCheckMove]) -> Void,
         onSkip: @escaping ([VoiceCheckMove]) -> Void = { _ in },
         onClose: @escaping () -> Void) {
        self.result = result
        self.onPlay = onPlay
        self.player = player
        self.playbackId = playbackId
        self.onShow = onShow
        self.onApply = onApply
        self.onSkip = onSkip
        self.onClose = onClose
        _lines = State(initialValue: result.lines ?? [])
        _speakers = State(initialValue: result.speakers ?? [])
    }

    private var remaining: Int { lines.count + speakers.count }

    private var headline: String {
        if remaining == 0 { return "Refine: all suggestions dealt with" }
        var parts: [String] = []
        if !speakers.isEmpty { parts.append("\(speakers.count) speaker\(speakers.count == 1 ? "" : "s")") }
        if !lines.isEmpty { parts.append("\(lines.count) line\(lines.count == 1 ? "" : "s")") }
        return "Refine suggests changing " + parts.joined(separator: " and ")
    }

    private var summary: String {
        let people = result.voicesKnown?.count ?? 0
        var text = "Checked \(result.checked ?? 0) lines against the saved voices of \(people) people in this meeting"
        if let short = result.skippedShort, short > 0 { text += " (\(short) too short to judge)" }
        text += ". Play a line to check it, then apply or skip it."
        if let missing = result.noVoice, !missing.isEmpty {
            text += " Not checked, no saved voice yet: \(missing.joined(separator: ", "))."
        }
        if applied > 0 { text += " \(applied) applied so far." }
        return text
    }

    /// "Jeff Chow 0.57", or "David McGregor: no saved voice" when there is
    /// nothing to compare (previously shown as 0.00, which read as a mismatch).
    static func match(_ name: String, _ score: Double?) -> String {
        guard let score else { return "\(name): no saved voice" }
        return "\(name) " + String(format: "%.2f", score)
    }

    private static func time(_ seconds: Double) -> String {
        let s = Int(seconds)
        return s >= 3600 ? String(format: "%d:%02d:%02d", s / 3600, s / 60 % 60, s % 60)
                         : String(format: "%d:%02d", s / 60, s % 60)
    }

    private func move(_ line: VoiceCheckResult.Line) -> VoiceCheckMove {
        VoiceCheckMove(index: line.index, toName: line.toName, start: line.start,
                       fromName: line.fromName, score: line.score, currentScore: line.currentScore)
    }

    private func moves(_ speaker: VoiceCheckResult.Speaker) -> [VoiceCheckMove] {
        // A line with its own suggestion goes where that suggestion says.
        let own = Set(lines.map(\.index))
        return speaker.moves.filter { !own.contains($0.index) }
            .map { VoiceCheckMove(index: $0.index, toName: speaker.suggested, start: $0.start,
                                  fromName: speaker.name, score: speaker.score,
                                  currentScore: speaker.currentScore, kind: "speaker") }
    }

    private func finishIfEmpty() {
        if remaining == 0 { onClose() }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .firstTextBaseline) {
                Text(headline).font(.headline)
                Spacer()
                Button("Close", action: onClose)
                    .buttonStyle(.borderless)
                    .help("Close without applying the rest")
            }
            Text(summary).font(.caption).foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            // A fixed minimum height: with only a maximum, the ScrollView
            // collapsed to zero inside the sidecar.
            ScrollView {
                VStack(alignment: .leading, spacing: 0) {
                    ForEach(speakers) { speaker in
                        speakerRow(speaker)
                        Divider()
                    }
                    ForEach(lines) { line in
                        lineRow(line)
                        Divider()
                    }
                }
            }
            .frame(minHeight: min(CGFloat(remaining) * 84, 340), maxHeight: 340)

            if remaining > 0 {
                HStack {
                    Spacer()
                    Button("Skip all") {
                        onSkip(speakers.flatMap(moves) + lines.map(move))
                        lines = []; speakers = []
                        onClose()
                    }
                    Button("Apply all \(remaining)") {
                        var all = speakers.flatMap(moves)
                        all += lines.map(move)
                        applied += remaining
                        lines = []; speakers = []
                        onApply(all)
                        onClose()
                    }
                    .buttonStyle(.borderedProminent)
                }
                .controlSize(.small)
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func speakerRow(_ speaker: VoiceCheckResult.Speaker) -> some View {
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: "person.crop.circle.badge.questionmark")
                .foregroundColor(.orange)
            VStack(alignment: .leading, spacing: 2) {
                Text("Everyone labelled “\(speaker.name)” sounds like \(speaker.suggested)")
                    .font(.callout.weight(.medium))
                Text("Voice match: \(Self.match(speaker.suggested, speaker.score)), "
                     + "\(Self.match(speaker.name, speaker.currentScore)) · moves \(speaker.moves.count) of \(speaker.lines) lines"
                     + (speaker.kept.isEmpty ? "" : " (keeps \(speaker.kept.count) that sound like \(speaker.name))"))
                    .font(.caption).foregroundColor(.secondary)
            }
            Spacer(minLength: 4)
            rowButtons(apply: {
                applied += 1
                onApply(moves(speaker))
                speakers.removeAll { $0.id == speaker.id }
                finishIfEmpty()
            }, skip: {
                onSkip(moves(speaker))
                speakers.removeAll { $0.id == speaker.id }
                finishIfEmpty()
            })
        }
        .padding(.vertical, 6)
    }

    private func lineRow(_ line: VoiceCheckResult.Line) -> some View {
        HStack(alignment: .top, spacing: 8) {
            if let onPlay {
                let playing = player.playingSegmentId == playbackId(line)
                Button {
                    if playing { player.stop() } else { onPlay(line) }
                } label: {
                    Image(systemName: playing ? "stop.circle.fill" : "play.circle").font(.title3)
                }
                .buttonStyle(.borderless)
                .help(playing ? "Stop" : "Play this line and show it in the transcript")
            }
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Button(Self.time(line.start)) { onShow?(line.start) }
                        .buttonStyle(.borderless)
                        .font(.caption.monospacedDigit())
                        .help("Show this line in the transcript")
                    Text(line.fromName).strikethrough().foregroundColor(.secondary)
                    Image(systemName: "arrow.right").font(.caption2).foregroundColor(.secondary)
                    Text(line.toName).fontWeight(.semibold)
                }
                .font(.caption)
                Text(line.text).font(.callout).lineLimit(3)
                    .fixedSize(horizontal: false, vertical: true)
                Text("Voice match: \(Self.match(line.toName, line.score)), \(Self.match(line.fromName, line.currentScore))")
                    .font(.caption2).foregroundColor(.secondary)
            }
            Spacer(minLength: 4)
            rowButtons(apply: {
                applied += 1
                onApply([move(line)])
                lines.removeAll { $0.id == line.id }
                finishIfEmpty()
            }, skip: {
                onSkip([move(line)])
                lines.removeAll { $0.id == line.id }
                finishIfEmpty()
            })
        }
        .padding(.vertical, 6)
    }

    private func rowButtons(apply: @escaping () -> Void, skip: @escaping () -> Void) -> some View {
        VStack(spacing: 4) {
            Button("Apply", action: apply).buttonStyle(.bordered)
            Button("Skip", action: skip).buttonStyle(.borderless).foregroundColor(.secondary)
        }
        .controlSize(.small)
    }
}
