import SwiftUI

struct SyncHeaderSection: View {
    @ObservedObject var viewModel: HiDockViewModel

    private var statusColor: Color {
        switch viewModel.syncStatusLevel {
        case .success: return .green
        case .transcribed: return .purple
        case .summarised: return .indigo
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

            // Action buttons (Download Selected, Download New, Skip,
            // Unskip) used to live here. They migrated to the toolbar
            // below as part of the 2026-04-26 layout consolidation —
            // Skip joined Merge/Trim/Remove on the actions row, and
            // Download Selected sits on the select/filter row, since
            // "select rows → choose what → click Download Selected"
            // reads as a left-to-right verb on a single line. Unskip
            // remained available via the row's right-click context
            // menu (which it also was before).
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
    }
}
