import AppKit
import SwiftUI

// MARK: - Data Model

struct VoiceLibrarySpeaker: Identifiable {
    let id: String
    let name: String
    let sampleCount: Int
    /// Distinct recording sources represented by this profile.
    let meetingCount: Int
    let lastUpdated: String
    let profileStatus: String

    init(
        id: String,
        name: String,
        sampleCount: Int,
        meetingCount: Int = 0,
        lastUpdated: String,
        profileStatus: String = "thin"
    ) {
        self.id = id
        self.name = name
        self.sampleCount = sampleCount
        self.meetingCount = meetingCount
        self.lastUpdated = lastUpdated
        self.profileStatus = profileStatus
    }
}

struct VoiceLibrarySample: Identifiable {
    let id: String
    let source: String
    let addedAt: String
    let updatedAt: String
    let sourceFile: String?
    let audioFile: String?
    let speakerId: String?
    let segmentStart: Double?
    let segmentEnd: Double?
    let model: String?
    let qualityScore: Double?
    let qualityState: String?
    let isActive: Bool?
}

// MARK: - VoiceLibraryView

enum VoiceSortKey: String, CaseIterable, Identifiable {
    // Keep the control order aligned with the requested default workflow:
    // meeting coverage first, then sample depth, name, and recency.
    case meetings, samples, name, updated
    var id: String { rawValue }
    var label: String {
        switch self {
        case .name: return "Name"
        case .samples: return "Samples"
        case .meetings: return "Meetings"
        case .updated: return "Recent"
        }
    }
}

struct VoiceLibraryView: View {
    @State var speakers: [VoiceLibrarySpeaker]
    @State private var editingId: String? = nil
    @State private var editingName: String = ""
    @State private var search = ""
    // Meeting coverage is the most useful default: people who appear most
    // often are the highest-value profiles to keep improving.
    @State private var sortKey: VoiceSortKey = .meetings
    /// When set, show a picker to merge this speaker into another library name.
    @State private var mergingFrom: VoiceLibrarySpeaker? = nil
    @State private var mergeTargetName: String = ""
    @State private var selectionMode = false
    @State private var selectedSpeakerIDs: Set<String> = []
    @State private var confirmBulkDelete = false
    @State private var samplesFor: VoiceLibrarySpeaker? = nil
    @State private var samples: [VoiceLibrarySample] = []
    @State private var samplesLoading = false
    @StateObject private var samplePlayer = SegmentAudioPlayer()
    let onDelete: (String) -> Void
    let onRename: (String, String) -> Void
    var onListSamples: ((String, @escaping ([VoiceLibrarySample]) -> Void) -> Void)? = nil
    var onDeleteSample: ((String, String) -> Void)? = nil
    /// Backfill trustworthy historical meeting exemplars for one person.
    var onBackfill: ((String) -> Void)? = nil
    /// person name → number of meetings they appear in (for display + sort).
    var meetingCounts: [String: Int] = [:]
    /// Aggregate totals for the full library. Meeting count is already
    /// deduplicated across speakers by the backend summary command.
    var totalMeetingCount: Int = 0
    var totalSampleCount: Int = 0
    /// Filter the main recordings list to meetings this person is in.
    var onFilterToPerson: ((String) -> Void)? = nil

