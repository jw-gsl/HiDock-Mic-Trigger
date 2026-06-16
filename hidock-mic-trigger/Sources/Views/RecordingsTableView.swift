import SwiftUI

struct RecordingsTableView: View {
    @ObservedObject var viewModel: HiDockViewModel
    /// Track whether we've programmatically scrolled to the top for the
    /// first non-empty row set. Without this, SwiftUI's List keeps a
    /// stale scroll anchor from a prior render (common when the initial
    /// paint-from-cache populate replaces entries with the live-probe
    /// result a second later), leaving the user on row ~5 of 284 instead
    /// of row 1. One-shot anchor — after the first jump we let the user
    /// scroll freely.
    @State private var didScrollToTop = false

    var body: some View {
        VStack(spacing: 0) {
            // Column headers
            HStack(spacing: 0) {
                Text("").frame(width: 36) // checkbox
                headerButton("Device", key: "device", width: 120)
                headerButton("Status", key: "status", width: 110)
                // Renamed from "Transcribed" — the icons in this column
                // actually communicate speaker-tagging state (tagged ✓,
                // needs tagging ⚠), not whether the file is transcribed.
                // Transcribed is now part of the main Status cascade:
                // On device → Downloaded → Transcribed.
                headerButton("Tagged", key: nil, width: 90)
                // Summary column — mirrors Tagged: a tick that opens the
                // generated summary. No sort key (summary state isn't a
                // sortable scalar the way name/date are).
                headerButton("Summary", key: nil, width: 80)
                headerButton("Recording", key: "name", width: 220)
                headerButton("Created", key: "created", width: 155)
                headerButton("Length", key: "duration", width: 70)
                headerButton("Size", key: "size", width: 70)
                Text("").frame(width: 50) // actions
                Spacer(minLength: 0)
            }
            .font(.caption.weight(.medium))
            .foregroundColor(.secondary)
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .background(Color(nsColor: .controlBackgroundColor))

            Divider()

            // Rows
            ScrollViewReader { proxy in
                List(viewModel.displayRows) { row in
                    switch row {
                    case .recording(let entry):
                        recordingRow(entry: entry, indented: false)
                            .contextMenu { entryContextMenu(entry: entry) }
                            .id(row.id)
                    case .mergeParent(let group):
                        mergeParentRow(group: group)
                            .id(row.id)
                    case .mergeChild(let entry):
                        recordingRow(entry: entry, indented: true)
                            .contextMenu { entryContextMenu(entry: entry) }
                            .id(row.id)
                    }
                }
                .listStyle(.plain)
                .onChange(of: viewModel.displayRows.count) { newCount in
                    guard !didScrollToTop, newCount > 0,
                          let firstId = viewModel.displayRows.first?.id else { return }
                    didScrollToTop = true
                    // Run on next tick so SwiftUI has laid out the rows
                    // before we ask it to scroll.
                    DispatchQueue.main.async {
                        withAnimation(.none) {
                            proxy.scrollTo(firstId, anchor: .top)
                        }
                    }
                }
                // User clicked the "N merge suggestions" toolbar label —
                // jump to the first candidate row so they don't have
                // to hunt for it. Watching a counter lets repeat clicks
                // work even when the row is already on-screen (and
                // makes the gesture feel like a "find it again" affordance).
                .onChange(of: viewModel.scrollToFirstCandidateTrigger) { _ in
                    guard let path = viewModel.firstMergeCandidatePath else { return }
                    guard let target = viewModel.displayRows.first(where: { row in
                        if case .recording(let e) = row, e.recording.outputPath == path { return true }
                        if case .mergeChild(let e) = row, e.recording.outputPath == path { return true }
                        return false
                    }) else { return }
                    DispatchQueue.main.async {
                        withAnimation(.easeInOut(duration: 0.25)) {
                            proxy.scrollTo(target.id, anchor: .center)
                        }
                    }
                }
            }
        }
        .overlay(
            RoundedRectangle(cornerRadius: 4)
                .stroke(Color(nsColor: .separatorColor).opacity(0.5), lineWidth: 1)
        )
        .padding(.horizontal, 4)
    }

    // MARK: - Header

