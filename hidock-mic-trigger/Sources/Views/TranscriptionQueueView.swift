import SwiftUI

struct TranscriptionQueueView: View {
    @ObservedObject var viewModel: HiDockViewModel

    var body: some View {
        VStack(spacing: 0) {
            // Header
            HStack {
                Image(systemName: "list.bullet.rectangle")
                    .foregroundColor(.secondary)
                Text("Transcription Queue")
                    .font(.headline)
                Spacer()

                if viewModel.transcriptionBusy {
                    if viewModel.transcriptionPaused {
                        Button {
                            viewModel.onResumeTranscription()
                        } label: {
                            Label("Resume", systemImage: "play.fill")
                        }
                        .buttonStyle(.bordered)
                        .controlSize(.small)
                    } else {
                        Button {
                            viewModel.onPauseTranscription()
                        } label: {
                            Label("Pause", systemImage: "pause.fill")
                        }
                        .buttonStyle(.bordered)
                        .controlSize(.small)
                    }

                    Button {
                        viewModel.onCancelTranscription()
                    } label: {
                        Label("Cancel All", systemImage: "xmark.circle")
                    }
                    .buttonStyle(.bordered)
                    .controlSize(.small)
                    .tint(.red)
                }
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 10)
            .background(.ultraThinMaterial)

            Divider()

            if viewModel.transcriptionQueue.isEmpty {
                VStack(spacing: 8) {
                    Spacer()
                    Image(systemName: "checkmark.circle")
                        .font(.largeTitle)
                        .foregroundColor(.secondary)
                    Text("Queue is empty")
                        .foregroundColor(.secondary)
                    Spacer()
                }
            } else {
                List {
                    ForEach(viewModel.transcriptionQueue) { item in
                        queueRow(item: item)
                    }
                    .onMove { from, to in
                        guard let source = from.first else { return }
                        viewModel.onMoveInQueue(source, to)
                    }
                }
                .listStyle(.inset)
            }

            // Footer summary
            HStack {
                let queued = viewModel.transcriptionQueue.filter { $0.status == .queued }.count
                let active = viewModel.transcriptionQueue.filter { $0.status == .transcribing }.count
                let done = viewModel.transcriptionQueue.filter { $0.status == .completed }.count

                if active > 0 {
                    Image(systemName: "waveform")
                        .foregroundColor(.blue)
                    Text("\(active) transcribing")
                        .font(.caption)
                }
                if queued > 0 {
                    Image(systemName: "clock")
                        .foregroundColor(.secondary)
                    Text("\(queued) queued")
                        .font(.caption)
                }
                if done > 0 {
                    Image(systemName: "checkmark.circle.fill")
                        .foregroundColor(.green)
                    Text("\(done) done")
                        .font(.caption)
                }
                if viewModel.transcriptionPaused {
                    Text("— PAUSED")
                        .font(.caption.bold())
                        .foregroundColor(.orange)
                }
                Spacer()
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 6)
            .background(.ultraThinMaterial)
        }
        .frame(minWidth: 400, minHeight: 300)
    }

    @ViewBuilder
    private func queueRow(item: TranscriptionQueueItem) -> some View {
        HStack(spacing: 8) {
            // Status icon
            switch item.status {
            case .transcribing:
                ProgressView()
                    .controlSize(.small)
                    .frame(width: 16)
            case .completed:
                Image(systemName: "checkmark.circle.fill")
                    .foregroundColor(.green)
            case .failed:
                Image(systemName: "xmark.circle.fill")
                    .foregroundColor(.red)
            case .cancelled:
                Image(systemName: "slash.circle")
                    .foregroundColor(.secondary)
            case .queued:
                Image(systemName: "clock")
                    .foregroundColor(.secondary)
            }

            VStack(alignment: .leading, spacing: 2) {
                Text(item.filename)
                    .font(.body)
                    .lineLimit(1)

                if item.status == .transcribing && item.progress > 0 {
                    ProgressView(value: Double(item.progress), total: 100)
                        .progressViewStyle(.linear)
                    Text("\(item.progress)%")
                        .font(.caption2)
                        .foregroundColor(.secondary)
                }

                if item.status == .queued {
                    Text("Waiting…")
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
            }

            Spacer()

            // Remove button (only for queued items)
            if item.status == .queued {
                Button {
                    viewModel.onRemoveFromQueue(item.path)
                } label: {
                    Image(systemName: "xmark")
                        .foregroundColor(.secondary)
                }
                .buttonStyle(.plain)
            }
        }
        .padding(.vertical, 2)
    }
}
