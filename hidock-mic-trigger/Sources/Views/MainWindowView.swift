import SwiftUI

struct MainWindowView: View {
    @ObservedObject var viewModel: HiDockViewModel

    var body: some View {
        HStack(spacing: 0) {
            // Expand to fill all slack so the main content sits flush against
            // the detail pane — no empty gutter between them.
            mainColumn
                .frame(maxWidth: .infinity, alignment: .leading)
            if viewModel.detailPaneVisible {
                Divider()
                detailPane
                    .frame(width: 460)
                    .transition(.move(edge: .trailing))
            }
        }
        .frame(minWidth: viewModel.detailPaneVisible ? 1220 : 980, minHeight: 510)
        .sheet(isPresented: $viewModel.showOnboarding) {
            OnboardingView(viewModel: viewModel)
        }
    }

    /// The right-hand pane: a tab strip over the CLI + any hosted windows
    /// (transcripts, summaries, tool views).
    private var detailPane: some View {
        VStack(spacing: 0) {
            detailTabStrip
            Divider()
            detailContent
        }
    }

    private var detailTabStrip: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 4) {
                if viewModel.cliPaneVisible {
                    detailTabChip(id: "cli", title: cliTabTitle, icon: "terminal") {
                        viewModel.cliPaneVisible = false
                        if viewModel.activeDetailTabId == "cli" {
                            viewModel.activeDetailTabId = viewModel.detailTabs.last?.id ?? "cli"
                        }
                    }
                }
                ForEach(viewModel.detailTabs) { tab in
                    detailTabChip(id: tab.id, title: tab.title, icon: tab.icon) {
                        viewModel.closeDetailTab(tab.id)
                    }
                }
            }
            .padding(6)
        }
        .background(.ultraThinMaterial)
    }

    private func detailTabChip(id: String, title: String, icon: String, onClose: @escaping () -> Void) -> some View {
        let active = viewModel.activeDetailTabId == id
        return HStack(spacing: 4) {
            Image(systemName: icon).font(.system(size: 10))
            Text(title).font(.caption).lineLimit(1)
            Button(action: onClose) {
                Image(systemName: "xmark").font(.system(size: 8, weight: .bold))
            }
            .buttonStyle(.plain)
            .foregroundColor(.secondary)
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 4)
        .background(active ? Color.accentColor.opacity(0.22) : Color.secondary.opacity(0.08),
                    in: RoundedRectangle(cornerRadius: 6))
        .frame(maxWidth: 170)
        .contentShape(Rectangle())
        .onTapGesture { viewModel.activeDetailTabId = id }
    }

    @ViewBuilder private var detailContent: some View {
        if viewModel.activeDetailTabId == "cli", viewModel.cliPaneVisible {
            cliPane
        } else if let tab = viewModel.detailTabs.first(where: { $0.id == viewModel.activeDetailTabId }) {
            tab.content
        } else if viewModel.cliPaneVisible {
            cliPane
        } else if let first = viewModel.detailTabs.first {
            first.content
        } else {
            Color.clear
        }
    }

    private var cliTabTitle: String {
        switch viewModel.cliPaneMode {
        case .summary: return "Summary"
        case .chat: return "Ask AI"
        case .terminal: return "Terminal"
        }
    }

    /// The right-hand pane content, chosen by the current mode. Auth / template
    /// authoring use the raw terminal; summarise and Ask AI use the formatted
    /// views.
    @ViewBuilder private var cliPane: some View {
        switch viewModel.cliPaneMode {
        case .summary:
            SummaryReadoutPane(
                transcript: viewModel.summaryTranscript,
                onOpenRawTerminal: { viewModel.onOpenRawTerminal() },
                onClose: { viewModel.cliPaneVisible = false }
            )
        case .chat:
            AgentChatView(viewModel: viewModel, onClose: { viewModel.cliPaneVisible = false })
        case .terminal:
            EmbeddedTerminalPane(
                controller: viewModel.terminalController,
                onClose: { viewModel.cliPaneVisible = false }
            )
        }
    }

    private var mainColumn: some View {
        VStack(spacing: 4) {
            MicTriggerSection(viewModel: viewModel)
            SyncHeaderSection(viewModel: viewModel)
            SyncToolbarSection(viewModel: viewModel)
            DownloadProgressBar(viewModel: viewModel)
            TranscriptionProgressBar(viewModel: viewModel)
            TrimProgressBar(viewModel: viewModel)
            RecordingsTableView(viewModel: viewModel)

            // Footer
            HStack {
                // Refresh moved here from the action toolbar in 2026-04-26
                // restructure — re-probing devices is a maintenance action,
                // not a primary verb in the workflow, so it lives next to
                // the other footer affordances (notifications, appearance)
                // rather than competing with Import / Transcribe / Merge.
                Button {
                    viewModel.onRefreshSync()
                } label: {
                    Label("Refresh", systemImage: "arrow.clockwise")
                        .font(.caption)
                }
                .buttonStyle(.borderless)
                .disabled(viewModel.syncBusy)
                .help("Probe paired devices for fresh status.")

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

                // Bottom-bar CLI toggle — opens/closes the embedded
                // terminal pane (Claude Code + summarise activity).
                Button {
                    viewModel.cliPaneVisible.toggle()
                } label: {
                    Label("CLI", systemImage: "terminal")
                        .font(.caption)
                }
                .buttonStyle(.bordered)
                .controlSize(.small)
                .tint(viewModel.cliPaneVisible ? .accentColor : nil)
                .help("Show/hide the embedded CLI pane — runs Ask AI and shows live summarise activity")

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
                    viewModel.onShowTemplatesManager()
                } label: {
                    Label("Templates", systemImage: "doc.text")
                        .font(.caption)
                }
                .buttonStyle(.bordered)
                .controlSize(.small)
                .help("Manage summary templates — import, edit/iterate with AI, delete")

                Button {
                    viewModel.onShowVoiceLibrary()
                } label: {
                    Label("Voice Library", systemImage: "person.2")
                        .font(.caption)
                }
                .buttonStyle(.bordered)
                .controlSize(.small)

                // Feedback — one button, dropdown for Send / My Feedback.
                Menu {
                    Button {
                        viewModel.onSendFeedback()
                    } label: {
                        Label("Send Feedback", systemImage: "bubble.left.and.text.bubble.right")
                    }
                    Button {
                        viewModel.onShowFeedbackHistory()
                    } label: {
                        Label("My Feedback", systemImage: "list.bullet.clipboard")
                    }
                } label: {
                    Label("Feedback", systemImage: "bubble.left.and.text.bubble.right")
                        .font(.caption)
                }
                .menuStyle(.borderlessButton)
                .controlSize(.small)
                .fixedSize()
            }
            .padding(.horizontal, 16)
            .padding(.bottom, 8)
            .padding(.top, 2)
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
    ///   "<Rec52> 2/5 — 42% · Diarizing speakers"
    /// Prefix leads with the current filename (stem, no extension) plus
    /// queue position + percent. Suffix shows the pipeline stage when the
    /// transcription script reports one — suppressed when the stage is
    /// "Transcribing" because the progress bar already conveys that
    /// implicitly.
    private var statusText: String {
        let stem: String
        if let name = viewModel.transcriptionCurrentFile, !name.isEmpty {
            stem = (name as NSString).deletingPathExtension
        } else {
            stem = "—"
        }
        let prefix: String
        if viewModel.transcriptionFileCount > 1 {
            prefix = "\(stem) \(viewModel.transcriptionFileIndex + 1)/\(viewModel.transcriptionFileCount) — \(viewModel.transcriptionProgress)%"
        } else {
            prefix = "\(stem) — \(viewModel.transcriptionProgress)%"
        }
        let stage = viewModel.transcriptionStatus
        if !stage.isEmpty && stage.lowercased() != "transcribing" {
            return "\(prefix) · \(stage)"
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

/// Compact, persistent trim-in-progress indicator. Lives in the same
/// bottom strip as DownloadProgressBar and TranscriptionProgressBar so
/// there's one consistent place for "something is happening". Replaces
/// the old transient syncStatus "Trimming…" text that popped above
/// Download Selected — that slot was cramped and noisy, and several
/// other status lines competed for it.
struct TrimProgressBar: View {
    @ObservedObject var viewModel: HiDockViewModel

    var body: some View {
        if viewModel.trimBusy {
            HStack(spacing: 8) {
                ProgressView()
                    .controlSize(.small)

                Text("Trimming…")
                    .font(.caption)
                    .foregroundColor(.secondary)

                Spacer()
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 6)
            .background(.ultraThinMaterial)
        }
    }
}
