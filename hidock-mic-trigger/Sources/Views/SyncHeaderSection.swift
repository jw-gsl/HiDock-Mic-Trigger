import SwiftUI

struct SyncHeaderSection: View {
    @ObservedObject var viewModel: HiDockViewModel

    private var statusColor: Color {
        // Mirrors StatusBadge.color — keep the two in sync.
        switch viewModel.syncStatusLevel {
        case .success: return .teal
        case .transcribed: return .green
        case .summarised: return .indigo
        case .info: return .blue
        case .merged: return .purple
        case .skipped: return .brown
        case .removed: return .pink
        case .warning: return .orange
        case .error: return .red
        case .secondary: return .secondary
        case .normal: return .primary
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

            // GitHub-style meeting-activity heatmap — one square per day over
            // the last year, intensity = meetings recorded that day, hover for
            // the day's stats. Shown once there are recordings to plot. Its
            // header now also hosts the refreshing/downloading status.
            if !viewModel.syncEntries.isEmpty {
                MeetingHeatmapView(
                    viewModel: viewModel,
                    ledMatrix: viewModel.ledMatrix,
                    ledSettings: viewModel.ledSettings
                )
                .padding(.top, 2)
            }

            // Status fallback ONLY when the heatmap is hidden (no recordings) —
            // otherwise the heatmap header carries the refreshing/downloading
            // status. Suppressed while the TranscriptionProgressBar is showing.
            if viewModel.syncEntries.isEmpty && !viewModel.transcriptionBusy
                && !viewModel.trimBusy && !viewModel.syncStatus.isEmpty {
                HStack(spacing: 6) {
                    Circle()
                        .fill(statusColor)
                        .frame(width: 8, height: 8)
                    Text(viewModel.syncStatus)
                        .font(.caption)
                        .foregroundColor(statusColor == .secondary ? .secondary : statusColor)
                    Spacer()
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
        .padding(.top, 10)
        // Tight bottom padding so the toolbar's action row sits directly under
        // the heatmap (no orphan gap now that the status row is gone).
        .padding(.bottom, 2)
    }
}
