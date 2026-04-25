import SwiftUI

struct SyncHeaderSection: View {
    @ObservedObject var viewModel: HiDockViewModel

    private var statusColor: Color {
        switch viewModel.syncStatusLevel {
        case .success: return .green
        case .transcribed: return .purple
        case .warning: return .orange
        case .error: return .red
        case .info: return .blue
        case .secondary: return .secondary
        case .normal: return .primary
        case .skipped: return Color.teal.opacity(0.6)
        case .removed: return Color.red.opacity(0.6)
        }
    }

    private var isConnected: Bool {
        viewModel.syncStatusLevel == .success
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            // Device strip — one card per paired device, plus an imports
            // card when relevant. Every fact about a device (connected /
            // recording / unreachable / storage / reconnect / filter)
            // lives on its own card, so the header doesn't need separate
            // status / storage / filter rows any more.
            DeviceStripView(viewModel: viewModel)

            // Generic pipeline-status line: transcription progress, skip
            // confirmations, auto-flow messages. Per-device connection
            // state lives on the cards above, so this row is hidden when
            // there's no pipeline message to surface — prevents the
            // redundant "Connected — 🔊 P1" line showing the same thing
            // as the cards.
            //
            // Hidden entirely when the TranscriptionProgressBar (top of
            // MainWindowView) is already rendering a live
            // "Transcribing N/M — p% · <stage>" line with its own
            // progress bar and cancel button. Two places showing the
            // same transcription status ended up racing each other;
            // one well-designed indicator wins.
            if !viewModel.transcriptionBusy && !viewModel.trimBusy
                && (!viewModel.syncStatus.isEmpty || !viewModel.syncSummary.isEmpty) {
                HStack(spacing: 6) {
                    if !viewModel.syncStatus.isEmpty {
                        Circle()
                            .fill(statusColor)
                            .frame(width: 8, height: 8)
                        Text(viewModel.syncStatus)
                            .font(.caption)
                            .foregroundColor(statusColor == .secondary ? .secondary : statusColor)
                    }
                    Spacer()
                    if !viewModel.syncSummary.isEmpty {
                        Text(viewModel.syncSummary)
                            .font(.caption)
                            .foregroundColor(.secondary)
                    }
                }
            }

            // Download actions stay in the header until the Process split
            // button ships — grouped with the device context, same pattern
            // as before, minus the folder-picker clutter (now in the app
            // menu bar).
            HStack(spacing: 6) {
                Button {
                    viewModel.onDownloadSelected()
                } label: {
                    // Re-label when any selected recording is a
                    // locally-trimmed file — the extractor would
                    // overwrite the trimmed bytes with the device
                    // original, so the user should see that framing
                    // before clicking.
                    let label = viewModel.selectionIncludesTrimmed
                        ? "Re-download Selected"
                        : "Download Selected"
                    Label(label, systemImage: "arrow.down.circle")
                }
                .disabled(viewModel.syncBusy || !viewModel.syncPaired || !viewModel.hasSelection)
                .help(viewModel.selectionIncludesTrimmed
                      ? "At least one selected recording is trimmed — re-downloading will replace the trimmed local file with the device's original."
                      : "Download the selected recordings from the device")

                // Hide Download New when auto-download is on — it'd be
                // redundant: any new file appearing would auto-download
                // anyway. Showing it would just bait users into manual
                // double-trips. If the user toggles auto-download off,
                // the button comes back.
                if !viewModel.syncAutoDownload {
                    Button {
                        viewModel.onDownloadNew()
                    } label: {
                        Label("Download New", systemImage: "arrow.down.to.line")
                    }
                    .disabled(viewModel.syncBusy || !viewModel.syncPaired)
                }

                Button {
                    viewModel.onMarkDownloaded()
                } label: {
                    Label("Skip", systemImage: "forward.fill")
                }
                .disabled(viewModel.syncBusy || !viewModel.hasSelection)

                if viewModel.anySelectedMarkedOnly {
                    Button {
                        viewModel.onUnmarkDownloaded()
                    } label: {
                        Label("Unskip", systemImage: "backward.fill")
                    }
                    .disabled(viewModel.syncBusy)
                }

                Spacer()
            }
            .buttonStyle(.bordered)
            .controlSize(.small)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
    }
}