    private var visibleSpeakers: [VoiceLibrarySpeaker] {
        let q = search.trimmingCharacters(in: .whitespaces).lowercased()
        let filtered = q.isEmpty ? speakers
            : speakers.filter { $0.name.lowercased().contains(q) }
        return filtered.sorted { a, b in
            // The default order is meetings → samples → name → recent. Keep
            // the same deterministic tie-breakers when the user chooses a
            // different primary sort, so rows do not shuffle unpredictably.
            let keys: [VoiceSortKey]
            switch sortKey {
            case .meetings:
                keys = [.meetings, .samples, .name, .updated]
            case .samples:
                keys = [.samples, .meetings, .name, .updated]
            case .name:
                keys = [.name, .meetings, .samples, .updated]
            case .updated:
                keys = [.updated, .meetings, .samples, .name]
            }

            for key in keys {
                switch key {
                case .meetings:
                    let left = a.meetingCount > 0 ? a.meetingCount : (meetingCounts[a.name] ?? 0)
                    let right = b.meetingCount > 0 ? b.meetingCount : (meetingCounts[b.name] ?? 0)
                    if left != right { return left > right }
                case .samples:
                    if a.sampleCount != b.sampleCount { return a.sampleCount > b.sampleCount }
                case .name:
                    let comparison = a.name.localizedCaseInsensitiveCompare(b.name)
                    if comparison != .orderedSame { return comparison == .orderedAscending }
                case .updated:
                    if a.lastUpdated != b.lastUpdated { return a.lastUpdated > b.lastUpdated }
                }
            }

            return a.id.localizedCaseInsensitiveCompare(b.id) == .orderedAscending
        }
    }

    var body: some View {
        VStack(spacing: 0) {
            // Header
            HStack {
                Image(systemName: "person.2.wave.2")
                    .foregroundColor(.accentColor)
                Text("Voice Library")
                    .font(.headline)
                Spacer()
                HStack(spacing: 10) {
                    libraryTotal(value: speakers.count, label: "speaker", plural: "speakers")
                    libraryTotal(value: totalMeetingCount, label: "meeting", plural: "meetings")
                    libraryTotal(value: totalSampleCount, label: "sample", plural: "samples")
                }
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 12)
            .background(.ultraThinMaterial)

            Divider()

            // Search + sort
            HStack(spacing: 8) {
                Image(systemName: "magnifyingglass").foregroundColor(.secondary)
                TextField("Search speakers…", text: $search)
                    .textFieldStyle(.roundedBorder)
                Divider().frame(height: 16)
                Text("Sort:").font(.caption.weight(.medium)).foregroundColor(.secondary)
                Picker("", selection: $sortKey) {
                    ForEach(VoiceSortKey.allCases) { Text($0.label).tag($0) }
                }
                .pickerStyle(.segmented)
                .frame(width: 260)
                Spacer(minLength: 0)
                if selectionMode && !selectedSpeakerIDs.isEmpty {
                    Text("\(selectedSpeakerIDs.count) selected")
                        .font(.caption)
                        .foregroundColor(.secondary)
                    Button(role: .destructive) {
                        confirmBulkDelete = true
                    } label: {
                        Label("Remove", systemImage: "trash")
                    }
                    .buttonStyle(.bordered)
                }
                if speakers.count > 1 {
                    Button(selectionMode ? "Done" : "Select") {
                        selectionMode.toggle()
                        if !selectionMode { selectedSpeakerIDs.removeAll() }
                    }
                    .buttonStyle(.bordered)
                    .help(selectionMode ? "Finish selecting speakers" : "Select multiple speakers to remove them together")
                }
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 8)

            Divider()

            if speakers.isEmpty {
                emptyState
            } else {
                speakerList
            }
        }
        .frame(minWidth: 360, minHeight: 300)   // hosted in a resizable pane now
        .alert("Remove selected speakers?", isPresented: $confirmBulkDelete) {
            Button("Cancel", role: .cancel) { }
            Button("Remove", role: .destructive) {
                deleteSelectedSpeakers()
            }
        } message: {
            Text("This removes their voice samples from the library. Their existing transcripts are not changed.")
        }
    }

    private func libraryTotal(value: Int, label: String, plural: String) -> some View {
        VStack(alignment: .trailing, spacing: 1) {
            Text("\(value)")
                .font(.caption.weight(.semibold))
            Text(value == 1 ? label : plural)
                .font(.caption2)
                .foregroundColor(.secondary)
        }
        .help("\(value) \(value == 1 ? label : plural)")
    }