    @ViewBuilder
    private func headerButton(_ title: String, key: String?, width: CGFloat) -> some View {
        if let key = key {
            Button {
                if viewModel.syncSortKey == key {
                    viewModel.syncSortAscending.toggle()
                } else {
                    viewModel.syncSortKey = key
                    viewModel.syncSortAscending = false
                }
            } label: {
                HStack(spacing: 2) {
                    Text(title)
                    if viewModel.syncSortKey == key {
                        Image(systemName: viewModel.syncSortAscending ? "chevron.up" : "chevron.down")
                            .font(.caption2)
                    }
                }
            }
            .buttonStyle(.plain)
            .frame(width: width, alignment: .leading)
        } else {
            Text(title)
                .frame(width: width, alignment: .leading)
        }
    }

    // MARK: - Merge Parent Row

    @ViewBuilder
    private func mergeParentRow(group: MergeGroup) -> some View {
        let fileExists = FileManager.default.fileExists(atPath: group.outputPath)
        let totalSize = group.childNames.reduce(0) { total, name in
            let entry = viewModel.visibleEntries.first { $0.recording.name == name }
            return total + (entry?.recording.length ?? 0)
        }
        let firstChild = viewModel.visibleEntries.first { group.childNames.contains($0.recording.name) }
        let deviceName = firstChild?.deviceName ?? ""
        let earliestDate = group.childNames.compactMap { name in
            viewModel.visibleEntries.first { $0.recording.name == name }
        }.map { "\($0.recording.createDate) \($0.recording.createTime)" }.sorted().first ?? ""

        HStack(spacing: 0) {
            // Checkbox
            Toggle("", isOn: Binding(
                get: { viewModel.syncCheckedRecordings.contains("merge:\(group.id)") },
                set: { _ in viewModel.onToggleChecked("merge:\(group.id)", false) }
            ))
            .toggleStyle(.checkbox)
            .labelsHidden()
            .frame(width: 36)

            // Device + expand arrow — line glyph to the right of the
            // name so rows column-align.
            HStack(spacing: 6) {
                Text(deviceName)
                    .lineLimit(1)
                if let glyph = hidockDeviceGlyph(deviceName, deviceType: .hidock) {
                    glyph
                        .resizable()
                        .scaledToFit()
                        .frame(width: 16, height: 16)
                        .foregroundColor(.secondary)
                }
                Button {
                    viewModel.onToggleMergeExpand(group.id)
                } label: {
                    Image(systemName: viewModel.expandedMergeGroups.contains(group.id) ? "chevron.down" : "chevron.right")
                        .font(.caption)
                        .foregroundColor(.accentColor)
                }
                .buttonStyle(.plain)
            }
            .frame(width: 120, alignment: .leading)

            // Status
            StatusBadge(text: "Merged", level: .info)
                .frame(width: 110, alignment: .leading)

            // Transcription state for merged file
            mergeTranscriptionIndicator(group: group)
                .frame(width: 90, alignment: .leading)

            // Summary column placeholder — merge groups don't carry a
            // per-entry summaryPath; dash keeps the columns aligned with
            // the regular rows.
            Text("—")
                .foregroundColor(.secondary.opacity(0.5))
                .frame(width: 80, alignment: .leading)

            // Recording name — truncate to match regular row length
            let displayName = group.outputName.count > 30
                ? String(group.outputName.prefix(28)) + "…"
                : group.outputName
            Text(displayName)
                .lineLimit(1)
                .frame(width: 220, alignment: .leading)
                .clipped()

            // Created (earliest child)
            Text(earliestDate)
                .font(.caption.monospacedDigit())
                .frame(width: 155, alignment: .leading)

            // Length (total)
            Text(formatRecordingDuration(group.totalDuration))
                .font(.caption.monospacedDigit())
                .frame(width: 70, alignment: .leading)

            // Size (total)
            Text(humanSize(totalSize))
                .font(.caption.monospacedDigit())
                .frame(width: 70, alignment: .leading)

            // Actions — width + alignment match the recording row so
            // folder icons line up across rows. The merge-parent gets
            // the same blue merge-triangle indicator the children
            // carry, so the parent and child rows visually advertise
            // the same merge group at a glance.
            HStack(spacing: 4) {
                if fileExists {
                    Button {
                        viewModel.onRevealRecording(group.outputPath)
                    } label: {
                        Image(systemName: "folder")
                    }
                    .buttonStyle(.plain)
                    .foregroundColor(.accentColor)
                    .help("Show in Finder")
                }
                Image(systemName: "arrow.triangle.merge")
                    .font(.caption2)
                    .foregroundColor(.blue)
                    .help("Merged from \(group.childNames.count) recordings")
            }
            .frame(width: 70, alignment: .leading)

            Spacer(minLength: 0)
        }
        .font(.system(size: 12))
        .padding(.vertical, 1)
    }

