import SwiftUI

struct StatusBadge: View {
    let text: String
    let level: StatusLevel

    private var color: Color {
        switch level {
        case .success: return .green
        case .transcribed: return .purple   // distinct from Downloaded's green
        case .warning: return .orange
        case .error: return .red
        case .info: return .blue
        case .secondary: return .secondary
        case .normal: return .primary
        }
    }

    var body: some View {
        Text(text)
            .font(.caption.weight(.medium))
            .foregroundColor(color)
            .padding(.horizontal, 6)
            .padding(.vertical, 2)
            .background(color.opacity(0.12), in: Capsule())
    }
}

struct TranscriptionIndicator: View {
    let entry: HiDockSyncRecordingEntry
    let transcriptionBusy: Bool
    let transcriptionCurrentFile: String?
    let transcriptionProgress: Int
    var onRevealTranscript: (String) -> Void = { _ in }
    var onOpenTranscriptViewer: ((String) -> Void)? = nil

    var body: some View {
        if entry.transcribed && entry.speakersTagged {
            // Fully ready — green checkmark
            Button {
                if let path = entry.transcriptPath {
                    onRevealTranscript(path)
                }
            } label: {
                Image(systemName: "checkmark.circle.fill")
                    .foregroundColor(.green)
            }
            .buttonStyle(.plain)
            .help("Transcript ready — show in Finder")
        } else if entry.transcribed && !entry.speakersTagged {
            // Transcribed but speakers need tagging — orange tag
            Button {
                if let path = entry.transcriptPath, let handler = onOpenTranscriptViewer {
                    handler(path)
                } else if let path = entry.transcriptPath {
                    onRevealTranscript(path)
                }
            } label: {
                Image(systemName: "tag.fill")
                    .foregroundColor(.orange)
            }
            .buttonStyle(.plain)
            .help("Speakers need tagging — click to open")
        } else if transcriptionBusy && transcriptionCurrentFile == entry.recording.outputName {
            HStack(spacing: 4) {
                ProgressView()
                    .controlSize(.mini)
                Text("\(transcriptionProgress)%")
                    .font(.caption.monospacedDigit())
                    .foregroundColor(.orange)
            }
        } else {
            Text("—")
                .foregroundColor(.secondary.opacity(0.5))
        }
    }
}
