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
            // Action row — everything you DO to selected rows, icon-only to save
            // space: Import · Merge/Trim/Skip/Remove · Download/Transcribe/
            // Summarise. Tooltips carry the names. Status counts sit on the right.
            HStack(spacing: 6) {
                Button {
                    viewModel.onImportAudioFile()
                } label: {
                    Label("Import", systemImage: "square.and.arrow.down")
                }
                .labelStyle(.iconOnly)
                .help("Import an audio or video file (mp3/wav/m4a/mp4/…) — copies into Recordings and adds it to the table")

                Divider().frame(height: 16)

                Button {
                    viewModel.onMergeSelected()
                } label: {
                    Label("Merge", systemImage: "arrow.triangle.merge")
                }
                .labelStyle(.iconOnly)
                .help("Merge the selected recordings into one")
                // Local-file op — don't gate on `syncBusy`. Only block during an
                // active download or in-flight trim.
                .disabled(viewModel.syncDownloading || viewModel.trimBusy || viewModel.syncCheckedRecordings.count < 2)

                Button {
                    if let entry = viewModel.visibleEntries.first(where: {
                        viewModel.syncCheckedRecordings.contains($0.recording.name) && $0.recording.localExists
                    }) {
                        viewModel.onTrimRecording(entry.recording.outputPath)
                    }
                } label: {
                    Label("Trim", systemImage: "scissors")
                }
                .labelStyle(.iconOnly)
                .help("Trim the selected recording")
                .disabled(viewModel.syncDownloading || viewModel.trimBusy || viewModel.syncCheckedRecordings.count != 1)

                Button {
                    viewModel.onMarkDownloaded()
                } label: {
                    Label("Skip", systemImage: "forward.fill")
                }
                .labelStyle(.iconOnly)
                .disabled(viewModel.syncBusy || !viewModel.hasSelection)
                .help("Skip — mark selected on-device recordings as 'don't download' so they drop out of download-new sweeps")

                Button {
                    viewModel.onRemoveSelected()
                } label: {
                    Label("Remove", systemImage: "trash")
                }
                .labelStyle(.iconOnly)
                .help("Remove imported files entirely / delete local copies of downloaded HiDock recordings. Device copies are preserved.")
                .disabled(viewModel.syncDownloading || viewModel.trimBusy || !viewModel.hasSelection)

                Divider().frame(height: 16)

                // Pipeline verbs on the selection — moved here from the filter row.
                Button {
                    viewModel.onDownloadSelected()
                } label: {
                    Label(viewModel.selectionIncludesTrimmed ? "Re-download Selected" : "Download Selected",
                          systemImage: "arrow.down.circle")
                }
                .labelStyle(.iconOnly)
                .disabled(viewModel.syncBusy || !viewModel.syncPaired || !viewModel.hasSelection)
                .help(viewModel.selectionIncludesTrimmed
                      ? "Re-download Selected — replaces the trimmed local file with the device original."
                      : "Download the selected recordings from the device")

                Button {
                    viewModel.onTranscribeSelected()
                } label: {
                    Label("Transcribe Selected", systemImage: "text.bubble")
                }
                .labelStyle(.iconOnly)
                .help("Transcribe the selected recordings")
                .disabled(viewModel.transcriptionBusy || viewModel.syncDownloading || !viewModel.hasSelection)

                Button {
                    viewModel.onSummariseSelected()
                } label: {
                    Label("Summarise Selected", systemImage: "sparkles")
                }
                .labelStyle(.iconOnly)
                .disabled(viewModel.syncDownloading || !viewModel.hasSelection)
                .help("Summarise (via Claude Code) each selected transcribed recording. Untranscribed selections are skipped.")

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

                // Status counts — responsive: truncate rather than push the row
                // wider than the window. (People filter moved to the filter row.)
                if viewModel.needsTaggingCount > 0 {
                    Label("\(viewModel.needsTaggingCount) to tag", systemImage: "tag.fill")
                        .font(.caption.weight(.medium))
                        .foregroundColor(.orange)
                        .fixedSize()
                        .help("\(viewModel.needsTaggingCount) transcribed recordings still need speaker tagging")
                }
                if !viewModel.syncSummary.isEmpty {
                    Text(viewModel.syncSummary)
                        .font(.caption)
                        .foregroundColor(.secondary)
                        .lineLimit(1)
                        .truncationMode(.tail)
                        .layoutPriority(-1)
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
                    // "All" clears the multi-select set (no filter).
                    Button {
                        viewModel.statusFilters = []
                    } label: {
                        HStack {
                            Image(systemName: viewModel.statusFilters.isEmpty
                                  ? "checkmark.circle.fill" : "circle")
                            Text("All")
                        }
                    }
                    Divider()
                    // Multi-select statuses — tick to stack (OR). Checkmark
                    // shows what's active without closing the menu.
                    ForEach(SyncStatusFilter.selectable) { f in
                        Button {
                            viewModel.toggleStatusFilter(f)
                        } label: {
                            HStack {
                                Image(systemName: viewModel.statusFilters.contains(f)
                                      ? "checkmark.square.fill" : "square")
                                Text(f.label)
                            }
                        }
                    }
                } label: {
                    let n = viewModel.statusFilters.subtracting([.all]).count
                    Label(
                        n == 0 ? "Filter" : "Filter (\(n))",
                        systemImage: "line.3.horizontal.decrease.circle"
                    )
                }
                .menuStyle(.borderlessButton)
                .fixedSize()
                .help("Show recordings matching any of the selected stages (stackable). 'All' clears the filter. Combines with the device filter on the cards above.")

                // "Hide" — multiselect menu (sibling of Filter). Hides the
                // user-actioned terminal states (Skipped / Removed) so they
                // stop cluttering the table. Picking one in Filter overrides
                // its hide.
                Menu {
                    ForEach(HiDockViewModel.hideableStatuses, id: \.self) { s in
                        Button {
                            viewModel.toggleHidden(s)
                        } label: {
                            HStack {
                                Image(systemName: viewModel.hiddenStatuses.contains(s)
                                      ? "checkmark.square.fill" : "square")
                                // Show how many recordings carry this status so
                                // the user can see what hiding it removes.
                                Text("\(s) (\(viewModel.statusCount(s)))")
                            }
                        }
                    }
                } label: {
                    let count = HiDockViewModel.hideableStatuses
                        .filter { viewModel.hiddenStatuses.contains($0) }.count
                    Label {
                        // The layout-participating view is ALWAYS the widest
                        // state ("Hidden (2)") so the menu measures a constant
                        // width and the dropdown arrow / toolbar row never
                        // shifts. The actual label is drawn as a leading
                        // overlay on top (overlays don't affect layout).
                        Text("Hidden (\(HiDockViewModel.hideableStatuses.count))")
                            .hidden()
                            .overlay(alignment: .leading) {
                                Text(count == 0 ? "Hide" : "Hidden (\(count))")
                                    .fixedSize()
                            }
                    } icon: {
                        Image(systemName: "eye.slash")
                    }
                }
                .menuStyle(.borderlessButton)
                .fixedSize()
                .help("Hide rows you've already actioned — Skipped (won't download) and Removed (local copy deleted). Multiselect; picking a status in Filter overrides hiding it.")

                // Summary-type filter — only shown once something has been
                // summarised. Lets the user narrow to one classification
                // (e.g. just "Brainstorming"). AND-ed with the Filter above.
                if !viewModel.summaryTypeOptions.isEmpty {
                    Menu {
                        Button {
                            viewModel.summaryTypeFilter = nil
                        } label: {
                            HStack {
                                Image(systemName: viewModel.summaryTypeFilter == nil
                                      ? "checkmark.circle.fill" : "circle")
                                Text("All types")
                            }
                        }
                        Divider()
                        ForEach(viewModel.summaryTypeOptions, id: \.self) { type in
                            Button {
                                viewModel.summaryTypeFilter = type
                            } label: {
                                HStack {
                                    Image(systemName: viewModel.summaryTypeFilter == type
                                          ? "checkmark.circle.fill" : "circle")
                                    Text(type)
                                }
                            }
                        }
                    } label: {
                        Label(
                            viewModel.summaryTypeFilter == nil
                                ? "Type"
                                : "Type: \(viewModel.summaryTypeFilter!)",
                            systemImage: "tag"
                        )
                    }
                    .menuStyle(.borderlessButton)
                    .fixedSize()
                    .help("Show only recordings whose summary was classified as this type.")
                }

                // People filter — sits with the other narrowing controls.
                if !viewModel.allPeople.isEmpty {
                    peopleFilterMenu
                }

                Spacer()

                // Auto-download / transcribe / summarise — collapsed into one
                // dropdown to save space.
                autoMenu
            }
            .font(.caption)
            .buttonStyle(.bordered)
            .controlSize(.small)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 6)
    }

    /// People filter — multi-select with an Any/All mode. Filters the list to
    /// meetings containing the selected people.
    /// Auto-download / transcribe / summarise as checkable menu items — one
    /// compact dropdown instead of three inline checkboxes.
    private var autoMenu: some View {
        let onCount = [viewModel.syncAutoDownload, viewModel.syncAutoTranscribe, viewModel.syncAutoSummarise]
            .filter { $0 }.count
        return Menu {
            Toggle("Auto-download", isOn: Binding(
                get: { viewModel.syncAutoDownload }, set: { _ in viewModel.onToggleAutoDownload() }))
            Toggle("Auto-transcribe", isOn: Binding(
                get: { viewModel.syncAutoTranscribe }, set: { _ in viewModel.onToggleAutoTranscribe() }))
            Toggle("Auto-summarise", isOn: Binding(
                get: { viewModel.syncAutoSummarise }, set: { _ in viewModel.onToggleAutoSummarise() }))
        } label: {
            Label(onCount > 0 ? "Auto (\(onCount))" : "Auto", systemImage: "bolt.horizontal.circle")
        }
        .menuStyle(.borderlessButton)
        .fixedSize()
        .foregroundColor(onCount > 0 ? .accentColor : .secondary)
        .help("Automatically download / transcribe / summarise new recordings as they arrive.")
    }

    private var peopleFilterMenu: some View {
        let selected = viewModel.syncFilterPeople
        return Menu {
            Picker("Match", selection: Binding(
                get: { viewModel.syncPeopleFilterMode },
                set: { viewModel.syncPeopleFilterMode = $0 }
            )) {
                Text("Any of these people").tag(PeopleFilterMode.any)
                Text("All of these people").tag(PeopleFilterMode.all)
            }
            Divider()
            ForEach(viewModel.allPeople, id: \.self) { person in
                let count = viewModel.personMeetingCounts[person] ?? 0
                Button {
                    if selected.contains(person) { viewModel.syncFilterPeople.remove(person) }
                    else { viewModel.syncFilterPeople.insert(person) }
                } label: {
                    Label("\(person)  (\(count))",
                          systemImage: selected.contains(person) ? "checkmark.circle.fill" : "circle")
                }
            }
            if !selected.isEmpty {
                Divider()
                Button("Clear people filter", role: .destructive) {
                    viewModel.syncFilterPeople = []
                }
            }
        } label: {
            Label(selected.isEmpty ? "People" : "People (\(selected.count))",
                  systemImage: "person.crop.circle")
        }
        .menuStyle(.borderlessButton)
        .fixedSize()
        .foregroundColor(selected.isEmpty ? .secondary : .accentColor)
        .help("Filter the list to meetings that include the people you pick (Any or All).")
    }
}
