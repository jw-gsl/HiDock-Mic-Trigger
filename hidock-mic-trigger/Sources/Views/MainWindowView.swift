import SwiftUI

struct MainWindowView: View {
    @ObservedObject var viewModel: HiDockViewModel

    var body: some View {
        VStack(spacing: 4) {
            MicTriggerSection(viewModel: viewModel)
            SyncHeaderSection(viewModel: viewModel)
            SyncToolbarSection(viewModel: viewModel)
            DownloadProgressBar(viewModel: viewModel)
            TranscriptionProgressBar(viewModel: viewModel)
            RecordingsTableView(viewModel: viewModel)

            // Footer
            HStack {
                if !viewModel.updateStatusText.isEmpty {
                    Label {
                        Text(viewModel.updateStatusText)
                            .font(.caption)
                    } icon: {
                        Image(systemName: "arrow.down.circle")
                            .foregroundColor(.blue)
                    }
                    .font(.caption)
                    .foregroundColor(.secondary)
                }

                Spacer()

                Menu {
                    Button {
                        viewModel.onToggleNotifyTranscription()
                    } label: {
                        Label(
                            "Transcription Complete",
                            systemImage: viewModel.notifyTranscriptionComplete ? "checkmark.circle.fill" : "circle"
                        )
                    }
                    Button {
                        viewModel.onToggleNotifyDownload()
                    } label: {
                        Label(
                            "Download Complete",
                            systemImage: viewModel.notifyDownloadComplete ? "checkmark.circle.fill" : "circle"
                        )
                    }
                    Button {
                        viewModel.onToggleNotifyMicChanges()
                    } label: {
                        Label(
                            "Mic Changes",
                            systemImage: viewModel.notifyMicChanges ? "checkmark.circle.fill" : "circle"
                        )
                    }
                } label: {
                    Label("Notifications", systemImage: "bell")
                        .font(.caption)
                }
                .menuStyle(.borderlessButton)
                .fixedSize()
                .help("Notification preferences")

                Menu {
                    Button {
                        viewModel.onSetAppearance("auto")
                    } label: {
                        Label("Auto (System)", systemImage: "circle.lefthalf.filled")
                    }
                    Button {
                        viewModel.onSetAppearance("dark")
                    } label: {
                        Label("Dark", systemImage: "moon.fill")
                    }
                    Button {
                        viewModel.onSetAppearance("light")
                    } label: {
                        Label("Light", systemImage: "sun.max.fill")
                    }
                } label: {
                    Label(viewModel.appearanceLabel, systemImage: viewModel.appearanceIcon)
                        .font(.caption)
                }
                .menuStyle(.borderlessButton)
                .fixedSize()
                .help("Change appearance")

                Button {
                    viewModel.onShowCoworkPrompt()
                } label: {
                    Label("Cowork", systemImage: "sparkles")
                        .font(.caption)
                }
                .buttonStyle(.bordered)
                .controlSize(.small)

                Button {
                    viewModel.onCheckForUpdates()
                } label: {
                    Label("Check for Updates", systemImage: "arrow.triangle.2.circlepath")
                        .font(.caption)
                }
                .buttonStyle(.bordered)
                .controlSize(.small)

                Button {
                    viewModel.onShowDeviceManager()
                } label: {
                    Label("Devices", systemImage: "externaldrive.connected.to.line.below")
                        .font(.caption)
                }
                .buttonStyle(.bordered)
                .controlSize(.small)

                Button {
                    viewModel.onShowModelManager()
                } label: {
                    Label("Models", systemImage: "square.and.arrow.down")
                        .font(.caption)
                }
                .buttonStyle(.bordered)
                .controlSize(.small)

                Button {
                    viewModel.onShowVoiceLibrary()
                } label: {
                    Label("Voice Library", systemImage: "person.2")
                        .font(.caption)
                }
                .buttonStyle(.bordered)
                .controlSize(.small)

                Button {
                    viewModel.onShowFeedbackHistory()
                } label: {
                    Label("My Feedback", systemImage: "list.bullet.clipboard")
                        .font(.caption)
                }
                .buttonStyle(.bordered)
                .controlSize(.small)

                Button {
                    viewModel.onSendFeedback()
                } label: {
                    Label("Send Feedback", systemImage: "bubble.left.and.text.bubble.right")
                        .font(.caption)
                }
                .buttonStyle(.bordered)
                .controlSize(.small)
            }
            .padding(.horizontal, 16)
            .padding(.bottom, 8)
            .padding(.top, 2)
        }
        .frame(minWidth: 980, minHeight: 510)
        .sheet(isPresented: $viewModel.showOnboarding) {
            OnboardingView(viewModel: viewModel)
        }
    }
}

struct DownloadProgressBar: View {
    @ObservedObject var viewModel: HiDockViewModel

    var body: some View {
        if viewModel.syncDownloading {
            HStack(spacing: 8) {
                ProgressView()
                    .controlSize(.small)
                Text(viewModel.syncDownloadProgress ?? "Downloading...")
                    .font(.caption)
                    .foregroundColor(.secondary)
                Spacer()
                Button(role: .destructive) {
                    viewModel.onStopDownload()
                } label: {
                    Label("Stop", systemImage: "stop.fill")
                        .font(.caption)
                }
                .buttonStyle(.bordered)
                .tint(.red)
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 6)
            .background(.ultraThinMaterial)
        }
    }
}

struct TranscriptionProgressBar: View {
    @ObservedObject var viewModel: HiDockViewModel

    /// Build a single consolidated status string:
    ///   "Transcribing 2/5 — 42% · Diarizing speakers 3/5"
    /// Prefix shows file progress through the queue + overall percent.
    /// Suffix (optional) shows the current pipeline stage when the
    /// transcription script has reported one via a STAGE: line.
    private var statusText: String {
        let prefix: String
        if viewModel.transcriptionFileCount > 1 {
            prefix = "Transcribing \(viewModel.transcriptionFileIndex + 1)/\(viewModel.transcriptionFileCount) — \(viewModel.transcriptionProgress)%"
        } else {
            prefix = "Transcribing — \(viewModel.transcriptionProgress)%"
        }
        if !viewModel.transcriptionStatus.isEmpty {
            return "\(prefix) · \(viewModel.transcriptionStatus)"
        }
        return prefix
    }

    var body: some View {
        if viewModel.transcriptionBusy {
            HStack(spacing: 8) {
                ProgressView(value: Double(viewModel.transcriptionProgress), total: 100)
                    .frame(width: 120)

                Text(statusText)
                    .font(.caption)
                    .foregroundColor(.secondary)
                    .lineLimit(1)
                    .truncationMode(.tail)

                Spacer()

                Button(role: .destructive) {
                    viewModel.onCancelTranscription()
                } label: {
                    Label("Cancel", systemImage: "xmark.circle")
                        .font(.caption)
                }
                .buttonStyle(.bordered)
                .tint(.red)
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 6)
            .background(.ultraThinMaterial)
        }
    }
}
