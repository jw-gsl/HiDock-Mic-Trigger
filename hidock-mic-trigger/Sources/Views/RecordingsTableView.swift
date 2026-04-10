import SwiftUI

struct RecordingsTableView: View {
    @ObservedObject var viewModel: HiDockViewModel

    var body: some View {
        Table(viewModel.visibleEntries) {
            TableColumn("") { entry in
                Toggle("", isOn: Binding(
                    get: { viewModel.syncCheckedRecordings.contains(entry.recording.name) },
                    set: { _ in
                        let shiftHeld = NSEvent.modifierFlags.contains(.shift)
                        viewModel.onToggleChecked(entry.recording.name, shiftHeld)
                    }
                ))
                .toggleStyle(.checkbox)
                .labelsHidden()
            }
            .width(36)

            TableColumn("Device") { entry in
                Text(entry.deviceName)
                    .lineLimit(1)
            }
            .width(min: 80, ideal: 120, max: 160)

            TableColumn("Status") { entry in
                StatusBadge(text: entry.statusText, level: entry.statusLevel)
            }
            .width(min: 80, ideal: 110, max: 130)

            TableColumn("Transcribed") { entry in
                TranscriptionIndicator(
                    entry: entry,
                    transcriptionBusy: viewModel.transcriptionBusy,
                    transcriptionCurrentFile: viewModel.transcriptionCurrentFile,
                    transcriptionProgress: viewModel.transcriptionProgress,
                    onRevealTranscript: viewModel.onRevealTranscript,
                    onOpenTranscriptViewer: viewModel.onOpenTranscriptViewer
                )
            }
            .width(min: 70, ideal: 90, max: 100)

            TableColumn("Recording") { entry in
                Text(entry.recording.outputName)
                    .lineLimit(1)
                    .truncationMode(.middle)
            }
            .width(min: 120, ideal: 220, max: 400)

            TableColumn("Created") { entry in
                Text("\(entry.recording.createDate) \(entry.recording.createTime)")
                    .font(.caption.monospacedDigit())
            }
            .width(min: 120, ideal: 155, max: 170)

            TableColumn("Length") { entry in
                Text(formatRecordingDuration(entry.recording.duration))
                    .font(.caption.monospacedDigit())
            }
            .width(min: 50, ideal: 70, max: 80)

            TableColumn("Size") { entry in
                Text(entry.recording.humanLength)
                    .font(.caption.monospacedDigit())
            }
            .width(min: 50, ideal: 70, max: 80)

            TableColumn("Output") { entry in
                if entry.recording.downloaded {
                    Text(entry.recording.outputPath)
                        .lineLimit(1)
                        .truncationMode(.middle)
                        .font(.caption)
                } else {
                    Text("—")
                        .foregroundColor(.secondary.opacity(0.5))
                }
            }
            .width(min: 100, ideal: 250, max: .infinity)

            TableColumn("") { entry in
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
                    if entry.summaryPath != nil {
                        Button {
                            if let path = entry.summaryPath {
                                viewModel.onOpenInObsidian(path)
                            }
                        } label: {
                            Image(systemName: "book.closed")
                        }
                        .buttonStyle(.plain)
                        .foregroundColor(.purple)
                        .help("Open in Obsidian")
                    }
                }
            }
            .width(min: 36, ideal: 60, max: 70)
        }
        .contextMenu(forSelectionType: HiDockSyncRecordingEntry.ID.self) { selection in
            if let id = selection.first, let entry = viewModel.visibleEntries.first(where: { $0.id == id }) {
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
                        Label("Mark as Downloaded", systemImage: "checkmark.circle")
                    }
                }

                if entry.recording.downloaded {
                    Button {
                        viewModel.onUnmarkDownloaded()
                    } label: {
                        Label("Unmark Downloaded", systemImage: "arrow.uturn.backward.circle")
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
        .padding(4)
        .overlay(
            RoundedRectangle(cornerRadius: 4)
                .stroke(Color(nsColor: .separatorColor).opacity(0.5), lineWidth: 1)
        )
        .padding(.horizontal, 4)
    }
}