    // MARK: - Empty State

    private var emptyState: some View {
        VStack(spacing: 12) {
            Spacer()
            Image(systemName: "person.2.slash")
                .font(.system(size: 40))
                .foregroundColor(.secondary)
            Text("No voices enrolled")
                .font(.title3)
                .foregroundColor(.secondary)
            Text("Transcribe a recording with speaker labels, then name the speakers.")
                .font(.caption)
                .foregroundColor(.secondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 40)
            Spacer()
        }
    }

    // MARK: - Speaker List

    private var speakerList: some View {
        List {
            ForEach(visibleSpeakers) { speaker in
                HStack {
                    if selectionMode {
                        Toggle(
                            "Select \(speaker.name)",
                            isOn: Binding(
                                get: { selectedSpeakerIDs.contains(speaker.id) },
                                set: { selected in
                                    if selected { selectedSpeakerIDs.insert(speaker.id) }
                                    else { selectedSpeakerIDs.remove(speaker.id) }
                                }
                            )
                        )
                        .labelsHidden()
                        .toggleStyle(.checkbox)
                    }

                    if let onFilterToPerson = onFilterToPerson {
                        Button {
                            onFilterToPerson(speaker.name)
                        } label: {
                            Image(systemName: "line.3.horizontal.decrease.circle")
                        }
                        .buttonStyle(.borderless)
                        .foregroundColor(.accentColor)
                        .help("Show only meetings \(speaker.name) is in")
                    }

                    if editingId == speaker.id {
                        TextField("Name", text: $editingName, onCommit: {
                            commitRename(speaker: speaker)
                        })
                        .textFieldStyle(.roundedBorder)
                        .frame(maxWidth: 200)
                    } else {
                        Text(speaker.name)
                            .font(.body)
                            .fontWeight(.medium)
                            .onTapGesture {
                                editingId = speaker.id
                                editingName = speaker.name
                            }
                    }

                    Spacer()

                    let meetings = speaker.meetingCount > 0
                        ? speaker.meetingCount
                        : (meetingCounts[speaker.name] ?? 0)
                    Button {
                        openSamples(for: speaker)
                    } label: {
                        Text("\(speaker.sampleCount) sample\(speaker.sampleCount == 1 ? "" : "s") · \(meetings) meeting\(meetings == 1 ? "" : "s")")
                            .font(.caption)
                            .foregroundColor(.secondary)
                    }
                    .buttonStyle(.borderless)
                    .disabled(onListSamples == nil)
                    .help("Inspect and audition the samples behind this voice")

                    if let onBackfill = onBackfill {
                        Button {
                            onBackfill(speaker.name)
                        } label: {
                            Image(systemName: "arrow.clockwise.circle")
                        }
                        .buttonStyle(.borderless)
                        .help("Backfill trustworthy historical meeting samples")
                    }

                    Text(profileStatusLabel(speaker.profileStatus))
                        .font(.caption2.weight(.medium))
                        .foregroundColor(profileStatusColor(speaker.profileStatus))
                        .help("Voice profile depth: \(profileStatusHelp(speaker.profileStatus))")

                    if !speaker.lastUpdated.isEmpty {
                        Text(formatDate(speaker.lastUpdated))
                            .font(.caption)
                            .foregroundColor(.secondary)
                    }

                    if speakers.count > 1 {
                        Button {
                            mergingFrom = speaker
                            mergeTargetName = speakers.first(where: { $0.id != speaker.id })?.name ?? ""
                        } label: {
                            Image(systemName: "arrow.triangle.merge")
                        }
                        .buttonStyle(.borderless)
                        .help("Merge into another speaker — keep one name, combine voice samples")
                    }

                    Button(role: .destructive) {
                        deleteSpeaker(speaker)
                    } label: {
                        Image(systemName: "trash")
                            .foregroundColor(.red)
                    }
                    .buttonStyle(.borderless)
                    .help("Delete speaker")
                }
                .padding(.vertical, 2)
            }
        }
        .sheet(item: $mergingFrom) { source in
            mergeSheet(source: source)
        }
        .sheet(item: $samplesFor) { speaker in
            samplesSheet(for: speaker)
        }
    }

