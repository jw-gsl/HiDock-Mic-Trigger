import SwiftUI

/// The panes of the Settings window, in sidebar order.
enum SettingsPane: String, CaseIterable, Identifiable {
    case general, microphone, devices, transcription, speakers, models, summaries, calendar, github, led

    var id: String { rawValue }

    var title: String {
        switch self {
        case .general: return "General"
        case .microphone: return "Microphone"
        case .devices: return "Devices"
        case .transcription: return "Transcription"
        case .speakers: return "Speakers"
        case .models: return "Models"
        case .summaries: return "Summaries"
        case .calendar: return "Calendar"
        case .github: return "GitHub"
        case .led: return "LED Ticker"
        }
    }

    var icon: String {
        switch self {
        case .general: return "gearshape"
        case .microphone: return "mic"
        case .devices: return "externaldrive.connected.to.line.below"
        case .transcription: return "waveform"
        case .speakers: return "person.2.wave.2"
        case .models: return "shippingbox"
        case .summaries: return "sparkles"
        case .calendar: return "calendar"
        case .github: return "icloud.and.arrow.up"
        case .led: return "square.grid.3x3.fill"
        }
    }

    /// Where a failing setup check (`transcribe.py doctor`) is fixed.
    static func forSetupCheck(_ id: String) -> SettingsPane {
        switch id {
        case "nemotron", "embedding", "naming": return .models
        case "github", "other_mac": return .github
        default: return .general
        }
    }
}

/// Every setting in one window: a sidebar of panes, each a short grouped
/// form. Replaces settings spread over footer buttons, menus, a tab hidden
/// inside Models and popovers (docs/PLAN-settings-window.md).
///
/// Controls bind to the same view-model setters and UserDefaults keys as
/// before, so existing preferences carry over unchanged.
struct SettingsView: View {
    @ObservedObject var viewModel: HiDockViewModel

    var body: some View {
        NavigationSplitView {
            List(SettingsPane.allCases, selection: Binding(
                get: { viewModel.settingsPane },
                set: { if let pane = $0 { viewModel.settingsPane = pane } }
            )) { pane in
                Label(pane.title, systemImage: pane.icon)
                    .badge(badge(for: pane))
                    .tag(pane)
            }
            .navigationSplitViewColumnWidth(min: 170, ideal: 190, max: 230)
        } detail: {
            pane(viewModel.settingsPane)
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
                .navigationTitle(viewModel.settingsPane.title)
        }
        .frame(minWidth: 760, minHeight: 520)
    }

    /// Flags a pane with something to fix, so it's findable from the sidebar.
    private func badge(for pane: SettingsPane) -> Text? {
        let count = viewModel.setupProblems.filter { SettingsPane.forSetupCheck($0.id) == pane }.count
            + (pane == .github && viewModel.transcriptPublishProblem != nil ? 1 : 0)
        return count > 0 ? Text("\(count)") : nil
    }

    @ViewBuilder
    private func pane(_ pane: SettingsPane) -> some View {
        switch pane {
        case .general: GeneralSettingsPane(viewModel: viewModel)
        case .microphone: MicrophoneSettingsPane(viewModel: viewModel)
        case .devices: DevicesSettingsPane(viewModel: viewModel)
        case .transcription: TranscriptionSettingsPane(viewModel: viewModel)
        case .speakers: SpeakersSettingsPane(viewModel: viewModel)
        case .models: ModelManagerView(viewModel: viewModel, section: .models)
        case .summaries: SummariesSettingsPane(viewModel: viewModel)
        case .calendar: ModelManagerView(viewModel: viewModel, section: .calendar)
        case .github: ModelManagerView(viewModel: viewModel, section: .github)
        case .led:
            LEDSettingsView(settings: viewModel.ledSettings) {
                viewModel.ledMatrix.notify(LEDEvent(kind: .syncComplete, text: "\(LEDFont.check) LED TEST"))
            }
        }
    }

}

// MARK: - Panes

/// The summariser and the template list in one form, so there's one scroll.
private struct SummariesSettingsPane: View {
    @ObservedObject var viewModel: HiDockViewModel

    var body: some View {
        Form {
            Section("AI summariser") {
                SummariserRows(viewModel: viewModel)
            }
            TemplatesManagerView(viewModel: viewModel, asFormSection: true)
        }
        .formStyle(.grouped)
    }
}

private struct SummariserRows: View {
    @ObservedObject var viewModel: HiDockViewModel

    var body: some View {
        Picker("Engine", selection: Binding(
            get: { viewModel.summarizeEngine },
            set: { viewModel.onSetSummarizeEngine($0) }
        )) {
            ForEach(viewModel.summarizeEngineChoices, id: \.id) { Text($0.label).tag($0.id) }
        }
        Toggle("Show the CLI pane while summarising", isOn: Binding(
            get: { viewModel.showCLIWhileSummarising },
            set: { viewModel.onSetShowCLIWhileSummarising($0) }
        ))
    }
}