    @ViewBuilder
    private func mergeTranscriptionIndicator(group: MergeGroup) -> some View {
        // Merge groups don't live in syncEntries, so we can't reuse
        // the per-row entry lookup the regular row uses. Read from the
        // viewModel.mergedFileTranscribed / mergedFileTagged sets
        // instead — refreshTranscriptionState populates these from the
        // same Python `transcribe.py status` JSON that drives the
        // per-row state.
        let mp3Name = (group.outputPath as NSString).lastPathComponent
        let isTranscribed = viewModel.mergedFileTranscribed.contains(mp3Name)
        let isTagged = viewModel.mergedFileTagged.contains(mp3Name)
        let path = viewModel.mergedFileTranscriptPaths[mp3Name]

        if isTranscribed && isTagged {
            Button {
                if let path = path {
                    viewModel.onOpenTranscriptViewer(path)
                }
            } label: {
                Image(systemName: "checkmark.circle.fill")
                    .foregroundColor(.green)
            }
            .buttonStyle(.plain)
            .help("Transcribed and tagged")
        } else if isTranscribed {
            Button {
                if let path = path {
                    viewModel.onOpenTranscriptViewer(path)
                }
            } label: {
                Image(systemName: "tag.fill")
                    .foregroundColor(.orange)
            }
            .buttonStyle(.plain)
            .help("Needs speaker tagging")
        } else if viewModel.transcriptionBusy && viewModel.transcriptionCurrentFile == mp3Name {
            HStack(spacing: 4) {
                ProgressView()
                    .controlSize(.mini)
                Text("\(viewModel.transcriptionProgress)%")
                    .font(.caption.monospacedDigit())
                    .foregroundColor(.orange)
            }
        } else {
            Text("—")
                .foregroundColor(.secondary.opacity(0.5))
        }
    }

    private func humanSize(_ bytes: Int) -> String {
        if bytes < 1024 { return "\(bytes) B" }
        let kb = Double(bytes) / 1024
        if kb < 1024 { return String(format: "%.0f KB", kb) }
        let mb = kb / 1024
        return String(format: "%.1f MB", mb)
    }

    // MARK: - Recording Row