    // MARK: - Merge

    private func mergeSheet(source: VoiceLibrarySpeaker) -> some View {
        let targets = speakers
            .filter { $0.id != source.id }
            .sorted { $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending }
        return VStack(alignment: .leading, spacing: 16) {
            Text("Merge speakers")
                .font(.headline)
            Text("Move all voice samples from “\(source.name)” into another library name, then remove “\(source.name)”. Use this for typos (e.g. Wildmsith → Wildsmith).")
                .font(.callout)
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            Picker("Merge into", selection: $mergeTargetName) {
                ForEach(targets) { t in
                    Text(t.name).tag(t.name)
                }
            }
            .labelsHidden()
            // Ensure a valid default if the sheet opened before target was set.
            .onAppear {
                if mergeTargetName.isEmpty || mergeTargetName == source.name {
                    mergeTargetName = targets.first?.name ?? ""
                }
            }

            HStack {
                Spacer()
                Button("Cancel") { mergingFrom = nil }
                    .keyboardShortcut(.cancelAction)
                Button("Merge") {
                    commitMerge(from: source, into: mergeTargetName)
                }
                .keyboardShortcut(.defaultAction)
                .disabled(mergeTargetName.isEmpty || mergeTargetName == source.name)
                .buttonStyle(.borderedProminent)
            }
        }
        .padding(20)
        .frame(minWidth: 360)
    }

    private func commitMerge(from source: VoiceLibrarySpeaker, into targetName: String) {
        guard !targetName.isEmpty, targetName != source.name else {
            mergingFrom = nil
            return
        }
        // Backend rename-to-existing merges exemplars and deletes the source key.
        onRename(source.name, targetName)
        if let ti = speakers.firstIndex(where: { $0.name == targetName }),
           let si = speakers.firstIndex(where: { $0.id == source.id }) {
            let target = speakers[ti]
            speakers[ti] = VoiceLibrarySpeaker(
                id: target.id,
                name: target.name,
                sampleCount: target.sampleCount + source.sampleCount,
                meetingCount: target.meetingCount + source.meetingCount,
                lastUpdated: target.lastUpdated,
                profileStatus: target.profileStatus
            )
            speakers.remove(at: si)
        } else {
            speakers.removeAll { $0.id == source.id }
        }
        mergingFrom = nil
    }

    // MARK: - Actions

    private func commitRename(speaker: VoiceLibrarySpeaker) {
        let trimmed = editingName.trimmingCharacters(in: .whitespaces)
        guard !trimmed.isEmpty, trimmed != speaker.name else {
            editingId = nil
            return
        }
        onRename(speaker.name, trimmed)
        // Update local state — if the new name already exists, this was a merge.
        if let existing = speakers.firstIndex(where: { $0.name == trimmed && $0.id != speaker.id }) {
            let kept = speakers[existing]
            speakers[existing] = VoiceLibrarySpeaker(
                id: kept.id,
                name: kept.name,
                sampleCount: kept.sampleCount + speaker.sampleCount,
                meetingCount: kept.meetingCount + speaker.meetingCount,
                lastUpdated: kept.lastUpdated,
                profileStatus: kept.profileStatus
            )
            speakers.removeAll { $0.id == speaker.id }
        } else if let index = speakers.firstIndex(where: { $0.id == speaker.id }) {
            speakers[index] = VoiceLibrarySpeaker(
                id: trimmed,
                name: trimmed,
                sampleCount: speaker.sampleCount,
                meetingCount: speaker.meetingCount,
                lastUpdated: speaker.lastUpdated,
                profileStatus: speaker.profileStatus
            )
        }
        editingId = nil
    }

