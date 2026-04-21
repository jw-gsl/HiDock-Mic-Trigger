import SwiftUI

struct SyncToolbarSection: View {
    @ObservedObject var viewModel: HiDockViewModel

    var body: some View {
        VStack(spacing: 6) {
            // Row: Pair/Unpair/Folders/Refresh on left, Transcribe on right
            HStack(spacing: 6) {
                Button {
                    viewModel.onPairDock()
                } label: {
                    Label("Pair", systemImage: "link.badge.plus")
                }
                .disabled(viewModel.syncBusy)

                Button {
                    viewModel.onUnpairDock()
                } label: {
                    Label("Unpair", systemImage: "minus.circle")
                }
                .disabled(viewModel.syncBusy || !viewModel.syncPaired)

                Button {
                    viewModel.onChooseRecordingsFolder()
                } label: {
                    Label("Recordings", systemImage: "folder")
                }

                Button {
                    viewModel.onChooseTranscriptFolder()
                } label: {
                    Label("Transcripts", systemImage: "doc.text")
                }

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

                Spacer()

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

                Toggle("Speaker Labels", isOn: Binding(
                    get: { viewModel.diarizeEnabled },
                    set: { _ in viewModel.onToggleDiarize() }
                ))
                .toggleStyle(.checkbox)

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

            // Selection & filter row
            HStack(spacing: 8) {
                Button("Select All") { viewModel.onSelectAll() }
                Button("Select None") { viewModel.onSelectNone() }
                Button("Select New") { viewModel.onSelectNotDownloaded() }

                Divider().frame(height: 16)

                Text("Filter:")
                    .font(.caption.weight(.medium))

                Button("All") {
                    viewModel.onFilterByDevice(nil)
                }
                .buttonStyle(.bordered)
                .tint(viewModel.syncFilterDeviceId == nil ? .accentColor : nil)

                ForEach(viewModel.syncPairedDevices, id: \.deviceId) { device in
                    let connected = viewModel.syncDeviceConnected[device.deviceId] ?? false
                    let unreachable = viewModel.syncDeviceLastError[device.deviceId] != nil
                    HStack(spacing: 2) {
                        Button {
                            viewModel.onFilterByDevice(device.deviceId)
                        } label: {
                            HStack(spacing: 4) {
                                Circle()
                                    .fill(unreachable ? Color.orange : (connected ? Color.green : Color.gray))
                                    .frame(width: 6, height: 6)
                                Text(device.shortName)
                            }
                        }
                        .buttonStyle(.bordered)
                        .tint(viewModel.syncFilterDeviceId == device.deviceId ? .accentColor : nil)
                        // Reconnect affordance: always present, but the
                        // arrow turns orange when the device's last query
                        // failed so it stands out as the next logical
                        // action. Clicking runs a fresh status probe.
                        Button {
                            viewModel.onReconnectDevice(device.deviceId)
                        } label: {
                            Image(systemName: "arrow.clockwise.circle\(unreachable ? ".fill" : "")")
                                .foregroundColor(unreachable ? .orange : .secondary)
                        }
                        .buttonStyle(.plain)
                        .help(unreachable
                            ? "\(device.shortName) is unreachable — try reconnecting"
                            : "Reconnect \(device.shortName)")
                    }
                }

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