    @ViewBuilder
    private func recordingRow(entry: HiDockSyncRecordingEntry, indented: Bool) -> some View {
        // Highlight whole-row when the merge-candidate detector flagged
        // this row. The earlier 3pt left-bar was too subtle to spot in
        // a long table; a row-wide blue tint catches the eye without
        // overwhelming the row's own content. Background tint applied
        // at the bottom via .background — declared here so all the
        // child views render in front.
        let isCandidate = viewModel.mergeCandidatePaths.contains(entry.recording.outputPath)
        HStack(spacing: 0) {
            if indented {
                Color.clear.frame(width: 24) // indent for merge children
                Image(systemName: "arrow.turn.down.right")
                    .font(.caption2)
                    .foregroundColor(.secondary)
                    .frame(width: 12)
            }

            Toggle("", isOn: Binding(
                get: { viewModel.syncCheckedRecordings.contains(entry.recording.name) },
                set: { _ in
                    let shiftHeld = NSEvent.modifierFlags.contains(.shift)
                    viewModel.onToggleChecked(entry.recording.name, shiftHeld)
                }
            ))
            .toggleStyle(.checkbox)
            .labelsHidden()
            .frame(width: indented ? 24 : 36)

            HStack(spacing: 6) {
                // Line-glyph (flat SVG, monochrome) for a compact visual
                // cue beside the device name — the product-photo assets
                // live on the big cards at the top; in the table they'd
                // be too busy. Glyph goes AFTER the name so the text
                // column-aligns between rows.
                Text(entry.deviceName)
                    .lineLimit(1)
                let rowDeviceType: DeviceType = entry.deviceId.hasPrefix("volume:") ? .volume : (entry.deviceId.hasPrefix("plaud:") ? .plaud : .hidock)
                if let glyph = hidockDeviceGlyph(entry.deviceName, deviceType: rowDeviceType) {
                    glyph
                        .resizable()
                        .scaledToFit()
                        .frame(width: 16, height: 16)
                        .foregroundColor(.secondary)
                } else {
                    Image(systemName: hidockDeviceIcon(entry.deviceName, deviceType: rowDeviceType))
                        .font(.caption)
                        .foregroundColor(.secondary)
                        .frame(width: 16, height: 16)
                }
            }
            .frame(width: 120, alignment: .leading)

            // In-flight overlay: when this row is the file the extractor
            // is actively pulling, paint "Downloading"; when it's the
            // file the transcriber is actively running, paint
            // "Transcribing". Both overlays use .warning (orange/yellow)
            // so the eye can spot live work at a glance. Underlying
            // statusText / statusLevel are unchanged — pipeline-stage
            // filters and sort still see the lifecycle state.
            let isDownloadingRow = viewModel.currentlyDownloadingName == entry.recording.name
            let isTranscribingRow = viewModel.transcriptionBusy
                && viewModel.transcriptionCurrentFile == entry.recording.outputName
            let isSummarisingRow = viewModel.summarisingNames.contains(entry.recording.outputName)
            let badgeText: String = {
                if isDownloadingRow { return "Downloading" }
                if isTranscribingRow { return "Transcribing" }
                if isSummarisingRow { return "Summarising" }
                return entry.statusText
            }()
            let badgeLevel: StatusLevel = (isDownloadingRow || isTranscribingRow || isSummarisingRow) ? .warning : entry.statusLevel
            ClickableStatusBadge(
                text: badgeText,
                level: badgeLevel,
                errorMessage: entry.recording.lastError
            )
            .frame(width: 110, alignment: .leading)

            TranscriptionIndicator(
                entry: entry,
                transcriptionBusy: viewModel.transcriptionBusy,
                transcriptionCurrentFile: viewModel.transcriptionCurrentFile,
                transcriptionProgress: viewModel.transcriptionProgress,
                transcriptionFailed: viewModel.failedTranscriptionPaths.contains(entry.recording.outputPath),
                transcriptionErrorMessage: viewModel.transcriptionErrorMessage(for: entry.recording.outputPath),
                onRevealTranscript: viewModel.onRevealTranscript,
                onOpenTranscriptViewer: viewModel.onOpenTranscriptViewer
            )
            .frame(width: 90, alignment: .leading)

            // Summary column — indigo doc tick when a typed summary exists
            // (click opens it), spinner while summarising, dash otherwise.
            // Same open-on-click affordance as the Tagged column.
            Group {
                if isSummarisingRow {
                    ProgressView()
                        .controlSize(.mini)
                } else if let sp = entry.summaryPath, !sp.isEmpty {
                    Button {
                        viewModel.onViewSummary(sp)
                    } label: {
                        Image(systemName: "doc.text.fill")
                            .foregroundColor(.indigo)
                    }
                    .buttonStyle(.plain)
                    .help("Summary ready — click to open")
                } else {
                    Text("—")
                        .foregroundColor(.secondary.opacity(0.5))
                }
            }
            .frame(width: 80, alignment: .leading)

            Text(entry.recording.outputName)
                .lineLimit(1)
                .truncationMode(.middle)
                .frame(width: 220, alignment: .leading)

            Text("\(entry.recording.createDate) \(entry.recording.createTime)")
                .font(.caption.monospacedDigit())
                .frame(width: 155, alignment: .leading)

            // The extractor pre-download estimate is `file_size / 8000`
            // (assumes 64 kbps), which is correct for H1 (16 kHz/64 kbps)
            // but ~50% over for P1 (48 kHz/96 kbps). Real value comes
            // from mutagen reading the MP3 once the file is local.
            //
            // We trust the explicit `durationEstimated` flag if the
            // extractor sent one (newer payloads always do). Older
            // payloads default to the previous heuristic: "estimated
            // iff not localExists." This keeps the `~` accurate even
            // if mutagen ever fails post-download — which is exactly
            // how a 2h P1 recording silently displayed as 3h until the
            // missing-mutagen dependency was caught on 2026-04-25.
            let isEstimated: Bool = {
                if let flag = entry.recording.durationEstimated {
                    return flag
                }
                return !entry.recording.localExists
            }()
            Text(isEstimated
                 ? "~" + formatRecordingDuration(entry.recording.duration)
                 : formatRecordingDuration(entry.recording.duration))
                .font(.caption.monospacedDigit())
                .foregroundColor(isEstimated ? .secondary : .primary)
                .frame(width: 70, alignment: .leading)
                .help(isEstimated
                      ? "Estimated duration — actual value will appear after download (read from MP3)"
                      : "")

            Text(entry.recording.humanLength)
                .font(.caption.monospacedDigit())
                .frame(width: 70, alignment: .leading)

            HStack(spacing: 4) {
                // Show in Finder gates on `localExists` only, not on the
                // state.json `downloaded` flag. A file can land on disk
                // with `downloaded=false` if the bytes-written came up
                // short of the device-reported length (saw a 7KB
                // mismatch on Rec63 on 2026-04-27 — file was perfectly
                // playable + transcribed but the flag stayed false).
                // The `downloaded` flag is for download-decisioning
                // ("should we auto-fetch this again"); UI affordances
                // should ask the filesystem.
                if entry.recording.localExists {
                    Button {
                        viewModel.onRevealRecording(entry.recording.outputPath)
                    } label: {
                        Image(systemName: "folder")
                    }
                    .buttonStyle(.plain)
                    .foregroundColor(.accentColor)
                    .help("Show in Finder")
                }
                // Indicator icons, right of the folder button:
                //   - scissors: the local file was trimmed in-place;
                //     flagged in state.json so refreshes preserve it
                //     and re-downloads warn.
                //   - arrow.triangle.merge: this recording is a child
                //     of an active merge group (i.e. its bytes were
                //     combined into a merged .mp3 alongside siblings).
                // Both icons use `.blue` so they pick up exactly the
                // same colour as the "Merged" StatusBadge (which is
                // `.info` → `.blue`). Keeps the affordance consistent
                // visually across the row — folder + trim + merge all
                // read as one related set, not three different things.
                if entry.recording.trimmed == true {
                    Image(systemName: "scissors")
                        .font(.caption2)
                        .foregroundColor(.blue)
                        .help("Trimmed locally — Re-download will overwrite this with the original from the device")
                }
                if viewModel.mergeGroups.contains(where: { $0.childNames.contains(entry.recording.name) }) {
                    Image(systemName: "arrow.triangle.merge")
                        .font(.caption2)
                        .foregroundColor(.blue)
                        .help("Included in a merge group")
                }
            }
            .frame(width: 70, alignment: .leading)

            // Per-row "Potential merge" toggle for candidate rows. Click
            // ticks the row; once 2+ are ticked, the toolbar surfaces
            // a "Merge N selected" button that combines exactly those
            // ticks. Lets the user pick which pieces from a detected
            // chain actually go together (a 5-piece chain may have a
            // genuine boundary in the middle).
            if isCandidate {
                let isTicked = viewModel.mergeCandidatesTicked.contains(entry.recording.outputPath)
                Button {
                    if isTicked {
                        viewModel.mergeCandidatesTicked.remove(entry.recording.outputPath)
                    } else {
                        viewModel.mergeCandidatesTicked.insert(entry.recording.outputPath)
                    }
                } label: {
                    HStack(spacing: 3) {
                        Image(systemName: isTicked ? "checkmark.square.fill" : "square")
                            .font(.caption)
                        Text(isTicked ? "Selected for merge" : "Potential merge")
                            .font(.caption)
                    }
                    .foregroundColor(.blue)
                }
                .buttonStyle(.plain)
                .help("System suggests this might be part of one conversation. Tick rows you want to merge, then click 'Merge N selected' in the toolbar.")
            }

            Spacer(minLength: 0)
        }
        .font(.system(size: 12))
        .padding(.vertical, 1)
        // Whole-row tint when this is a candidate. Subtle enough not
        // to overpower selection / hover styles, distinct enough to
        // catch the eye in a long table.
        .background(isCandidate ? Color.blue.opacity(0.10) : Color.clear)
    }

