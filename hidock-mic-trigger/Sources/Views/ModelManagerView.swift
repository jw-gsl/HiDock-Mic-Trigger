import SwiftUI

/// Status of a single downloadable model.
struct ModelStatus: Identifiable {
    let id: String  // registry key
    var name: String
    var description: String
    var sizeMB: Int
    var installed: Bool
    var downloading: Bool = false
    var progress: Double = 0  // 0..1
    /// Pipeline stage key: "transcription", "diarization", "vad",
    /// "embedding", or "other".
    var stage: String = "other"
    /// User-facing stage section header: "Transcription (Speech → Text)" etc.
    var stageLabel: String = ""
    /// "pipeline" = user's primary choice (Transcription, Diarization).
    /// "supporting" = infrastructure backends pipeline stages depend on
    /// (VAD, Speaker Embeddings). Drives top-level UI grouping.
    var category: String = "pipeline"
    /// Human copy for supporting models explaining which pipeline
    /// stages consume them — e.g. "Built-in Lite diarizer (not used
    /// by Sortformer)". Empty string on pipeline-stage rows.
    var usedBy: String = ""
    /// Human copy for pipeline-stage rows explaining which supporting
    /// models they pull in — e.g. "Silero VAD + TitaNet" on the
    /// Lite diarizer.
    var dependsOn: String = ""
    /// Stable backend identifier within the stage — "whisper" / "parakeet"
    /// for transcription, "lite" / "sortformer" for diarization, etc.
    /// Used when the user picks a new active backend.
    var backendKey: String = ""
    /// True if this entry is the currently-active backend for its stage.
    /// Derived from pipeline_backends.json on the Python side.
    var active: Bool = false
    /// True if this is a prototype that may not run end-to-end yet
    /// (e.g. Parakeet until transcribe.py routes to it).
    var experimental: Bool = false
    /// True if this entry is code-only (no file download, always
    /// available) — e.g. the lite diarization pipeline.
    var builtIn: Bool = false
    /// True if this entry is installed via pip + uses HuggingFace's
    /// cache rather than MODELS_DIR — e.g. Sortformer via nemo-toolkit.
    var nemoModel: Bool = false
}

/// Format a model size in human-readable form — switches to GB once the
/// value crosses 1024 MB so we don't show users "1200 MB" when "1.2 GB"
/// reads more naturally.
func formatSize(mb: Int) -> String {
    if mb >= 1024 {
        let gb = Double(mb) / 1024.0
        // One decimal for sub-10 GB, whole number above.
        return gb < 10 ? String(format: "%.1f GB", gb) : "\(Int(gb.rounded())) GB"
    }
    return "\(mb) MB"
}

struct ModelManagerView: View {
    @ObservedObject var viewModel: HiDockViewModel

