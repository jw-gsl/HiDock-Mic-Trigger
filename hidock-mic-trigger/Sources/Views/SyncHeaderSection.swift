import SwiftUI

struct SyncHeaderSection: View {
    @ObservedObject var viewModel: HiDockViewModel

    private var statusColor: Color {
        switch viewModel.syncStatusLevel {
        case .success: return .green
        case .warning: return .orange
        case .error: return .red
        case .info: return .blue
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

            // Generic pipeline-status line: transcription progress, skip
            // confirmations, auto-flow messages. Per-device connection
            // state lives on the cards above, so this row is hidden when
            // there's no pipeline message to surface — prevents the
            // redundant "Connected — 🔊 P1" line showing the same thing
            // as the cards.
            if !viewModel.syncStatus.isEmpty || !viewModel.syncSummary.isEmpty {
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
                    Label("Download Selected", systemImage: "arrow.down.circle")
                }
                .disabled(viewModel.syncBusy || !viewModel.syncPaired || !viewModel.hasSelection)

                Button {
                    viewModel.onDownloadNew()
                } label: {
                    Label("Download New", systemImage: "arrow.down.to.line")
                }
                .disabled(viewModel.syncBusy || !viewModel.syncPaired)

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
