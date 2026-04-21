import SwiftUI

/// Compact pipeline toolbar. Device-specific concerns (pair/unpair,
/// reachability, filter, reconnect, storage, recording state) now live
/// on the per-device cards above. Configuration-y controls (folder
/// pickers, Speaker Labels toggle) moved to the app's main menu where
/// they belong. This view only hosts pipeline *actions*.
struct SyncToolbarSection: View {
    @ObservedObject var viewModel: HiDockViewModel

    var body: some View {
        VStack(spacing: 6) {
            // Action row — left-aligned actions + spacer + right-aligned
            // status/indicators.
            HStack(spacing: 6) {
                Button {
                    viewModel.onRefreshSync()
                } label: {
                    Label("Refresh", systemImage: "arrow.clockwise")
                }
                .disabled(viewModel.syncBusy)

                Button {
                    viewModel.onImportAudioFile()
                } label: {
                    Label("Import", systemImage: "square.and.arrow.down")
                }
                .help("Import an audio or video file (mp3/wav/m4a/mp4/…) — copies into Recordings and adds it to the table")

                Divider().frame(height: 16)

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

                Divider().frame(height: 16)

                Button {
                    viewModel.onMergeSelected()
                } label: {
                    Label("Merge", systemImage: "arrow.triangle.merge")
                }
                .disabled(viewModel.syncBusy || viewModel.syncCheckedRecordings.count < 2)

                Button {
                    if let entry = viewModel.visibleEntries.first(where: {
                        viewModel.syncCheckedRecordings.contains($0.recording.name) && $0.recording.downloaded && $0.recording.localExists
                    }) {
                        viewModel.onTrimRecording(entry.recording.outputPath)
                    }
                } label: {
                    Label("Trim", systemImage: "scissors")
                }
                .disabled(viewModel.syncBusy || viewModel.syncCheckedRecordings.count != 1)

                Button {
                    viewModel.onRemoveSelected()
                } label: {
                    Label("Remove", systemImage: "trash")
                }
                .help("Remove imported files entirely, delete local copies of downloaded HiDock recordings. Device copies are preserved.")
                .disabled(viewModel.syncBusy || !viewModel.hasSelection)

                Spacer()

                if !viewModel.transcriptionQueue.isEmpty {
                    Button {
                        viewModel.onShowTranscriptionQueue()
                    } label: {
                        let queued = viewModel.transcriptionQueue.filter { $0.status == .queued }.count
                        let active = viewModel.transcriptionQueue.filter { $0.status == .transcribing }.count
                        Label(
                            active > 0 ? "Queue (\(active) + \(queued))" : "Queue (\(queued))",
                            systemImage: "list.bullet.rectangle"
                        )
                    }
                }

                if viewModel.needsTaggingCount > 0 {
                    Label("\(viewModel.needsTaggingCount) need tagging", systemImage: "tag.fill")
                        .font(.caption.weight(.medium))
                        .foregroundColor(.orange)
                }
            }
            .buttonStyle(.bordered)
            .controlSize(.small)

            // Selection + view preferences. Device filter chips have moved
            // onto the device cards in the header; filtering is now
            // "click a card's filter icon".
            HStack(spacing: 8) {
                Button("Select All") { viewModel.onSelectAll() }
                Button("Select None") { viewModel.onSelectNone() }
                Button("Select New") { viewModel.onSelectNotDownloaded() }

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

                Toggle("Auto-transcribe", isOn: Binding(
                    get: { viewModel.syncAutoTranscribe },
                    set: { _ in viewModel.onToggleAutoTranscribe() }
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