    var body: some View {
        VStack(spacing: 0) {
            // Header
            HStack {
                Text("Models")
                    .font(.title2)
                    .fontWeight(.semibold)
                Spacer()
                Button {
                    viewModel.onRefreshModelStatuses()
                } label: {
                    Image(systemName: "arrow.clockwise")
                }
                .buttonStyle(.borderless)
                .help("Refresh model statuses")
            }
            .padding(.horizontal, 20)
            .padding(.top, 16)
            .padding(.bottom, 12)

            Divider()

            // AI summariser engine — which CLI runs Summarise with AI / Ask AI.
            VStack(alignment: .leading, spacing: 4) {
                HStack {
                    Image(systemName: "sparkles").foregroundColor(.indigo)
                    Text("AI Summariser").fontWeight(.medium)
                    Spacer()
                    Picker("", selection: Binding(
                        get: { viewModel.summarizeEngine },
                        set: { viewModel.onSetSummarizeEngine($0) }
                    )) {
                        ForEach(viewModel.summarizeEngineChoices, id: \.id) { choice in
                            Text(choice.label).tag(choice.id)
                        }
                    }
                    .pickerStyle(.menu)
                    .fixedSize()
                }
                Text("Which CLI generates summaries and powers “Summarise with AI” / “Ask AI”. Uses your existing CLI login — no API keys.")
                    .font(.caption).foregroundColor(.secondary)

                Toggle("Show the CLI pane while summarising", isOn: Binding(
                    get: { viewModel.showCLIWhileSummarising },
                    set: { viewModel.onSetShowCLIWhileSummarising($0) }
                ))
                .toggleStyle(.checkbox)
                .padding(.top, 4)
                Text("When off, summaries run quietly in the background. The CLI button (bottom bar) still opens the pane for Ask AI or a one-time sign-in.")
                    .font(.caption).foregroundColor(.secondary)
            }
            .padding(.horizontal, 20)
            .padding(.vertical, 12)

            Divider()

            if viewModel.modelStatuses.isEmpty {
                VStack(spacing: 12) {
                    Spacer()
                    ProgressView()
                    Text("Loading model statuses...")
                        .foregroundColor(.secondary)
                    Spacer()
                }
            } else {
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 0, pinnedViews: []) {
                        categoryBlock(
                            title: "Pipeline Stages",
                            subtitle: "Your primary choices — what transforms audio into diarized transcripts.",
                            stages: pipelineStageOrder
                        )
                        categoryBlock(
                            title: "Supporting Models",
                            subtitle: "Infrastructure consumed by one or more pipeline backends. Each stage is also pick-one so alternatives can land later.",
                            stages: supportingStageOrder
                        )
                    }
                    .padding(.vertical, 8)
                }
            }
        }
        .frame(minWidth: 540, minHeight: 420)
    }

    /// Top-level categorisation. Pipeline stages are the user's direct
    /// backend choices; supporting stages hold infrastructure models
    /// that those backends depend on. Each category renders as a
    /// bold section header with a one-line explainer.
    private let pipelineStageOrder = ["transcription", "diarization"]
    private let supportingStageOrder = ["vad", "embedding"]

    /// Group model statuses by stage, keeping active entries first so
    /// the current selection is always at the top of each section.
    private var stageGroups: [String: [ModelStatus]] {
        var groups: [String: [ModelStatus]] = [:]
        for status in viewModel.modelStatuses.values {
            groups[status.stage, default: []].append(status)
        }
        for key in groups.keys {
            groups[key]?.sort { a, b in
                if a.active != b.active { return a.active }
                if a.builtIn != b.builtIn { return a.builtIn }
                return a.name < b.name
            }
        }
        return groups
    }

    @ViewBuilder
    private func categoryBlock(title: String, subtitle: String, stages: [String]) -> some View {
        let blockStages = stages.filter { (stageGroups[$0]?.isEmpty == false) }
        if !blockStages.isEmpty {
            HStack {
                Text(title)
                    .font(.title3)
                    .fontWeight(.semibold)
                Spacer()
            }
            .padding(.horizontal, 20)
            .padding(.top, 14)
            Text(subtitle)
                .font(.caption)
                .foregroundColor(.secondary)
                .padding(.horizontal, 20)
                .padding(.bottom, 4)
            Divider()
                .padding(.horizontal, 16)
            ForEach(blockStages, id: \.self) { stage in
                if let entries = stageGroups[stage] {
                    stageSection(stage: stage, entries: entries)
                }
            }
        }
    }

    @ViewBuilder
    private func stageSection(stage: String, entries: [ModelStatus]) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            // Section header shows the stage label + a count of how many
            // alternatives exist so the user sees at a glance that this
            // is a pick-one choice.
            HStack(alignment: .firstTextBaseline) {
                Text(entries.first?.stageLabel ?? stage.capitalized)
                    .font(.headline)
                Text(entries.count == 1 ? "" : " — pick one")
                    .font(.caption)
                    .foregroundColor(.secondary)
                Spacer()
            }
            .padding(.horizontal, 20)
            .padding(.top, 14)
            .padding(.bottom, 6)

            ForEach(entries) { status in
                ModelRowView(
                    status: status,
                    allowSelection: entries.count > 1,
                    onDownload: { viewModel.onDownloadModelByKey(status.id) },
                    onDelete: { viewModel.onDeleteModelByKey(status.id) },
                    onSetActive: { viewModel.onSetActiveModelByKey(status.id) }
                )
                Divider()
                    .padding(.horizontal, 16)
            }
        }
    }
}

struct ModelRowView: View {
    let status: ModelStatus
    /// True if this stage has multiple alternatives, so the row shows
    /// a radio-style selector. Stages with only one candidate (VAD,
    /// Voice Library) hide the picker and just show installed state.
    let allowSelection: Bool
    let onDownload: () -> Void
    let onDelete: () -> Void
    let onSetActive: () -> Void

