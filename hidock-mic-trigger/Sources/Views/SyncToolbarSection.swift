import SwiftUI

/// Compact pipeline toolbar. Device-specific concerns (pair/unpair,
/// reachability, filter, reconnect, storage, recording state) now live
/// on the per-device cards above. Configuration-y controls (folder
/// pickers, Speaker Labels toggle) moved to the app's main menu where
/// they belong. This view only hosts pipeline *actions*.
struct SyncToolbarSection: View {
    @ObservedObject var viewModel: HiDockViewModel
    @State private var splitTarget: HiDockSyncRecordingEntry?
    // Minutes and seconds are separate fields rather than one "MM:SS" string.
    // The free-text version made the user type the colon, and silently accepted
    // anything that did not parse into one.
    @State private var splitMinutes = ""
    @State private var splitSeconds = ""
    @State private var splitError = ""

    var body: some View {
        VStack(spacing: 6) {
            // Action row — every icon carries a short visible name, with the
            // full explanation still available as its tooltip.
            HStack(spacing: 6) {
                Button {
                    viewModel.onImportAudioFile()
                } label: {
                    Label("Import", systemImage: "square.and.arrow.down")
                }
                .labelStyle(ToolbarActionLabelStyle())
                .help("Import an audio or video file (mp3/wav/m4a/mp4/…) — copies into Recordings and adds it to the table")

                Divider().frame(height: 16)

                Button {
                    viewModel.onMergeSelected()
                } label: {
                    Label("Merge", systemImage: "arrow.triangle.merge")
                }
                .labelStyle(ToolbarActionLabelStyle())
                .help("Merge the selected recordings into one. An existing merged file can be "
                      + "one of the selections — it is rebuilt from its original recordings plus "
                      + "whatever you add, so the audio is only ever encoded once.")
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
                .labelStyle(ToolbarActionLabelStyle())
                .help("Trim the selected recording")
                .disabled(viewModel.syncDownloading || viewModel.trimBusy || viewModel.syncCheckedRecordings.count != 1)

                Button {
                    if let entry = viewModel.visibleEntries.first(where: {
                        viewModel.syncCheckedRecordings.contains($0.recording.name) && $0.recording.localExists
                    }) {
                        splitTarget = entry
                        // Default to the midpoint: it is the most common intent
                        // and makes the two fields self-explanatory on sight.
                        let midpoint = Int(entry.recording.duration / 2)
                        splitMinutes = String(midpoint / 60)
                        splitSeconds = String(format: "%02d", midpoint % 60)
                        splitError = ""
                    }
                } label: {
                    Label("Split", systemImage: "divide")
                }
                .labelStyle(ToolbarActionLabelStyle())
                .help("Split the selected recording into two copies, preserving transcripts")
                .disabled(viewModel.syncDownloading || viewModel.trimBusy || viewModel.syncCheckedRecordings.count != 1)
                .popover(item: $splitTarget, arrowEdge: .bottom) { entry in
                    splitPopover(for: entry)
                }

                Button {
                    viewModel.onMarkDownloaded()
                } label: {
                    Label("Skip", systemImage: "forward.fill")
                }
                .labelStyle(ToolbarActionLabelStyle())
                .disabled(viewModel.syncBusy || !viewModel.hasSelection)
                .help("Skip — mark selected on-device recordings as 'don't download' so they drop out of download-new sweeps")

                Button {
                    viewModel.onRemoveSelected()
                } label: {
                    Label("Remove", systemImage: "trash")
                }
                .labelStyle(ToolbarActionLabelStyle())
                .help("Remove imported files entirely / delete local copies of downloaded HiDock recordings. Device copies are preserved.")
                .disabled(viewModel.syncDownloading || viewModel.trimBusy || !viewModel.hasSelection)

                Divider().frame(height: 16)

                // Pipeline verbs on the selection — moved here from the filter row.
                Button {
                    viewModel.onDownloadSelected()
                } label: {
                    Label(viewModel.selectionIncludesTrimmed ? "Re-download" : "Download",
                          systemImage: "arrow.down.circle")
                }
                .labelStyle(ToolbarActionLabelStyle())
                .disabled(viewModel.syncBusy || !viewModel.syncPaired || !viewModel.hasSelection)
                .help(viewModel.selectionIncludesTrimmed
                      ? "Re-download Selected — replaces the trimmed local file with the device original."
                      : "Download the selected recordings from the device")

                Button {
                    viewModel.onTranscribeSelected()
                } label: {
                    Label("Transcribe", systemImage: "text.bubble")
                }
                .labelStyle(ToolbarActionLabelStyle())
                .help("Transcribe the selected recordings")
                .disabled(viewModel.transcriptionBusy || viewModel.syncDownloading || !viewModel.hasSelection)

                Button {
                    viewModel.onSummariseSelected()
                } label: {
                    Label("Summarise", systemImage: "sparkles")
                }
                .labelStyle(ToolbarActionLabelStyle())
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
                // "Hide" is a persistent visibility preference, separate from
                // the temporary filters cleared by Clear filters. Keep it at
                // the far left so the table's hidden-row policy is visible
                // before the selection and narrowing controls.
                hiddenStatusesMenu

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

                    Button {
                        viewModel.clearAllRecordingFilters()
                    } label: {
                        Label("Clear filters", systemImage: "xmark.circle")
                    }
                    .buttonStyle(.borderless)
                    .fixedSize()
                    .foregroundColor(viewModel.hasActiveRecordingFilters ? .accentColor : .secondary)
                    .disabled(!viewModel.hasActiveRecordingFilters)
                    .help("Clear people, device, status, summary-type, and day filters. Hidden-status choices are kept.")
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

    /// Persistent visibility menu for terminal states. This is intentionally
    /// separate from the temporary filters so Clear filters does not undo it.
    private var hiddenStatusesMenu: some View {
        Menu {
            ForEach(HiDockViewModel.hideableStatuses, id: \.self) { s in
                Button {
                    viewModel.toggleHidden(s)
                } label: {
                    HStack {
                        Image(systemName: viewModel.hiddenStatuses.contains(s)
                              ? "checkmark.square.fill" : "square")
                        // Show how many recordings carry this status so the
                        // user can see what hiding it removes.
                        Text("\(s) (\(viewModel.statusCount(s)))")
                    }
                }
            }
        } label: {
            let count = HiDockViewModel.hideableStatuses
                .filter { viewModel.hiddenStatuses.contains($0) }.count
            Label {
                // Keep the menu's measured width stable as the count changes.
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
            // Clear first so it's always one click away even with a long people list.
            if !selected.isEmpty {
                Button("Clear people filter", role: .destructive) {
                    viewModel.syncFilterPeople = []
                }
                Divider()
            }
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
        } label: {
            Label(selected.isEmpty ? "People" : "People (\(selected.count))",
                  systemImage: "person.crop.circle")
        }
        .menuStyle(.borderlessButton)
        .fixedSize()
        .foregroundColor(selected.isEmpty ? .secondary : .accentColor)
        .help("Filter the list to meetings that include the people you pick (Any or All).")
    }

    private func splitPopover(for entry: HiDockSyncRecordingEntry) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Split recording").font(.headline)
            Text("Where should the second meeting start?")
                .font(.caption).foregroundColor(.secondary)
            HStack(spacing: 4) {
                splitField(value: $splitMinutes, unit: "min", range: 0...(Int(entry.recording.duration) / 60))
                Text(":").font(.body.monospacedDigit()).foregroundColor(.secondary)
                splitField(value: $splitSeconds, unit: "sec", range: 0...59)
                Spacer()
                Text("of \(formatRecordingDuration(entry.recording.duration))")
                    .font(.caption).foregroundColor(.secondary)
            }
            if !splitError.isEmpty { Text(splitError).font(.caption).foregroundColor(.red) }
            HStack {
                Button("Cancel") { splitTarget = nil }
                Spacer()
                Button("Split") { submitSplit(entry) }
                    .keyboardShortcut(.defaultAction)
            }
        }
        .padding()
        .frame(width: 280)
    }

    /// One unit of the split time: a typed field with a stepper beside it, so
    /// the value can be nudged as well as typed and the unit is never in doubt.
    private func splitField(value: Binding<String>, unit: String, range: ClosedRange<Int>) -> some View {
        VStack(alignment: .leading, spacing: 1) {
            HStack(spacing: 2) {
                TextField("0", text: value)
                    .textFieldStyle(.roundedBorder)
                    .frame(width: 46)
                    .multilineTextAlignment(.trailing)
                Stepper("") {
                    let next = min(range.upperBound, (Int(value.wrappedValue) ?? 0) + 1)
                    value.wrappedValue = unit == "sec" ? String(format: "%02d", next) : String(next)
                    splitError = ""
                } onDecrement: {
                    let next = max(range.lowerBound, (Int(value.wrappedValue) ?? 0) - 1)
                    value.wrappedValue = unit == "sec" ? String(format: "%02d", next) : String(next)
                    splitError = ""
                }
                .labelsHidden()
            }
            Text(unit).font(.caption2).foregroundColor(.secondary)
        }
    }

    private func submitSplit(_ entry: HiDockSyncRecordingEntry) {
        let minutes = Int(splitMinutes.trimmingCharacters(in: .whitespaces))
        let secs = Int(splitSeconds.trimmingCharacters(in: .whitespaces))
        guard let minutes, let secs, minutes >= 0, secs >= 0, secs < 60 else {
            splitError = "Enter whole numbers — seconds must be under 60."
            return
        }
        let seconds = Double(minutes * 60 + secs)
        guard seconds > 0, seconds < entry.recording.duration else {
            splitError = "Use a time between 0:01 and \(formatRecordingDuration(entry.recording.duration))"
            return
        }
        splitTarget = nil
        viewModel.onSplitRecording(entry.recording.outputPath, seconds)
    }
}

/// Keeps the action icons recognisable while making their meaning visible
/// without requiring hover.  The full sentence remains in each `.help`.
private struct ToolbarActionLabelStyle: LabelStyle {
    func makeBody(configuration: Configuration) -> some View {
        VStack(spacing: 1) {
            configuration.icon
            configuration.title
                .font(.system(size: 9))
                .lineLimit(1)
        }
        .frame(minWidth: 38)
    }
}
