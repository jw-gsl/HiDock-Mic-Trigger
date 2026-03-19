import SwiftUI

struct MainWindowView: View {
    @ObservedObject var viewModel: HiDockViewModel

    var body: some View {
        VStack(spacing: 0) {
            MicTriggerSection(viewModel: viewModel)
            Divider()
            SyncHeaderSection(viewModel: viewModel)
            SyncToolbarSection(viewModel: viewModel)
            DownloadProgressBar(viewModel: viewModel)
            RecordingsTableView(viewModel: viewModel)
        }
        .frame(minWidth: 980, minHeight: 510)
    }
}

struct DownloadProgressBar: View {
    @ObservedObject var viewModel: HiDockViewModel

    var body: some View {
        if viewModel.syncDownloading {
            HStack(spacing: 8) {
                ProgressView()
                    .controlSize(.small)
                Text(viewModel.syncDownloadProgress ?? "Downloading...")
                    .font(.caption)
                    .foregroundColor(.secondary)
                Spacer()
                Button(role: .destructive) {
                    viewModel.onStopDownload()
                } label: {
                    Label("Stop", systemImage: "stop.fill")
                        .font(.caption)
                }
                .buttonStyle(.bordered)
                .tint(.red)
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 6)
            .background(.ultraThinMaterial)
        }
    }
}
