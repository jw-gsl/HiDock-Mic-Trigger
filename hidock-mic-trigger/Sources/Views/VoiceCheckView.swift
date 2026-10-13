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
        let currentScore: Double
        var id: Int { index }
    }

    struct Speaker: Decodable, Identifiable {
        struct Move: Decodable { let index: Int; let start: Double }
        let speakerId: String
        let name: String
        let suggested: String
        let score: Double
        let currentScore: Double
        let lines: Int
        /// The lines a rename moves: all of the speaker's, except `kept`.
        let moves: [Move]
        /// Lines that clearly are the current name, left where they are.
        let kept: [Int]
        var id: String { speakerId }
    }

    var people: [String]?
    var voicesKnown: [String]?
    var lines: [Line]?
    var speakers: [Speaker]?
    var checked: Int?
    var skippedShort: Int?
    var note: String?
    var error: String?

    static func failure(_ message: String) -> VoiceCheckResult {
        VoiceCheckResult(error: message)
    }
}

/// One accepted correction, as `voice-check --apply` reads it.
struct VoiceCheckMove: Encodable {
    let index: Int
    let toName: String
    let start: Double
}

/// The proposals from a voice check, for the reviewer to accept or untick.
///
/// Everything starts ticked: on Rec63 four in five proposals matched what the
/// reviewer then did by hand, so unticking the odd wrong one is quicker than
/// ticking the rest. Accepting a whole speaker moves its lines except those
/// that clearly are the current name, and a line's own proposal still wins.
struct VoiceCheckPanel: View {
    let result: VoiceCheckResult
    let onPlay: ((Double, Double) -> Void)?
    let onApply: ([VoiceCheckMove]) -> Void
    let onCancel: () -> Void

    @State private var acceptedSpeakers: Set<String>
    @State private var acceptedLines: Set<Int>

    init(result: VoiceCheckResult,
         onPlay: ((Double, Double) -> Void)? = nil,
         onApply: @escaping ([VoiceCheckMove]) -> Void,
         onCancel: @escaping () -> Void) {
        self.result = result
        self.onPlay = onPlay
        self.onApply = onApply
        self.onCancel = onCancel
        _acceptedSpeakers = State(initialValue: Set((result.speakers ?? []).map(\.id)))
        _acceptedLines = State(initialValue: Set((result.lines ?? []).map(\.id)))
    }

    private var moves: [VoiceCheckMove] {
        var byIndex: [Int: VoiceCheckMove] = [:]
        for speaker in result.speakers ?? [] where acceptedSpeakers.contains(speaker.id) {
            for line in speaker.moves {
                byIndex[line.index] = VoiceCheckMove(index: line.index, toName: speaker.suggested, start: line.start)
            }
        }
        for line in result.lines ?? [] where acceptedLines.contains(line.id) {
            byIndex[line.index] = VoiceCheckMove(index: line.index, toName: line.toName, start: line.start)
        }
        return byIndex.values.sorted { $0.index < $1.index }
    }

    private var headline: String {
        let lines = result.lines?.count ?? 0, speakers = result.speakers?.count ?? 0
        var parts: [String] = []
        if speakers > 0 { parts.append("\(speakers) speaker\(speakers == 1 ? "" : "s")") }
        if lines > 0 { parts.append("\(lines) line\(lines == 1 ? "" : "s")") }
        return "Refine suggests changing " + parts.joined(separator: " and ")
    }

    private var summary: String {
        let people = result.voicesKnown?.count ?? 0
        var text = "Checked \(result.checked ?? 0) lines against the saved voices of \(people) people in this meeting"
        if let short = result.skippedShort, short > 0 { text += " (\(short) too short to judge)" }
        return text + "."
    }

    private static func time(_ seconds: Double) -> String {
        let s = Int(seconds)
        return s >= 3600 ? String(format: "%d:%02d:%02d", s / 3600, s / 60 % 60, s % 60)
                         : String(format: "%d:%02d", s / 60, s % 60)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text(headline).font(.headline)
            Text(summary).font(.caption).foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            ScrollView {
                VStack(alignment: .leading, spacing: 10) {
                    if let speakers = result.speakers, !speakers.isEmpty {
                        Text("Speakers").font(.subheadline.weight(.semibold))
                        ForEach(speakers) { speaker in
                            Toggle(isOn: binding(for: speaker.id, in: $acceptedSpeakers)) {
                                VStack(alignment: .leading, spacing: 2) {
                                    Text("“\(speaker.name)” sounds like \(speaker.suggested)")
                                    Text(String(format: "%.2f vs %.2f · moves %d of %d lines", speaker.score,
                                                speaker.currentScore, speaker.moves.count, speaker.lines)
                                         + (speaker.kept.isEmpty ? ""
                                            : " · keeps \(speaker.kept.count) that sound like \(speaker.name)"))
                                        .font(.caption).foregroundColor(.secondary)
                                }
                            }
                        }
                    }
                    if let lines = result.lines, !lines.isEmpty {
                        HStack {
                            Text("Lines (\(lines.count))").font(.subheadline.weight(.semibold))
                            Spacer()
                            Button(acceptedLines.count == lines.count ? "None" : "All") {
                                acceptedLines = acceptedLines.count == lines.count ? [] : Set(lines.map(\.id))
                            }
                            .buttonStyle(.borderless).font(.caption)
                        }
                        ForEach(lines) { line in
                            HStack(alignment: .top, spacing: 6) {
                                Toggle("", isOn: binding(for: line.id, in: $acceptedLines)).labelsHidden()
                                VStack(alignment: .leading, spacing: 2) {
                                    HStack(spacing: 4) {
                                        Text(Self.time(line.start)).monospacedDigit()
                                        Text("\(line.fromName) → \(line.toName)").fontWeight(.medium)
                                        Text(String(format: "%.2f vs %.2f", line.score, line.currentScore))
                                            .foregroundColor(.secondary)
                                    }
                                    .font(.caption)
                                    Text(line.text).font(.caption).lineLimit(2)
                                        .foregroundColor(.secondary)
                                }
                                Spacer(minLength: 4)
                                if let onPlay {
                                    Button { onPlay(line.start, line.end) } label: {
                                        Image(systemName: "play.circle")
                                    }
                                    .buttonStyle(.borderless)
                                    .help("Play this line")
                                }
                            }
                        }
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .frame(maxHeight: 420)

            HStack {
                Spacer()
                Button("Cancel", action: onCancel)
                Button("Apply \(moves.count) change\(moves.count == 1 ? "" : "s")") { onApply(moves) }
                    .buttonStyle(.borderedProminent)
                    .disabled(moves.isEmpty)
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func binding<T: Hashable>(for id: T, in set: Binding<Set<T>>) -> Binding<Bool> {
        Binding(get: { set.wrappedValue.contains(id) },
                set: { if $0 { set.wrappedValue.insert(id) } else { set.wrappedValue.remove(id) } })
    }
}
