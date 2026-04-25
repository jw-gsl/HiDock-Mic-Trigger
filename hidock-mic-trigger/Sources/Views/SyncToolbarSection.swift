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
                // Local-file op — don't gate on `syncBusy` (which is set
                // whenever any device is probing). Only block during an
                // active download (file being written) or in-flight trim.
                .disabled(viewModel.syncDownloading || viewModel.trimBusy || viewModel.syncCheckedRecordings.count < 2)

                Button {
                    if let entry = viewModel.visibleEntries.first(where: {
                        viewModel.syncCheckedRecordings.contains($0.recording.name) && $0.recording.downloaded && $0.recording.localExists
                    }) {
                        viewModel.onTrimRecording(entry.recording.outputPath)
                    }
                } label: {
                    Label("Trim", systemImage: "scissors")
                }
                .disabled(viewModel.syncDownloading || viewModel.trimBusy || viewModel.syncCheckedRecordings.count != 1)

                Button {
                    viewModel.onRemoveSelected()
                } label: {
                    Label("Remove", systemImage: "trash")
                }
                .help("Remove imported files entirely, delete local copies of downloaded HiDock recordings. Device copies are preserved.")
                .disabled(viewModel.syncDownloading || viewModel.trimBusy || !viewModel.hasSelection)

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

                // Merge candidates indicator — passive count + tooltip,
                // not a button. The actual Merge / Dismiss actions live
                // in each candidate row's right-click context menu;
                // popping a separate window or sheet for a "system
                // suggestion" felt heavier than the affordance warrants.
                // The blue left-border on candidate rows is the primary
                // visual cue; this label just confirms the system is
                // paying attention.
                if viewModel.mergeCandidateCountForBadge > 0 {
                    Label(
                        "\(viewModel.mergeCandidateCountForBadge) merge suggestion\(viewModel.mergeCandidateCountForBadge == 1 ? "" : "s")",
                        systemImage: "arrow.triangle.merge"
                    )
                    .font(.caption.weight(.medium))
                    .foregroundColor(.blue)
                    .help("Right-click any blue-bordered row to Merge or Dismiss the suggestion.")
                }

                if viewModel.needsTaggingCount > 0 {
                    Label("\(viewModel.needsTaggingCount) need tagging", systemImage: "tag.fill")
                        .font(.caption.weight(.medium))
                        .foregroundColor(.orange)
                }
            }
            .buttonStyle(.bordered)
            .controlSize(.small)

            // Selection + filters. Three loose buttons here used to
            // grow each time a new "Select X" was added; consolidated
            // into a single Menu so adding "Select Failed" / "Select
            // Untranscribed" later is one line. Status filter sits
            // alongside it so selection and filter share a row and
            // the user can see what they're filtering AND selecting in
            // one glance. Device filter chips already live on the
            // device cards above and AND with this one.
            HStack(spacing: 8) {
                Menu {
                    Button("All")            { viewModel.onSelectAll() }
                    Button("None")           { viewModel.onSelectNone() }
                    Divider()
                    Button("New (on device, not downloaded)") {
                        viewModel.onSelectNotDownloaded()
                    }
                } label: {
                    Label("Select", systemImage: "checkmark.circle")
                }
                .menuStyle(.borderlessButton)
                .fixedSize()

                Picker("Filter", selection: Binding(
                    get: { viewModel.syncStatusFilter },
                    set: { viewModel.syncStatusFilter = $0 }
                )) {
                    ForEach(SyncStatusFilter.allCases) { f in
                        Text(f.label).tag(f)
                    }
                }
                .pickerStyle(.menu)
                .fixedSize()
                .help("Show only recordings at this pipeline stage. Combines with the device filter on the cards above.")

                Spacer()

                Toggle("Hide Downloaded", isOn: Binding(
                    get: { viewModel.syncHideDownloaded },
                    set: { _ in viewModel.onToggleHideDownloaded() }
                ))
                .toggleStyle(.checkbox)
                .help("Hide rows already pulled off the HiDock. Imported files stay visible.")

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
