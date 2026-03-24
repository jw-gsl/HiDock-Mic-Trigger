import SwiftUI

struct MainWindowView: View {
    @ObservedObject var viewModel: HiDockViewModel

    var body: some View {
        VStack(spacing: 4) {
            MicTriggerSection(viewModel: viewModel)
            SyncHeaderSection(viewModel: viewModel)
            SyncToolbarSection(viewModel: viewModel)
            DownloadProgressBar(viewModel: viewModel)
            RecordingsTableView(viewModel: viewModel)

            // Footer
            HStack {
                Spacer()
                Button {
                    viewModel.onShowFeedbackHistory()
                } label: {
                    Label("My Feedback", systemImage: "list.bullet.clipboard")
                        .font(.caption)
                }
                .buttonStyle(.bordered)
                .controlSize(.small)

                Button {
                    viewModel.onSendFeedback()
                } label: {
                    Label("Send Feedback", systemImage: "bubble.left.and.text.bubble.right")
                        .font(.caption)
                }
                .buttonStyle(.bordered)
                .controlSize(.small)
            }
            .padding(.horizontal, 16)
            .padding(.bottom, 8)
            .padding(.top, 2)
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
