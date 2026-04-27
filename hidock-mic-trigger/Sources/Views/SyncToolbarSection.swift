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
            // Action row 1 — Import + transformative selection-driven
            // actions (Merge, Trim, Skip, Remove). Transcribe Selected
            // sits on row 2 next to Download Selected (both are
            // "process selected rows" verbs and pair visually).
            HStack(spacing: 6) {
                Button {
                    viewModel.onImportAudioFile()
                } label: {
                    Label("Import", systemImage: "square.and.arrow.down")
                }
                .help("Import an audio or video file (mp3/wav/m4a/mp4/…) — copies into Recordings and adds it to the table")

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
                        // localExists, not `downloaded && localExists`:
                        // a row can have the file on disk but
                        // `downloaded=false` if the device-reported
                        // length and the actual byte count differ
                        // slightly. The flag is for download-decisioning;
                        // file ops should ask the filesystem.
                        viewModel.syncCheckedRecordings.contains($0.recording.name) && $0.recording.localExists
                    }) {
                        viewModel.onTrimRecording(entry.recording.outputPath)
                    }
                } label: {
                    Label("Trim", systemImage: "scissors")
                }
                .disabled(viewModel.syncDownloading || viewModel.trimBusy || viewModel.syncCheckedRecordings.count != 1)

                // Skip: mark selected device-side recordings as
                // "downloaded" without pulling bytes — hides them from
                // future download-new sweeps. Mirrors the action that
                // used to live in SyncHeaderSection.
                Button {
                    viewModel.onMarkDownloaded()
                } label: {
                    Label("Skip", systemImage: "forward.fill")
                }
                .disabled(viewModel.syncBusy || !viewModel.hasSelection)
                .help("Mark selected on-device recordings as 'don't download' — they'll stop appearing in download-new sweeps")

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

                // Merge candidates / merge-selected toolbar slot. Three
                // possible states:
                //   1. Ticks selected (>=2) → primary blue button:
                //      "Merge N selected" fires the merge.
                //   2. Suggestions exist, no ticks → clickable label
                //      that scrolls the table to the first candidate
                //      row so the user can find what was flagged.
                //   3. Nothing flagged → slot hidden.
                if viewModel.canMergeTickedCandidates {
                    Button {
                        viewModel.onMergeTickedCandidates()
                    } label: {
                        Label(
                            "Merge \(viewModel.mergeCandidatesTicked.count) selected",
                            systemImage: "arrow.triangle.merge"
                        )
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(.blue)
                    .help("Combine the ticked candidate rows into one merged recording. Re-runs diarization but reuses the existing per-piece transcripts.")
                } else if viewModel.mergeCandidateCountForBadge > 0 {
                    Button {
                        viewModel.scrollToFirstCandidateTrigger += 1
                    } label: {
                        Label(
                            "\(viewModel.mergeCandidateCountForBadge) merge suggestion\(viewModel.mergeCandidateCountForBadge == 1 ? "" : "s")",
                            systemImage: "arrow.triangle.merge"
                        )
                    }
                    .buttonStyle(.plain)
                    .foregroundColor(.blue)
                    .help("Click to jump to the first suggested row. Tick the 'Potential merge' box on each row you want to combine, then click 'Merge N selected'.")
                }

                if viewModel.needsTaggingCount > 0 {
                    Label("\(viewModel.needsTaggingCount) need tagging", systemImage: "tag.fill")
                        .font(.caption.weight(.medium))
                        .foregroundColor(.orange)
                }
            }
            .buttonStyle(.bordered)
            .controlSize(.small)

            // Action row 2 — narrowing the table (Select, Filter) +
            // the action that operates on the narrowed selection
            // (Download Selected). Auto-* toggles on the right.
            //
            // Filter is a Menu (not a Picker) so it matches Select's
            // shape — having two visually-different dropdowns next to
            // each other was the inconsistency James called out. Hide
            // Downloaded was removed: the Filter menu can already do
            // "On device" / "Untranscribed" / etc., which is the
            // strictly more general control.
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

                Menu {
                    ForEach(SyncStatusFilter.allCases) { f in
                        Button {
                            viewModel.syncStatusFilter = f
                        } label: {
                            // Tick the currently-active filter so the
                            // user can see what's selected without
                            // closing the menu first.
                            HStack {
                                Image(systemName: viewModel.syncStatusFilter == f
                                      ? "checkmark.circle.fill" : "circle")
                                Text(f.label)
                            }
                        }
                    }
                } label: {
                    Label(
                        viewModel.syncStatusFilter == .all
                            ? "Filter"
                            : "Filter: \(viewModel.syncStatusFilter.label)",
                        systemImage: "line.3.horizontal.decrease.circle"
                    )
                }
                .menuStyle(.borderlessButton)
                .fixedSize()
                .help("Show only recordings at this pipeline stage. Combines with the device filter on the cards above.")

                // "Process selected rows" verbs cluster here: Download
                // Selected and Transcribe Selected sit side-by-side
                // because they're the two things the user does to
                // narrowed-down rows. Download Selected re-labels to
                // "Re-download Selected" when any selected row is
                // locally trimmed, so the user sees the click will
                // overwrite the trimmed copy with the device original.
                Button {
                    viewModel.onDownloadSelected()
                } label: {
                    let label = viewModel.selectionIncludesTrimmed
                        ? "Re-download Selected"
                        : "Download Selected"
                    Label(label, systemImage: "arrow.down.circle")
                }
                .disabled(viewModel.syncBusy || !viewModel.syncPaired || !viewModel.hasSelection)
                .help(viewModel.selectionIncludesTrimmed
                      ? "At least one selected recording is trimmed — re-downloading will replace the trimmed local file with the device's original."
                      : "Download the selected recordings from the device")

                Button {
                    viewModel.onTranscribeSelected()
                } label: {
                    Label("Transcribe Selected", systemImage: "text.bubble")
                }
                .disabled(viewModel.transcriptionBusy || viewModel.syncDownloading || !viewModel.hasSelection)

                // (Download New removed in 2026-04-26 cleanup — it was
                // dead UI in every realistic state. Auto-download
                // covers the "I want the new ones" case when on; when
                // off, the renderSyncStatus auto-fire on file-count
                // rise + the manual Refresh + Select New + Download
                // Selected path together cover the rest. The
                // workhorse `downloadNewSyncRecordings` stays in
                // AppDelegate because the auto-download trigger calls
                // it directly.)

                Spacer()

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