private struct GeneralSettingsPane: View {
    @ObservedObject var viewModel: HiDockViewModel

    var body: some View {
        Form {
            Section("Appearance") {
                Picker("Theme", selection: Binding(
                    get: { viewModel.appearanceMode },
                    set: { viewModel.onSetAppearance($0) }
                )) {
                    Text("Auto (System)").tag("auto")
                    Text("Dark").tag("dark")
                    Text("Light").tag("light")
                }
                .pickerStyle(.segmented)
            }
            Section("Startup") {
                Toggle("Start the mic trigger when HiDock opens", isOn: Binding(
                    get: { viewModel.autoStartOnLaunch },
                    set: { _ in viewModel.onToggleAutoStart() }
                ))
            }
            Section("Notifications") {
                Toggle("Transcription complete", isOn: Binding(
                    get: { viewModel.notifyTranscriptionComplete },
                    set: { _ in viewModel.onToggleNotifyTranscription() }
                ))
                Toggle("Download complete", isOn: Binding(
                    get: { viewModel.notifyDownloadComplete },
                    set: { _ in viewModel.onToggleNotifyDownload() }
                ))
                Toggle("Mic changes", isOn: Binding(
                    get: { viewModel.notifyMicChanges },
                    set: { _ in viewModel.onToggleNotifyMicChanges() }
                ))
            }
            Section("Updates") {
                HStack {
                    Text(viewModel.updateStatusText.isEmpty ? "HiDock checks for updates automatically."
                                                           : viewModel.updateStatusText)
                        .foregroundColor(.secondary)
                    Spacer()
                    Button("Check for Updates") { viewModel.onCheckForUpdates() }
                }
            }
            Section {
                if viewModel.setupProblems.isEmpty {
                    Label("Everything the pipeline needs is in place.", systemImage: "checkmark.circle")
                        .foregroundColor(.secondary)
                } else {
                    ForEach(viewModel.setupProblems) { problem in
                        VStack(alignment: .leading, spacing: 2) {
                            HStack {
                                Text("\(problem.title): \(problem.detail)").fontWeight(.medium)
                                Spacer()
                                let target = SettingsPane.forSetupCheck(problem.id)
                                if target != .general {
                                    Button("Open \(target.title)") { viewModel.settingsPane = target }
                                        .controlSize(.small)
                                }
                            }
                            if !problem.fix.isEmpty {
                                Text(problem.fix).font(.caption).foregroundColor(.secondary)
                            }
                        }
                        .textSelection(.enabled)
                    }
                }
                Button("Check again") { viewModel.onRecheckSetup() }
            } header: {
                Text("Setup check")
            } footer: {
                Text("Checks tokens, models, Python packages and GitHub sign-in a minute after launch and every 6 hours.")
            }
        }
        .formStyle(.grouped)
    }
}

private struct MicrophoneSettingsPane: View {
    @ObservedObject var viewModel: HiDockViewModel

    var body: some View {
        Form {
            Section {
                Picker("Trigger mic", selection: Binding(
                    get: { viewModel.selectedMicName ?? "" },
                    set: { if !$0.isEmpty { viewModel.onSelectMic($0) } }
                )) {
                    if viewModel.selectedMicName == nil { Text("None").tag("") }
                    ForEach(viewModel.availableMics, id: \.self) { Text($0).tag($0) }
                }
            } footer: {
                Text("The input HiDock keeps open so the device starts recording when a call begins.")
            }
            Section {
                micPicker("Default", value: viewModel.preferredMicName, set: viewModel.onSetPreferredMic)
                micPicker("Fallback", value: viewModel.fallbackMicName, set: viewModel.onSetFallbackMic)
            } footer: {
                Text("HiDock switches back to the default mic whenever it's connected, and uses the fallback when it isn't.")
            }
        }
        .formStyle(.grouped)
    }

    private func micPicker(_ label: String, value: String?, set: @escaping (String?) -> Void) -> some View {
        Picker(label, selection: Binding(
            get: { value ?? "" },
            set: { set($0.isEmpty ? nil : $0) }
        )) {
            Text("None").tag("")
            // Keep a saved mic selectable even while it's unplugged.
            if let value, !value.isEmpty, !viewModel.availableMics.contains(value) {
                Text("\(value) (not connected)").tag(value)
            }
            ForEach(viewModel.availableMics, id: \.self) { Text($0).tag($0) }
        }
    }
}

private struct DevicesSettingsPane: View {
    @ObservedObject var viewModel: HiDockViewModel

    var body: some View {
        DeviceManagerView(viewModel: viewModel)
    }
}

private struct TranscriptionSettingsPane: View {
    @ObservedObject var viewModel: HiDockViewModel

