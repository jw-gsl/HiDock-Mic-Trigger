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
    /// Candidate identity models can generate human-review suggestions but
    /// are never allowed to write speaker names automatically.
    var reviewOnly: Bool = false
    /// True if this entry is listed for visibility only — no runtime
    /// integration exists yet, so it must stay unselectable (e.g. the
    /// W2V-BERT 2.0 speaker model ahead of the next bake-off).
    var planned: Bool = false
    /// Optional key into the Python capability preflight
    /// (`models.py capability <key>`). When set, the row offers a
    /// "Check compatibility" action.
    var capability: String? = nil
    /// Whether this model's licence permits shipping it in a distributed build.
    /// `nil` means unverified, which is shown as such — for a shipping decision
    /// "we don't know" and "it's fine" must not look alike.
    var distributable: Bool? = nil
    /// The licence itself, for the badge's tooltip.
    var licence: String? = nil
    /// True when downloading or running this model authenticates with a Hugging
    /// Face token. Only pyannote's diarizer is gated today.
    var gated: Bool = false

    /// Short badge text for the licence, or nil when there is nothing to say.
    /// Only models with a known licence position get a badge; a model outside
    /// the registry stays silent rather than claiming to be either safe or not.
    var licenceBadge: (text: String, safe: Bool)? {
        switch distributable {
        case .some(false): return ("Personal use only", false)
        case .some(true): return ("Distributable", true)
        case .none: return nil
        }
    }
}

/// One check from a `models.py capability <key>` preflight report.
struct CapabilityCheck {
    var name: String
    /// "pass" | "warn" | "fail" | "info" — warn/info never block.
    var status: String
    var detail: String
    /// Remediation hint shown when non-empty (e.g. "pip install torch").
    var fix: String
}

/// Parsed capability-preflight report for a planned model — per-check
/// results plus an overall can-run flag.
struct ModelCapabilityReport {
    var canRun: Bool
    var checks: [CapabilityCheck]
    /// Set when the CLI returned {"error": ...} (e.g. unknown key).
    var error: String?

    init?(json: [String: Any]) {
        if let error = json["error"] as? String {
            self.canRun = false
            self.checks = []
            self.error = error
            return
        }
        guard let canRun = json["can_run"] as? Bool,
              let rawChecks = json["checks"] as? [[String: Any]] else { return nil }
        self.canRun = canRun
        self.error = nil
        self.checks = rawChecks.map { check in
            CapabilityCheck(
                name: check["name"] as? String ?? "",
                status: check["status"] as? String ?? "info",
                detail: check["detail"] as? String ?? "",
                fix: check["fix"] as? String ?? ""
            )
        }
    }
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

/// Models vs the settings that merely lived on the same page.
///
/// AI Summariser, Hugging Face access, and Calendar are not models. They occupied
/// the first screen and pushed the actual pipeline below the fold, which is most
/// of why the page read as cluttered. Split out, Models answers exactly one
/// question: what is in the pipeline.
enum ModelManagerTab: String, CaseIterable, Identifiable {
    case models, settings
    var id: String { rawValue }
    var label: String { self == .models ? "Models" : "Settings" }
}

struct ModelManagerView: View {
    @ObservedObject var viewModel: HiDockViewModel
    /// Stages are collapsed by default; this holds the expanded ones.
    @State private var expandedStages: Set<String> = []
    @State private var tab: ModelManagerTab = .models
    /// Model rows whose description/provenance block is showing. Collapsed by
    /// default: twelve rows of 2–4 line descriptions buried the six words that
    /// actually matter, which is which one is on.
    @State private var expandedRows: Set<String> = []

    /// Every stage currently having at least one registered model.
    private var allStageKeys: Set<String> {
        Set((pipelineStageOrder + supportingStageOrder).filter {
            stageGroups[$0]?.isEmpty == false
        })
    }

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Text(tab.label)
                    .font(.title2)
                    .fontWeight(.semibold)
                Spacer()
                if tab == .models {
                    Button("Expand all") { expandedStages = allStageKeys }
                        .buttonStyle(.borderless)
                        .font(.caption)
                        .help("Expand every pipeline stage")
                    Button("Collapse all") { expandedStages = [] }
                        .buttonStyle(.borderless)
                        .font(.caption)
                        .help("Collapse every pipeline stage")
                }
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
            .padding(.bottom, 10)

