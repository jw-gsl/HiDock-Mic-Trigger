import SwiftUI

struct StatusBadge: View {
    let text: String
    let level: StatusLevel

    private var color: Color {
        switch level {
        case .success: return .green
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

    var body: some View {
        if entry.transcribed {
            Button {
                if let path = entry.transcriptPath {
                    onRevealTranscript(path)
                }
            } label: {
                Image(systemName: "checkmark.circle.fill")
                    .foregroundColor(.green)
            }
            .buttonStyle(.plain)
            .help("Show transcript in Finder")
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
