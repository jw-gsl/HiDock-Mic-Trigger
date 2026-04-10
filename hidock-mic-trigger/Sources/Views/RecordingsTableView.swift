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
                headerButton("Transcribed", key: nil, width: 90)
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
        HStack(spacing: 6) {
            // Expand/collapse arrow
            Button {
                viewModel.onToggleMergeExpand(group.id)
            } label: {
                Image(systemName: viewModel.expandedMergeGroups.contains(group.id) ? "chevron.down" : "chevron.right")
                    .font(.caption)
                    .foregroundColor(.secondary)
                    .frame(width: 16)
            }
            .buttonStyle(.plain)

            Image(systemName: "arrow.triangle.merge")
                .foregroundColor(.purple)
                .frame(width: 16)

            StatusBadge(text: "Merged", level: .info)
                .frame(width: 80)

            Text(group.outputName)
                .lineLimit(1)
                .truncationMode(.middle)
                .fontWeight(.medium)

            Spacer()

            Text(formatRecordingDuration(group.totalDuration))
                .font(.caption.monospacedDigit())
                .foregroundColor(.secondary)

            Text("\(group.childNames.count) files")
                .font(.caption)
                .foregroundColor(.secondary)
                .padding(.horizontal, 6)
                .padding(.vertical, 2)
                .background(Color.purple.opacity(0.1))
                .cornerRadius(4)

            if FileManager.default.fileExists(atPath: group.outputPath) {
                Button {
                    viewModel.onRevealRecording(group.outputPath)
                } label: {
                    Image(systemName: "folder")
                }
                .buttonStyle(.plain)
                .foregroundColor(.accentColor)
            }
        }
        .padding(.vertical, 2)
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

            Text(entry.deviceName)
                .lineLimit(1)
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
    }
}