            Picker("", selection: $tab) {
                ForEach(ModelManagerTab.allCases) { Text($0.label).tag($0) }
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .padding(.horizontal, 20)
            .padding(.bottom, 10)

            Divider()

            if tab == .settings {
                settingsTab
            } else {
                modelsTab
            }
        }
        .frame(minWidth: 360, minHeight: 300)   // hosted in the resizable detail pane (min 480 wide)
    }

    @ViewBuilder
    private var settingsTab: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
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

                // Hugging Face access — required only for *gated* models. pyannote's
                // diarizer is gated: accepting the licence grants your account
                // access, but a download still has to authenticate as you, so a
                // token is needed as well. Stored in the Keychain, never on disk,
                // and handed to the pipeline through the subprocess environment.
                huggingFaceSection

                Divider()

                // Calendar provider — meeting context (attendees) used for
                // speaker merging and suggestion narrowing.
                VStack(alignment: .leading, spacing: 4) {
                    HStack {
                        Image(systemName: "calendar").foregroundColor(.teal)
                        Text("Calendar").fontWeight(.medium)
                        Spacer()
                        Picker("", selection: Binding(
                            get: { viewModel.calendarProvider },
                            set: { viewModel.onSetCalendarProvider($0) }
                        )) {
                            ForEach(viewModel.calendarProviderChoices, id: \.id) { choice in
                                Text(choice.label).tag(choice.id)
                            }
                        }
                        .pickerStyle(.menu)
                        .fixedSize()
                    }
                    Text(calendarExplainer)
                        .font(.caption).foregroundColor(.secondary)
                }
                .padding(.horizontal, 20)
                .padding(.vertical, 12)
            }
        }
    }

    @ViewBuilder
    private var modelsTab: some View {
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
                        subtitle: "What transforms audio into diarized transcripts.",
                        stages: pipelineStageOrder
                    )
                    categoryBlock(
                        title: "Supporting Models",
                        subtitle: "Infrastructure the pipeline backends depend on.",
                        stages: supportingStageOrder
                    )
                }
                .padding(.vertical, 8)
            }
        }
    }

    /// Top-level categorisation. Pipeline stages are the user's direct
    /// backend choices; supporting stages hold infrastructure models
    /// that those backends depend on. Each category renders as a
    /// bold section header with a one-line explainer.
    @State private var huggingFaceTokenEntry: String = ""
    @State private var huggingFaceStatus: String = ""
    /// The stored token's redacted form, read from the Keychain **once**.
    ///
    /// `HuggingFaceToken.isConfigured` and `.redacted()` each perform their own
    /// `SecItemCopyMatching`, and both were called straight from the view body —
    /// four Keychain reads per render, re-run on every state change. When the
    /// item's ACL does not match the running app (an item created by an earlier,
    /// differently-signed build), macOS prompts on *each* read, so the app asked
    /// for Keychain access again and again. Caching makes it one read per open,
    /// refreshed only when this view actually changes the token.
    @State private var huggingFaceRedacted: String?
    @State private var huggingFaceLoaded = false
    /// False when the stored token can only be read by prompting — checked
    /// without prompting, via kSecUseAuthenticationUIFail.
    @State private var huggingFaceReadable = true

    private let pipelineStageOrder = ["transcription", "diarization"]
    private let supportingStageOrder = ["vad", "embedding", "identity_review"]


    // MARK: - Hugging Face access (gated models)

    /// Two steps, in the order they must happen: accept the licence, then store
    /// a token. Showing both explicitly matters because either one missing
    /// produces the same 401, and the failure otherwise looks like a bug.
    @ViewBuilder
    private var huggingFaceSection: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Image(systemName: "key.horizontal").foregroundColor(.orange)
                Text("Hugging Face access").fontWeight(.medium)
                Spacer()
                if let redacted = huggingFaceRedacted {
                    Label(redacted, systemImage: "checkmark.seal.fill")
                        .font(.caption)
                        .foregroundColor(.green)
                } else {
                    Label("not set", systemImage: "exclamationmark.triangle")
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
            }

            Text("Only needed for gated models. pyannote's diarizer requires both steps below — accepting the licence is free for research and commercial use.")
                .font(.caption).foregroundColor(.secondary)

            HStack(spacing: 8) {
                Text("1.").font(.caption.monospaced()).foregroundColor(.secondary)
                Button {
                    NSWorkspace.shared.open(HuggingFaceToken.licenceURL)
                } label: {
                    Label("Accept the model licence", systemImage: "arrow.up.forward.square")
                }
                .help("Opens the pyannote community-1 model page — accept the terms with your Hugging Face account")
                Spacer()
            }

            HStack(spacing: 8) {
                Text("2.").font(.caption.monospaced()).foregroundColor(.secondary)
                Button {
                    NSWorkspace.shared.open(HuggingFaceToken.tokenSettingsURL)
                } label: {
                    Label("Create a read token", systemImage: "arrow.up.forward.square")
                }
                .help("A read-scoped token is sufficient")
                Spacer()
            }

            HStack(spacing: 8) {
                Text("3.").font(.caption.monospaced()).foregroundColor(.secondary)
                if let redacted = huggingFaceRedacted {
                    // A stored token is a settled state, so show it as one. An
                    // always-live entry field invited typing a second token over
                    // a working one with no indication of which would win —
                    // Remove first is an explicit, reversible decision.
                    HStack(spacing: 6) {
                        Image(systemName: "lock.fill")
                            .font(.caption)
                            .foregroundColor(.secondary)
                        Text(redacted)
                            .font(.caption.monospaced())
                            .foregroundColor(.secondary)
                    }
                    .padding(.horizontal, 8)
                    .padding(.vertical, 4)
                    .frame(maxWidth: 260, alignment: .leading)
                    .background(
                        RoundedRectangle(cornerRadius: 5)
                            .fill(Color.secondary.opacity(0.08))
                    )
                    .help("Stored in your Keychain. Remove it to enter a different token.")
                    Button("Remove") {
                        HuggingFaceToken.delete()
                        huggingFaceTokenEntry = ""
                        huggingFaceRedacted = nil
                        huggingFaceStatus = "Token removed."
                    }
                    .help("Delete the stored token from your Keychain so a new one can be entered")
                } else {
                    // SecureField so the credential is never rendered, screenshotted,
                    // or captured in a screen recording.
                    SecureField("hf_…", text: $huggingFaceTokenEntry)
                        .textFieldStyle(.roundedBorder)
                        .frame(maxWidth: 260)
                    Button("Save") {
                        do {
                            try HuggingFaceToken.save(huggingFaceTokenEntry)
                            huggingFaceTokenEntry = ""
                            // Saving re-creates the item, so its ACL is bound to
                            // the app doing the saving. This is also the cure for
                            // an item stranded by an earlier build's signature:
                            // Remove then Save, and the prompts stop.
                            huggingFaceRedacted = HuggingFaceToken.redacted()
                            huggingFaceReadable = HuggingFaceToken.isReadableWithoutPrompting()
                            huggingFaceStatus = "Token saved to your Keychain."
                        } catch {
                            huggingFaceStatus = error.localizedDescription
                        }
                    }
                    .disabled(huggingFaceTokenEntry.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                }
                Spacer()
            }

            // A token whose access list no longer matches this build reads fine —
            // but only by asking permission every time, which cannot be fixed
            // from the Keychain side. Say so plainly and name the remedy, rather
            // than leaving the prompts unexplained.
            if huggingFaceRedacted != nil && !huggingFaceReadable {
                HStack(alignment: .top, spacing: 6) {
                    Image(systemName: "exclamationmark.triangle.fill")
                        .font(.caption).foregroundColor(.orange)
                    VStack(alignment: .leading, spacing: 2) {
                        Text("macOS asks permission every time this token is read.")
                            .font(.caption)
                        Text("It was saved by an earlier build, so it is no longer tied to this "
                             + "app. Remove it and save it again to stop the prompts.")
                            .font(.caption2)
                            .foregroundColor(.secondary)
                    }
                }
                .padding(.top, 2)
            }

            if !huggingFaceStatus.isEmpty {
                Text(huggingFaceStatus)
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 12)
        .onAppear {
            // Exactly one Keychain read per time this page is opened. Guarded so
            // a re-appear (tab switch, window refocus) does not read again.
            guard !huggingFaceLoaded else { return }
            huggingFaceLoaded = true
            huggingFaceReadable = HuggingFaceToken.isReadableWithoutPrompting()
            huggingFaceRedacted = HuggingFaceToken.redacted()
        }
    }

    /// Explainer under the Calendar provider picker — honest about the app
    /// not being able to start the provider's sign-in itself.
    private var calendarExplainer: String {
        switch viewModel.calendarProvider {
        case "microsoft365":
            return "Attendee lists from the Microsoft 365 connector narrow speaker merging and suggestions. Connect it in your MCP client (e.g. Claude's Microsoft 365 connector) — the app can't start the sign-in for you — then events flow in via calendar-context."
        case "google":
            return "Attendee lists from a Google Calendar MCP narrow speaker merging and suggestions. Connect it in your MCP client (e.g. @cocal/google-calendar-mcp) — the app can't start the sign-in for you — then events flow in via calendar-context."
        default:
            return "Calendar context is off. Speaker merging and suggestions won't use attendee lists."
        }
    }

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

    /// Licence status as a badge. Read from `shared/models.py`'s `distributable`
    /// field, which already models this — it was only ever reachable by reading a
    /// paragraph of description text, despite being the fact most likely to cause
    /// harm if missed.
    private func licencePill(_ text: String, safe: Bool, licence: String?) -> some View {
        Text(text)
            .font(.system(size: 9, weight: .semibold))
            .padding(.horizontal, 5)
            .padding(.vertical, 1)
            .background(Capsule().fill((safe ? Color.secondary : Color.orange).opacity(0.18)))
            .foregroundColor(safe ? .secondary : .orange)
            .help(licence.map { "\($0). " + (safe
                ? "Safe to include in a distributed build."
                : "Local personal use only — must never ship in a distributed build.") }
                ?? text)
    }

    private func modelRow(_ status: ModelStatus, stageEntryCount: Int) -> some View {
        ModelRowView(
            status: status,
            allowSelection: stageEntryCount > 1,
            expanded: expandedRows.contains(status.id),
            onToggleExpanded: {
                if expandedRows.contains(status.id) { expandedRows.remove(status.id) }
                else { expandedRows.insert(status.id) }
            },
            capabilityReport: viewModel.modelCapabilities[status.id],
            capabilityChecking: viewModel.modelCapabilityChecking.contains(status.id),
            onDownload: { viewModel.onDownloadModelByKey(status.id) },
            onDelete: { viewModel.onDeleteModelByKey(status.id) },
            onSetActive: { viewModel.onSetActiveModelByKey(status.id) },
            onCheckCapability: { viewModel.onCheckModelCapability(status.id) }
        )
    }

    @ViewBuilder
    private func stageSection(stage: String, entries: [ModelStatus]) -> some View {
        let expanded = expandedStages.contains(stage)
        VStack(alignment: .leading, spacing: 0) {
            // Section header: chevron toggle + stage label. Collapsed rows
            // still name the active backend so the current selection is
            // visible without expanding.
            Button {
                if expanded { expandedStages.remove(stage) } else { expandedStages.insert(stage) }
            } label: {
                let selected = entries.first(where: { $0.active })
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    Image(systemName: expanded ? "chevron.down" : "chevron.right")
                        .font(.caption.weight(.semibold))
                        .foregroundColor(.secondary)
                        .frame(width: 12)
                    Text(entries.first?.stageLabel ?? stage.capitalized)
                        .font(.subheadline)
                        .foregroundColor(.secondary)
                    Spacer(minLength: 8)
                    // The selected model is the answer this row exists to give,
                    // so it is the loudest thing on it. "— pick one" used to sit
                    // where the stage label is and read as an outstanding task
                    // even on stages that already had a selection.
                    if let selected {
                        if let badge = selected.licenceBadge, !badge.safe {
                            licencePill(badge.text, safe: false, licence: selected.licence)
                        }
                        Text(selected.name)
                            .font(.headline)
                            .lineLimit(1)
                            .truncationMode(.tail)
                    } else {
                        Text("None selected")
                            .font(.headline)
                            .foregroundColor(.orange)
                    }
                }
                .padding(.horizontal, 20)
                .padding(.top, 14)
                .padding(.bottom, 6)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .help(expanded ? "Collapse \(entries.first?.stageLabel ?? stage)" : "Expand \(entries.first?.stageLabel ?? stage)")

            if expanded {
                // Planned models can never be chosen, so they are separated out
                // rather than presented alongside real options with a radio
                // button they cannot honour.
                let choosable = entries.filter { !$0.planned }
                let notYet = entries.filter { $0.planned }
                ForEach(choosable) { status in
                    modelRow(status, stageEntryCount: choosable.count)
                    Divider().padding(.horizontal, 16)
                }
                if !notYet.isEmpty {
                    Text("Not yet available")
                        .font(.caption.weight(.semibold))
                        .foregroundColor(.secondary)
                        .padding(.horizontal, 20)
                        .padding(.top, 8)
                        .padding(.bottom, 2)
                    ForEach(notYet) { status in
                        modelRow(status, stageEntryCount: choosable.count)
                        Divider().padding(.horizontal, 16)
                    }
                }
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
    /// True when this row's description and provenance block is showing.
    let expanded: Bool
    let onToggleExpanded: () -> Void
    /// Latest capability-preflight report for this row, if the user has
    /// run "Check compatibility". Rendered inline under the description.
    let capabilityReport: ModelCapabilityReport?
    /// True while `models.py capability <key>` is running for this row.
    let capabilityChecking: Bool
    let onDownload: () -> Void
    let onDelete: () -> Void
    let onSetActive: () -> Void
    let onCheckCapability: () -> Void

    /// A radio-style indicator for which backend is active within a
    /// stage. Tapping a not-currently-active installed row promotes
    /// it. Not-installed rows can't be selected until downloaded.
    /// Planned rows are never selectable — no runtime integration
    /// exists yet, so promoting one would break the stage.
    @ViewBuilder
    private var selector: some View {
        if allowSelection {
            Button {
                if status.installed && !status.active && !status.planned {
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
            .disabled(status.planned || !status.installed || status.active)
            .help(
                status.planned
                    ? "Planned for the next model bake-off — not yet integrated into live review"
                    : (status.active
                        ? "Active — currently used for \(friendlyStage(status.stage))"
                        : (status.installed
                            ? "Set as active for \(friendlyStage(status.stage))"
                            : "Download first to select this backend"))
            )
        } else {
            // Single-option stage. Same radio vocabulary as everywhere else: a
            // green tick here made Speaker Embeddings look like a different kind
            // of control, so it was unclear whether it was even choosable.
            Image(systemName: status.active ? "largecircle.fill.circle"
                  : (status.installed ? "circle" : "circle.dashed"))
                .font(.title2)
                .foregroundColor(status.active ? .accentColor : .secondary.opacity(status.installed ? 1 : 0.4))
                .help(status.active
                      ? "Active — the only backend for \(friendlyStage(status.stage))"
                      : "The only backend for \(friendlyStage(status.stage))")
        }
    }

    private func tagPill(_ text: String, _ color: Color) -> some View {
        Text(text)
            .font(.caption2.weight(.bold))
            .foregroundColor(.white)
            .padding(.horizontal, 5)
            .padding(.vertical, 1)
            .background(color, in: Capsule())
    }

    /// Licence status badge. Deliberately quieter than the tag pills — it is a
    /// constraint to notice, not a label to shout — but always present on the
    /// collapsed row, because "may this ship?" should never need a click.
    private func licencePill(_ text: String, safe: Bool, licence: String?) -> some View {
        Text(text)
            .font(.system(size: 9, weight: .semibold))
            .padding(.horizontal, 5)
            .padding(.vertical, 1)
            .background(Capsule().fill((safe ? Color.secondary : Color.orange).opacity(0.18)))
            .foregroundColor(safe ? .secondary : .orange)
            .help(licence.map { "\($0). " + (safe
                ? "Safe to include in a distributed build."
                : "Local personal use only — must never ship in a distributed build.") }
                ?? text)
    }

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            selector
                .frame(width: 28)
                .padding(.top, 2)

            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 6) {
                    Button(action: onToggleExpanded) {
                        HStack(spacing: 5) {
                            Image(systemName: expanded ? "chevron.down" : "chevron.right")
                                .font(.system(size: 9, weight: .semibold))
                                .foregroundColor(.secondary)
                            Text(status.name)
                                .font(.headline)
                        }
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .help(expanded ? "Hide details" : "Show what this model is and what uses it")

                    // The licence position rides on the collapsed row: whether a
                    // model may ship is too consequential to require expanding.
                    if let badge = status.licenceBadge {
                        licencePill(badge.text, safe: badge.safe, licence: status.licence)
                    }

                    Spacer()
                    if !status.builtIn && status.sizeMB > 0 {
                        // "49 MB" meant consumed disk next to Installed and
                        // download cost next to Download, styled identically.
                        Text(status.installed
                             ? "\(formatSize(mb: status.sizeMB)) on disk"
                             : "\(formatSize(mb: status.sizeMB)) download")
                            .font(.caption)
                            .foregroundColor(.secondary)
                    }
                }

                // ACTIVE is gone: the filled radio and the stage header already
                // say it, and a third restatement competed with the badges that
                // carry information the other two do not.
                if status.builtIn || status.experimental || status.reviewOnly || status.planned {
                    HStack(spacing: 6) {
                        if status.builtIn {
                            tagPill("BUILT-IN", .gray)
                        }
                        if status.experimental {
                            tagPill("EXPERIMENTAL", .orange)
                        }
                        if status.reviewOnly {
                            tagPill("REVIEW ONLY", .blue)
                        }
                        if status.planned {
                            tagPill("PLANNED", .purple)
                        }
                    }
                }

                if expanded {
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
                }

                if status.downloading {
                    ProgressView(value: status.progress)
                        .progressViewStyle(.linear)
                    Text("Downloading... \(Int(status.progress * 100))%")
                        .font(.caption2)
                        .foregroundColor(.secondary)
                }

                // Entries with a capability key offer a read-only
                // resource preflight (`models.py capability <key>`);
                // the report renders inline once it comes back.
                if status.capability != nil {
                    if capabilityChecking {
                        HStack(spacing: 6) {
                            ProgressView()
                                .controlSize(.small)
                            Text("Checking compatibility...")
                                .font(.caption)
                                .foregroundColor(.secondary)
                        }
                        .padding(.top, 2)
                    } else {
                        Button {
                            onCheckCapability()
                        } label: {
                            Label("Check compatibility", systemImage: "checkmark.shield")
                                .font(.caption)
                        }
                        .buttonStyle(.bordered)
                        .controlSize(.small)
                        .help("Verify this Mac meets the hardware and software requirements")
                        .padding(.top, 2)
                    }
                }
                if let report = capabilityReport {
                    capabilityReportView(report)
                        .padding(.top, 4)
                }
            }

            VStack {
                if status.planned {
                    // Managed externally with no runtime integration yet —
                    // no Download/Delete actions; the row's only action is
                    // "Check compatibility" (inline, above).
                    EmptyView()
                } else if status.builtIn {
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

    /// Inline rendering of a capability preflight: an overall verdict
    /// line followed by one row per check (status icon, detail, and the
    /// fix hint when the check produced one).
    @ViewBuilder
    private func capabilityReportView(_ report: ModelCapabilityReport) -> some View {
        VStack(alignment: .leading, spacing: 3) {
            if let error = report.error {
                Label(error, systemImage: "exclamationmark.triangle.fill")
                    .font(.caption)
                    .foregroundColor(.orange)
            } else {
                Label(report.canRun ? "This Mac can run it" : "Missing requirements",
                      systemImage: report.canRun ? "checkmark.circle.fill" : "xmark.octagon.fill")
                    .font(.caption.weight(.semibold))
                    .foregroundColor(report.canRun ? .green : .red)
                ForEach(Array(report.checks.enumerated()), id: \.offset) { _, check in
                    HStack(alignment: .top, spacing: 4) {
                        Image(systemName: capabilityIcon(check.status))
                            .foregroundColor(capabilityColor(check.status))
                            .font(.caption2)
                        VStack(alignment: .leading, spacing: 1) {
                            Text("\(check.name): \(check.detail)")
                                .font(.caption2)
                                .foregroundColor(.secondary)
                                .fixedSize(horizontal: false, vertical: true)
                            if !check.fix.isEmpty {
                                Text(check.fix)
                                    .font(.caption2.monospaced())
                                    .foregroundColor(.secondary.opacity(0.85))
                            }
                        }
                    }
                }
            }
        }
    }
}

private func friendlyStage(_ stage: String) -> String {
    switch stage {
    case "transcription": return "Transcription"
    case "diarization":   return "Speaker Diarization"
    case "vad":           return "Voice Activity Detection"
    case "identity_review": return "Speaker Identity Review"
    case "voice_library": return "Voice Library"
    default:              return stage.capitalized
    }
}

/// SF Symbol per capability-check status ("pass" | "warn" | "fail" | "info").
private func capabilityIcon(_ status: String) -> String {
    switch status {
    case "pass": return "checkmark.circle.fill"
    case "warn": return "exclamationmark.triangle.fill"
    case "fail": return "xmark.octagon.fill"
    default:     return "info.circle.fill"
    }
}

private func capabilityColor(_ status: String) -> Color {
    switch status {
    case "pass": return .green
    case "warn": return .orange
    case "fail": return .red
    default:     return .blue
    }
}
