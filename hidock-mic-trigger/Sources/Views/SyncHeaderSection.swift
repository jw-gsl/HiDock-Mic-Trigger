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
        VStack(alignment: .leading, spacing: 4) {
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

            // Folder paths on same line
            HStack(spacing: 16) {
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
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .background(isConnected ? Color.green.opacity(0.04) : Color.clear)
    }
}
