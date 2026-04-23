import SwiftUI

struct RecordingsTableView: View {
    @ObservedObject var viewModel: HiDockViewModel

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
            List(viewModel.displayRows) { row in
                switch row {
                case .recording(let entry):
                    recordingRow(entry: entry, indented: false)
                        .contextMenu { entryContextMenu(entry: entry) }
                case .mergeParent(let group):
                    mergeParentRow(group: group)
                case .mergeChild(let entry):
                    recordingRow(entry: entry, indented: true)
                        .contextMenu { entryContextMenu(entry: entry) }
                }
            }
            .listStyle(.plain)
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

            // Device + expand arrow — with small product-photo glyph
            // so H1/P1/H1E are visually distinct at a glance.
            HStack(spacing: 6) {
                if let img = hidockDeviceImage(deviceName, deviceType: .hidock) {
                    img
                        .resizable()
                        .scaledToFit()
                        .frame(width: 20, height: 20)
                } else {
                    Image(systemName: "externaldrive")
                        .font(.callout)
                        .foregroundColor(.secondary)
                        .frame(width: 20, height: 20)
                }
                Text(deviceName)
                    .lineLimit(1)
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

            // Actions
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
            }
            .frame(width: 50)

            Spacer(minLength: 0)
        }
        .font(.system(size: 12))
        .padding(.vertical, 1)
    }

    @ViewBuilder
    private func mergeTranscriptionIndicator(group: MergeGroup) -> some View {
        // Check if the merged file has a transcript
        let mp3Name = (group.outputPath as NSString).lastPathComponent
        let entry = viewModel.syncEntries.first { $0.recording.outputName == mp3Name }

        if let entry = entry, entry.transcribed && entry.speakersTagged {
            Button {
                if let path = entry.transcriptPath {
                    viewModel.onOpenTranscriptViewer(path)
                }
            } label: {
                Image(systemName: "checkmark.circle.fill")
                    .foregroundColor(.green)
            }
            .buttonStyle(.plain)
            .help("Transcribed and tagged")
        } else if let entry = entry, entry.transcribed && !entry.speakersTagged {
            Button {
                if let path = entry.transcriptPath {
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
                // Small device photo (H1/H1E/P1 product shots) before
                // the text, so H1 and P1 are distinguishable at a glance
                // — the names are only one character apart. Falls back
                // to an SF Symbol for generic volumes / unknown SKUs.
                if let img = hidockDeviceImage(entry.deviceName, deviceType: .hidock) {
                    img
                        .resizable()
                        .scaledToFit()
                        .frame(width: 20, height: 20)
                } else {
                    Image(systemName: hidockDeviceIcon(entry.deviceName, deviceType: entry.deviceId.hasPrefix("volume:") ? .volume : .hidock))
                        .font(.callout)
                        .foregroundColor(.secondary)
                        .frame(width: 20, height: 20)
                }
                Text(entry.deviceName)
                    .lineLimit(1)
            }
            .frame(width: 120, alignment: .leading)

            StatusBadge(text: entry.statusText, level: entry.statusLevel)
                .frame(width: 110, alignment: .leading)

            TranscriptionIndicator(
                entry: entry,
                transcriptionBusy: viewModel.transcriptionBusy,
                transcriptionCurrentFile: viewModel.transcriptionCurrentFile,
                transcriptionProgress: viewModel.transcriptionProgress,
                onRevealTranscript: viewModel.onRevealTranscript,
                onOpenTranscriptViewer: viewModel.onOpenTranscriptViewer
            )
            .frame(width: 90, alignment: .leading)

            Text(entry.recording.outputName)
                .lineLimit(1)
                .truncationMode(.middle)
                .frame(width: 220, alignment: .leading)

            Text("\(entry.recording.createDate) \(entry.recording.createTime)")
                .font(.caption.monospacedDigit())
                .frame(width: 155, alignment: .leading)

            Text(formatRecordingDuration(entry.recording.duration))
                .font(.caption.monospacedDigit())
                .frame(width: 70, alignment: .leading)

            Text(entry.recording.humanLength)
                .font(.caption.monospacedDigit())
                .frame(width: 70, alignment: .leading)

            HStack(spacing: 4) {
                if entry.recording.downloaded && entry.recording.localExists {
                    Button {
                        viewModel.onRevealRecording(entry.recording.outputPath)
                    } label: {
                        Image(systemName: "folder")
                    }
                    .buttonStyle(.plain)
                    .foregroundColor(.accentColor)
                    .help("Show in Finder")
                }
            }
            .frame(width: 50)

            Spacer(minLength: 0)
        }
        .font(.system(size: 12))
        .padding(.vertical, 1)
    }

    // MARK: - Context Menu

    @ViewBuilder
    private func entryContextMenu(entry: HiDockSyncRecordingEntry) -> some View {
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

        if entry.recording.downloaded && entry.recording.localExists {
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

        if entry.recording.downloaded && entry.recording.localExists {
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

        if entry.recording.downloaded && entry.recording.localExists {
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
        } else if entry.recording.downloaded && entry.recording.localExists {
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