    /// A radio-style indicator for which backend is active within a
    /// stage. Tapping a not-currently-active installed row promotes
    /// it. Not-installed rows can't be selected until downloaded.
    @ViewBuilder
    private var selector: some View {
        if allowSelection {
            Button {
                if status.installed && !status.active {
                    onSetActive()
                }
            } label: {
                Image(systemName: status.active
                      ? "largecircle.fill.circle"
                      : (status.installed ? "circle" : "circle.dashed"))
                    .font(.title2)
                    .foregroundColor(status.active ? .accentColor : (status.installed ? .secondary : .secondary.opacity(0.4)))
            }
            .buttonStyle(.plain)
            .disabled(!status.installed || status.active)
            .help(
                status.active
                    ? "Active — currently used for \(friendlyStage(status.stage))"
                    : (status.installed
                        ? "Set as active for \(friendlyStage(status.stage))"
                        : "Download first to select this backend")
            )
        } else {
            // Single-option stage: still show an installed/uninstalled
            // dot so the row shape is consistent.
            Image(systemName: status.installed ? "checkmark.circle.fill" : "arrow.down.circle")
                .font(.title2)
                .foregroundColor(status.installed ? .green : .secondary)
        }
    }

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            selector
                .frame(width: 28)
                .padding(.top, 2)

            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 6) {
                    Text(status.name)
                        .font(.headline)
                    if status.active && status.installed {
                        Text("ACTIVE")
                            .font(.caption2.weight(.bold))
                            .foregroundColor(.white)
                            .padding(.horizontal, 5)
                            .padding(.vertical, 1)
                            .background(Color.green, in: Capsule())
                    }
                    if status.builtIn {
                        Text("BUILT-IN")
                            .font(.caption2.weight(.bold))
                            .foregroundColor(.white)
                            .padding(.horizontal, 5)
                            .padding(.vertical, 1)
                            .background(Color.gray, in: Capsule())
                    }
                    if status.experimental {
                        Text("EXPERIMENTAL")
                            .font(.caption2.weight(.bold))
                            .foregroundColor(.white)
                            .padding(.horizontal, 5)
                            .padding(.vertical, 1)
                            .background(Color.orange, in: Capsule())
                    }
                    Spacer()
                    if !status.builtIn && status.sizeMB > 0 {
                        Text(formatSize(mb: status.sizeMB))
                            .font(.callout)
                            .foregroundColor(.secondary)
                    }
                }

                Text(status.description)
                    .font(.caption)
                    .foregroundColor(.secondary)

                // Make the stage-relationship explicit so the user can
                // see why a supporting model exists or which support
                // a pipeline backend needs.
                if !status.dependsOn.isEmpty {
                    Text("Uses: \(status.dependsOn)")
                        .font(.caption2.italic())
                        .foregroundColor(.secondary.opacity(0.85))
                }
                if !status.usedBy.isEmpty {
                    Text("Used by: \(status.usedBy)")
                        .font(.caption2.italic())
                        .foregroundColor(.secondary.opacity(0.85))
                }

                if status.downloading {
                    ProgressView(value: status.progress)
                        .progressViewStyle(.linear)
                    Text("Downloading... \(Int(status.progress * 100))%")
                        .font(.caption2)
                        .foregroundColor(.secondary)
                }
            }

            VStack {
                if status.builtIn {
                    // Always available; nothing to download or delete.
                    Text("Always on")
                        .font(.caption)
                        .foregroundColor(.secondary)
                } else if status.downloading {
                    ProgressView()
                        .controlSize(.small)
                } else if status.installed {
                    VStack(spacing: 4) {
                        Text("Installed")
                            .font(.caption)
                            .foregroundColor(.green)
                        // Deleting the active backend would leave the
                        // pipeline broken; gate deletion behind "active
                        // is somewhere else" to prevent a foot-gun.
                        Button(role: .destructive) {
                            onDelete()
                        } label: {
                            Text("Delete")
                                .font(.caption)
                        }
                        .buttonStyle(.bordered)
                        .controlSize(.small)
                        .disabled(status.active)
                        .help(status.active
                              ? "Can't delete the active backend — pick a different one first"
                              : "Remove this model from disk")
                    }
                } else {
                    Button {
                        onDownload()
                    } label: {
                        Text(status.nemoModel ? "Install" : "Download")
                            .font(.caption)
                    }
                    .buttonStyle(.bordered)
                    .controlSize(.small)
                }
            }
            .frame(width: 90)
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 12)
    }
}

private func friendlyStage(_ stage: String) -> String {
    switch stage {
    case "transcription": return "Transcription"
    case "diarization":   return "Speaker Diarization"
    case "vad":           return "Voice Activity Detection"
    case "voice_library": return "Voice Library"
    default:              return stage.capitalized
    }
}

