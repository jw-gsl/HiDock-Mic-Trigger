import SwiftUI

struct SyncToolbarSection: View {
    @ObservedObject var viewModel: HiDockViewModel

    var body: some View {
        VStack(spacing: 6) {
            // Row: Pair/Unpair/Folders/Refresh on left, Transcribe on right
            HStack(spacing: 6) {
                Button {
                    viewModel.onPairDock()
                } label: {
                    Label("Pair", systemImage: "link.badge.plus")
                }
                .disabled(viewModel.syncBusy)

                Button {
                    viewModel.onUnpairDock()
                } label: {
                    Label("Unpair", systemImage: "minus.circle")
                }
                .disabled(viewModel.syncBusy || !viewModel.syncPaired)

                Button {
                    viewModel.onChooseRecordingsFolder()
                } label: {
                    Label("Recordings", systemImage: "folder")
                }

                Button {
                    viewModel.onChooseTranscriptFolder()
                } label: {
                    Label("Transcripts", systemImage: "doc.text")
                }

                Button {
                    viewModel.onRefreshSync()
                } label: {
                    Label("Refresh", systemImage: "arrow.clockwise")
                }
                .disabled(viewModel.syncBusy)

                Spacer()

                Button {
                    viewModel.onTranscribeSelected()
                } label: {
                    Label("Transcribe Selected", systemImage: "text.bubble")
                }
                .disabled(viewModel.transcriptionBusy || viewModel.syncDownloading || !viewModel.hasSelection)

                Button {
                    viewModel.onTranscribeAll()
                } label: {
                    Label("Transcribe All", systemImage: "text.bubble.fill")
                }
                .disabled(viewModel.transcriptionBusy)

                Toggle("Speaker Labels", isOn: Binding(
                    get: { viewModel.diarizeEnabled },
                    set: { _ in viewModel.onToggleDiarize() }
                ))
                .toggleStyle(.checkbox)

                if viewModel.needsTaggingCount > 0 {
                    Label("\(viewModel.needsTaggingCount) need tagging", systemImage: "tag.fill")
                        .font(.caption.weight(.medium))
                        .foregroundColor(.orange)
                }
            }
            .buttonStyle(.bordered)
            .controlSize(.small)

            // Selection & filter row
            HStack(spacing: 8) {
                Button("Select All") { viewModel.onSelectAll() }
                Button("Select None") { viewModel.onSelectNone() }
                Button("Select New") { viewModel.onSelectNotDownloaded() }

                Divider().frame(height: 16)

                Text("Filter:")
                    .font(.caption.weight(.medium))

                Button("All") {
                    viewModel.onFilterByDevice(nil)
                }
                .buttonStyle(.bordered)
                .tint(viewModel.syncFilterDeviceProductId == nil ? .accentColor : nil)

                ForEach(viewModel.syncPairedDevices, id: \.productId) { device in
                    Button(device.shortName) {
                        viewModel.onFilterByDevice(device.productId)
                    }
                    .buttonStyle(.bordered)
                    .tint(viewModel.syncFilterDeviceProductId == device.productId ? .accentColor : nil)
                }

                Spacer()

                Toggle("Hide Downloaded", isOn: Binding(
                    get: { viewModel.syncHideDownloaded },
                    set: { _ in viewModel.onToggleHideDownloaded() }
                ))
                .toggleStyle(.checkbox)

                Toggle("Auto-download", isOn: Binding(
                    get: { viewModel.syncAutoDownload },
                    set: { _ in viewModel.onToggleAutoDownload() }
                ))
                .toggleStyle(.checkbox)
            }
            .font(.caption)
            .buttonStyle(.bordered)
            .controlSize(.small)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 6)
    }
}
