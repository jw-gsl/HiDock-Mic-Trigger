import SwiftUI

struct StatusBadge: View {
    let text: String
    let level: StatusLevel

    private var color: Color {
        switch level {
        // Pipeline progression ramp (cool, deepening): green → teal → indigo.
        case .success: return .green        // Downloaded — "I have the file"
        case .transcribed: return .teal      // text ready
        case .summarised: return .indigo     // AI-distilled — deepest
        // Source / structural markers, set apart from the ramp.
        case .info: return .blue             // Imported — external source
        case .merged: return .purple         // Merged — structural combination
        // User-action / attention states, warm & earthy; red is failure-only.
        case .skipped: return .brown         // parked on purpose
        case .removed: return .pink          // deliberate destructive (≠ error red)
        case .warning: return .orange        // needs attention
        case .error: return .red             // errors only
        case .secondary: return .secondary   // inert / on device
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

/// Status pill that becomes clickable when the recording carries a
/// download/import error. Tapping opens an alert with the captured
/// `lastError` text — same UX as the failed-transcription red X but
/// for the upstream Failed-to-download case. When there's no error
/// it renders identically to a plain `StatusBadge`.
struct ClickableStatusBadge: View {
    let text: String
    let level: StatusLevel
    let errorMessage: String?

    @State private var showingError = false

    var body: some View {
        if let msg = errorMessage, !msg.isEmpty {
            Button {
                showingError = true
            } label: {
                StatusBadge(text: text, level: level)
            }
            .buttonStyle(.plain)
            .help("Click to see why this failed")
            .alert("Download failed", isPresented: $showingError) {
                Button("OK", role: .cancel) {}
            } message: {
                Text(msg)
            }
        } else {
            StatusBadge(text: text, level: level)
        }
    }
}

struct TranscriptionIndicator: View {
    let entry: HiDockSyncRecordingEntry
    let transcriptionBusy: Bool
    let transcriptionCurrentFile: String?
    let transcriptionProgress: Int
    /// True when the most recent transcription attempt for this
    /// recording ended in failure or was cancelled. Surfaces as a red
    /// X in the tag column so the user can see retry candidates without
    /// opening the Queue window. Ignored when the recording has since
    /// been successfully transcribed.
    var transcriptionFailed: Bool = false
    /// Captured stderr / NSError text from the failed run. Shown in an
    /// alert when the user clicks the red X. Optional because the path
    /// may have failed before this field existed (older session) or the
    /// failure happened before we started capturing it.
    var transcriptionErrorMessage: String? = nil
    var onRevealTranscript: (String) -> Void = { _ in }
    var onOpenTranscriptViewer: ((String) -> Void)? = nil

    @State private var showingError = false

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
        } else if transcriptionFailed {
            Button {
                showingError = true
            } label: {
                Image(systemName: "xmark.circle.fill")
                    .foregroundColor(.red)
            }
            .buttonStyle(.plain)
            .help("Last transcription attempt failed or was cancelled — click to see why")
            .alert("Transcription failed", isPresented: $showingError) {
                Button("OK", role: .cancel) {}
            } message: {
                Text(transcriptionErrorMessage ?? "No error details captured for this attempt. Re-select and click Transcribe Selected to retry — the next failure will record details.")
            }
        } else {
            Text("—")
                .foregroundColor(.secondary.opacity(0.5))
        }
    }
}