    private func deleteSpeaker(_ speaker: VoiceLibrarySpeaker) {
        onDelete(speaker.name)
        selectedSpeakerIDs.remove(speaker.id)
        speakers.removeAll { $0.id == speaker.id }
    }

    private func deleteSelectedSpeakers() {
        let selected = speakers.filter { selectedSpeakerIDs.contains($0.id) }
        for speaker in selected {
            onDelete(speaker.name)
        }
        speakers.removeAll { selectedSpeakerIDs.contains($0.id) }
        selectedSpeakerIDs.removeAll()
        selectionMode = false
    }

    // MARK: - Samples

    private func openSamples(for speaker: VoiceLibrarySpeaker) {
        guard let onListSamples = onListSamples else { return }
        samplePlayer.stop()
        samplesFor = speaker
        samples = []
        samplesLoading = true
        onListSamples(speaker.name) { loaded in
            DispatchQueue.main.async {
                guard samplesFor?.id == speaker.id else { return }
                samples = loaded
                samplesLoading = false
            }
        }
    }

    private func samplesSheet(for speaker: VoiceLibrarySpeaker) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                VStack(alignment: .leading, spacing: 3) {
                    Text("Samples for \(speaker.name)")
                        .font(.headline)
                    Text("One exemplar per meeting is retained; remove clips that are noisy or misattributed.")
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
                Spacer()
                Text("\(samples.count) sample\(samples.count == 1 ? "" : "s")")
                    .font(.caption)
                    .foregroundColor(.secondary)
                Button {
                    samplesFor = nil
                } label: {
                    Label("Close", systemImage: "xmark")
                }
                .keyboardShortcut(.cancelAction)
                .buttonStyle(.borderedProminent)
            }
            .padding(16)

            Divider()

