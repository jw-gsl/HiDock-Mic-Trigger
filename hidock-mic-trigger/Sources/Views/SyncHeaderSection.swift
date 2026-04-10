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
        VStack(alignment: .leading, spacing: 6) {
            // Status row
            HStack {
                HStack(spacing: 6) {
                    Circle()
                        .fill(statusColor)
                        .frame(width: 8, height: 8)
                    Text(viewModel.syncStatus)
                        .font(.body.weight(.medium))
                }
                Spacer()
                Text(viewModel.syncSummary)
                    .font(.caption)
                    .foregroundColor(.secondary)
            }

            // Folder paths + download buttons on same row
            HStack(spacing: 12) {
                if let folder = viewModel.syncOutputFolder {
                    Label {
                        Text(folder)
                            .lineLimit(1)
                            .truncationMode(.middle)
                    } icon: {
                        Image(systemName: "folder")
                    }
                    .font(.caption)
                    .foregroundColor(.secondary)
                }

                if let folder = viewModel.syncTranscriptFolder {
                    Label {
                        Text(folder)
                            .lineLimit(1)
                            .truncationMode(.middle)
                    } icon: {
                        Image(systemName: "doc.text")
                    }
                    .font(.caption)
                    .foregroundColor(.secondary)
                }

                Spacer()

                // Download buttons on same row as paths
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
                }
                .buttonStyle(.bordered)
                .controlSize(.small)
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .background(isConnected ? Color.green.opacity(0.04) : Color.clear)
    }
}