    // MARK: - Context Menu

    @ViewBuilder
    private func entryContextMenu(entry: HiDockSyncRecordingEntry) -> some View {
        // Merge-candidate suggestions live at the top of the menu so
        // users see "the system thinks this might pair with another"
        // without having to scan past the regular actions. Surfaces
        // only when this row is in a currently-visible candidate
        // chain — high-confidence by default, all candidates if the
        // user has flipped the toggle.
        let candidates = viewModel.effectiveMergeCandidates.filter { cand in
            (viewModel.mergeCandidatesShowAll || cand.high_confidence)
                && cand.pieces.contains(where: { $0.mp3_path == entry.recording.outputPath })
        }
        if !candidates.isEmpty {
            ForEach(candidates) { cand in
                let others = cand.pieces
                    .map { ($0.mp3_name as NSString).deletingPathExtension }
                    .filter { $0 != (entry.recording.outputName as NSString).deletingPathExtension }
                let othersLabel = others.isEmpty ? "adjacent recording" :
                    others.map { $0.split(separator: "-").last.map(String.init) ?? $0 }
                          .joined(separator: ", ")
                Button {
                    viewModel.onMergeCandidate(cand)
                } label: {
                    Label("Merge with \(othersLabel)", systemImage: "arrow.triangle.merge")
                }
                Button {
                    viewModel.onDismissMergeCandidate(cand)
                } label: {
                    Label("Dismiss merge suggestion", systemImage: "xmark.circle")
                }
            }
            Divider()
        }
        if entry.transcribed {
            let summarising = viewModel.summarisingNames.contains(entry.recording.outputName)
            Button {
                viewModel.onSummariseRecording(entry)
            } label: {
                Label(summarising ? "Summarising…" : "Summarise with Claude Code", systemImage: "sparkles")
            }
            .disabled(summarising)
            Button {
                viewModel.onAskClaudeRecording(entry)
            } label: {
                Label("Ask Claude Code…", systemImage: "terminal")
            }
            if let sp = entry.summaryPath, !sp.isEmpty {
                Button {
                    viewModel.onViewSummary(sp)
                } label: {
                    Label("View Summary", systemImage: "doc.text.magnifyingglass")
                }
            }
            Divider()
        }
        if !entry.recording.downloaded {
            Button {
                viewModel.onDownloadSelected()
            } label: {
                Label("Download", systemImage: "arrow.down.circle")
            }
        }

        if !entry.recording.downloaded {
            Button {
                viewModel.onMarkDownloaded()
            } label: {
                Label("Skip", systemImage: "forward.fill")
            }
        }

        if entry.recording.downloaded && !entry.recording.localExists {
            Button {
                viewModel.onUnmarkDownloaded()
            } label: {
                Label("Unskip", systemImage: "backward.fill")
            }
        }

        if !entry.transcribed {
            Button {
                viewModel.onTranscribeSelected()
            } label: {
                Label("Transcribe", systemImage: "text.bubble")
            }
        }

        if entry.recording.localExists {
            Menu {
                // Presets cover 1:1s, small meetings, and typical group
                // panels. Auto leaves the density-prior estimator in charge
                // (current default).
                ForEach([1, 2, 3, 4, 5, 6, 8, 10], id: \.self) { n in
                    Button("\(n) speaker\(n == 1 ? "" : "s")") {
                        viewModel.onTranscribeWithSpeakerCount(entry.recording.name, n)
                    }
                }
            } label: {
                Label("Transcribe with speaker count…", systemImage: "person.2.wave.2")
            }
        }

        Divider()

        if entry.recording.localExists {
            Button {
                viewModel.onRevealRecording(entry.recording.outputPath)
            } label: {
                Label("Show in Finder", systemImage: "folder")
            }
        }

        if let path = entry.transcriptPath, !path.isEmpty {
            Button {
                viewModel.onRevealTranscript(path)
            } label: {
                Label("Open Transcript", systemImage: "doc.text")
            }
            Button {
                viewModel.onExportSRT(path)
            } label: {
                Label("Export as SRT…", systemImage: "captions.bubble")
            }
        }

        if entry.recording.localExists {
            Divider()

            Button {
                viewModel.onTrimRecording(entry.recording.outputPath)
            } label: {
                Label("Trim…", systemImage: "scissors")
            }

            if viewModel.syncCheckedRecordings.count >= 2 {
                Button {
                    viewModel.onMergeSelected()
                } label: {
                    Label("Merge Selected", systemImage: "arrow.triangle.merge")
                }
            }
        }

        if entry.deviceId == "imported:local" {
            Divider()
            Button(role: .destructive) {
                viewModel.onRemoveImport(entry.recording.name)
            } label: {
                Label("Remove Import", systemImage: "trash")
            }
        } else if entry.recording.localExists {
            // HiDock recordings: offer to delete only the local copy.
            // Deleting from the device itself isn't supported yet —
            // the HiDock USB protocol we've reverse-engineered doesn't
            // include a delete command. Users can delete on-device
            // recordings through the HiNotes app.
            Divider()
            Button(role: .destructive) {
                viewModel.onDeleteLocalCopy(entry.recording.name)
            } label: {
                Label("Delete Local Copy", systemImage: "trash")
            }
        }
    }
}