            if samplesLoading {
                VStack {
                    Spacer()
                    ProgressView("Loading sample provenance…")
                    Spacer()
                }
            } else if samples.isEmpty {
                VStack(spacing: 8) {
                    Spacer()
                    Image(systemName: "waveform.slash")
                        .font(.system(size: 30))
                        .foregroundColor(.secondary)
                    Text("No sample provenance is available for this profile.")
                        .font(.callout)
                        .foregroundColor(.secondary)
                    Spacer()
                }
                .frame(maxWidth: .infinity)
            } else {
                List {
                    ForEach(samples) { sample in
                        sampleRow(sample, speaker: speaker)
                    }
                }
            }
        }
        .frame(minWidth: 560, minHeight: 360)
        .onDisappear {
            samplePlayer.stop()
        }
    }

    private func sampleRow(_ sample: VoiceLibrarySample, speaker: VoiceLibrarySpeaker) -> some View {
        HStack(spacing: 10) {
            Button {
                toggleSamplePlayback(sample)
            } label: {
                Image(systemName: samplePlayer.playingSegmentId == sample.id ? "stop.fill" : "play.fill")
                    .frame(width: 18)
            }
            .buttonStyle(.borderless)
            .disabled(!canPlay(sample))
            .help(canPlay(sample) ? "Play representative clip" : "Source audio is unavailable")

            VStack(alignment: .leading, spacing: 3) {
                Text(sampleMeetingName(sample))
                    .font(.body.weight(.medium))
                    .lineLimit(1)
                HStack(spacing: 6) {
                    Text(sample.source.capitalized)
                    if let range = sampleRange(sample) {
                        Text("·")
                        Text(range)
                    }
                    if let model = sample.model, !model.isEmpty {
                        Text("·")
                        Text(model)
                    }
                    if let quality = sample.qualityScore {
                        Text("·")
                        Text("quality \(Int((quality * 100).rounded()))%")
                    }
                }
                .font(.caption)
                .foregroundColor(.secondary)
            }

            Spacer()

            if let active = sample.isActive {
                Text(active ? "Active" : "Archived")
                    .font(.caption.weight(.medium))
                    .foregroundColor(active ? .green : .secondary)
                    .padding(.horizontal, 7)
                    .padding(.vertical, 3)
                    .background((active ? Color.green : Color.secondary).opacity(0.12))
                    .clipShape(Capsule())
                    .help(active
                        ? "Used for automatic voice matching"
                        : "Retained as provenance-backed evidence, but excluded from automatic matching")
            }

            if let sourceFile = sample.sourceFile, !sourceFile.isEmpty {
                Button {
                    NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: sourceFile)])
                } label: {
                    Image(systemName: "doc.text.magnifyingglass")
                }
                .buttonStyle(.borderless)
                .help("Show the diarization sidecar")
            }

            Button(role: .destructive) {
                samplePlayer.stop()
                onDeleteSample?(speaker.name, sample.id)
                samples.removeAll { $0.id == sample.id }
            } label: {
                Image(systemName: "trash")
                    .foregroundColor(.red)
            }
            .buttonStyle(.borderless)
            .help("Remove this voice sample")
        }
        .padding(.vertical, 4)
    }

    private func canPlay(_ sample: VoiceLibrarySample) -> Bool {
        guard let audioFile = sample.audioFile,
              let start = sample.segmentStart,
              let end = sample.segmentEnd,
              end > start else { return false }
        return FileManager.default.fileExists(atPath: audioFile)
    }

    private func toggleSamplePlayback(_ sample: VoiceLibrarySample) {
        guard let audioFile = sample.audioFile,
              let start = sample.segmentStart,
              let end = sample.segmentEnd else { return }
        if samplePlayer.playingSegmentId == sample.id {
            samplePlayer.stop()
        } else {
            samplePlayer.play(
                audioPath: audioFile,
                start: start,
                end: end,
                segmentId: sample.id
            )
        }
    }

    private func sampleMeetingName(_ sample: VoiceLibrarySample) -> String {
        let path = sample.sourceFile ?? sample.audioFile ?? "Unknown meeting"
        var name = URL(fileURLWithPath: path).deletingPathExtension().lastPathComponent
        if name.hasSuffix("_diarized") {
            name = String(name.dropLast("_diarized".count))
        }
        return name.isEmpty ? "Unknown meeting" : name
    }

    private func sampleRange(_ sample: VoiceLibrarySample) -> String? {
        guard let start = sample.segmentStart, let end = sample.segmentEnd, end > start else {
            return nil
        }
        return "\(formatDuration(start))–\(formatDuration(end))"
    }

    private func formatDuration(_ seconds: Double) -> String {
        let total = max(0, Int(seconds.rounded()))
        return String(format: "%02d:%02d", total / 60, total % 60)
    }

    private func formatDate(_ isoString: String) -> String {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = formatter.date(from: isoString) {
            let display = DateFormatter()
            display.dateStyle = .medium
            display.timeStyle = .none
            return display.string(from: date)
        }
        // Try without fractional seconds
        formatter.formatOptions = [.withInternetDateTime]
        if let date = formatter.date(from: isoString) {
            let display = DateFormatter()
            display.dateStyle = .medium
            display.timeStyle = .none
            return display.string(from: date)
        }
        return isoString
    }

    private func profileStatusLabel(_ status: String) -> String {
        switch status {
        case "healthy": return "Healthy"
        case "usable": return "Usable"
        default: return "Needs samples"
        }
    }

    private func profileStatusColor(_ status: String) -> Color {
        switch status {
        case "healthy": return .green
        case "usable": return .orange
        default: return .secondary
        }
    }

    private func profileStatusHelp(_ status: String) -> String {
        switch status {
        case "healthy": return "at least 12 samples across 5 meetings"
        case "usable": return "at least 5 samples across 3 meetings; more varied meetings will improve it"
        default: return "fewer than 5 samples or fewer than 3 meetings"
        }
    }
}