    var body: some View {
        Form {
            Section {
                Toggle("Download new recordings automatically", isOn: Binding(
                    get: { viewModel.syncAutoDownload },
                    set: { _ in viewModel.onToggleAutoDownload() }
                ))
                Toggle("Transcribe them automatically", isOn: Binding(
                    get: { viewModel.syncAutoTranscribe },
                    set: { _ in viewModel.onToggleAutoTranscribe() }
                ))
                Toggle("Summarise them automatically", isOn: Binding(
                    get: { viewModel.syncAutoSummarise },
                    set: { _ in viewModel.onToggleAutoSummarise() }
                ))
            } header: {
                Text("Automatic")
            }
            Section {
                Toggle("Detect and label speakers", isOn: Binding(
                    get: { viewModel.diarizeEnabled },
                    set: { _ in viewModel.onToggleDiarize() }
                ))
            } footer: {
                Text("Which models do the work is chosen under Models.")
            }
            Section("Folders") {
                LabeledContent("Recordings") {
                    FolderRow(path: viewModel.syncOutputFolder ?? "~/HiDock/Recordings",
                              choose: viewModel.onChooseRecordingsFolder)
                }
                LabeledContent("Transcripts") {
                    FolderRow(path: viewModel.syncTranscriptFolder ?? "~/HiDock/Raw Transcripts",
                              choose: viewModel.onChooseTranscriptFolder)
                }
            }
            Section {
                ForEach(HiDockViewModel.hideableStatuses, id: \.self) { status in
                    Toggle("Hide \(status.lowercased()) recordings", isOn: Binding(
                        get: { viewModel.hiddenStatuses.contains(status) },
                        set: { hide in
                            if hide { viewModel.hiddenStatuses.insert(status) }
                            else { viewModel.hiddenStatuses.remove(status) }
                        }
                    ))
                }
            } header: {
                Text("Recordings list")
            } footer: {
                Text("Also under Hide in the list's toolbar.")
            }
        }
        .formStyle(.grouped)
    }
}

private struct SpeakersSettingsPane: View {
    @ObservedObject var viewModel: HiDockViewModel
    @AppStorage(AppDelegate.refineAutoApplyKey) private var refineAutoApply = 0.7

    var body: some View {
        Form {
            Section {
                Picker("Apply automatically", selection: $refineAutoApply) {
                    Text("Off").tag(0.0)
                    Text("Score 0.9 or higher").tag(0.9)
                    Text("Score 0.8 or higher").tag(0.8)
                    Text("Score 0.7 or higher").tag(0.7)
                    Text("Score 0.6 or higher").tag(0.6)
                }
            } header: {
                Text("Refine")
            } footer: {
                Text(refineAutoApply == 0
                     ? "Refine's suggestions wait for you to apply or skip each one."
                     : "Line moves Refine is this sure of are applied once the meeting is confirmed or marked ad-hoc. Click Refine in the transcript to undo any, or keep them to teach the voice library. Whole-speaker renames and weaker suggestions always wait for you.")
            }
            Section {
                Toggle("Only count recordings from a date", isOn: Binding(
                    get: { viewModel.taggingIgnoreBefore != nil },
                    set: { on in
                        viewModel.taggingIgnoreBefore = on
                            ? Calendar.current.date(byAdding: .month, value: -1, to: Date())
                            : nil
                    }
                ))
                if let cutoff = viewModel.taggingIgnoreBefore {
                    DatePicker("Count from", selection: Binding(
                        get: { cutoff },
                        set: { viewModel.taggingIgnoreBefore = $0 }
                    ), displayedComponents: .date)
                }
            } header: {
                Text("Speaker tagging")
            } footer: {
                Text(viewModel.taggingIgnoreBefore == nil
                     ? "Every transcribed recording without confirmed speakers counts towards \u{201C}to tag\u{201D}."
                     : "Older recordings keep a grey tag and can still be tagged; they don't count towards \u{201C}to tag\u{201D}. \(viewModel.needsTaggingCount) to tag now.")
            }
            Section {
                LabeledContent("You are", value: viewModel.voiceLibraryMeName ?? "Not set")
                Button("Open Voice Library") { viewModel.onShowVoiceLibrary() }
            } header: {
                Text("Voice library")
            } footer: {
                Text("Mark yourself with the star next to your name in the Voice Library. Matching quality and clean-ups are shown there too.")
            }
        }
        .formStyle(.grouped)
    }
}

/// A folder path with a Choose… button.
private struct FolderRow: View {
    let path: String
    let choose: () -> Void

    var body: some View {
        HStack {
            Text((path as NSString).abbreviatingWithTildeInPath)
                .font(.callout.monospaced())
                .lineLimit(1)
                .truncationMode(.middle)
                .textSelection(.enabled)
            Spacer()
            Button("Choose…", action: choose)
        }
    }
}
