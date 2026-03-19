import SwiftUI

struct RecordingsTableView: View {
    @ObservedObject var viewModel: HiDockViewModel

    var body: some View {
        Table(viewModel.visibleEntries) {
            TableColumn("") { entry in
                Toggle("", isOn: Binding(
                    get: { viewModel.syncCheckedRecordings.contains(entry.recording.name) },
                    set: { _ in viewModel.onToggleChecked(entry.recording.name) }
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
                    onRevealTranscript: viewModel.onRevealTranscript
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
            .width(36)
        }
        .contextMenu(forSelectionType: HiDockSyncRecordingEntry.ID.self) { _ in
            Button("Mark as Downloaded") { viewModel.onMarkDownloaded() }
        }
    }
}
