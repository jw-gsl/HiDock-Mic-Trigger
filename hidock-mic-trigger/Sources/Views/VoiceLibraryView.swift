import SwiftUI

// MARK: - Data Model

struct VoiceLibrarySpeaker: Identifiable {
    let id: String
    let name: String
    let sampleCount: Int
    let lastUpdated: String
}

// MARK: - VoiceLibraryView

enum VoiceSortKey: String, CaseIterable, Identifiable {
    case name, samples, meetings, updated
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
    @State private var sortKey: VoiceSortKey = .name
    /// When set, show a picker to merge this speaker into another library name.
    @State private var mergingFrom: VoiceLibrarySpeaker? = nil
    @State private var mergeTargetName: String = ""
    let onDelete: (String) -> Void
    let onRename: (String, String) -> Void
    /// person name → number of meetings they appear in (for display + sort).
    var meetingCounts: [String: Int] = [:]
    /// Filter the main recordings list to meetings this person is in.
    var onFilterToPerson: ((String) -> Void)? = nil

    private var visibleSpeakers: [VoiceLibrarySpeaker] {
        let q = search.trimmingCharacters(in: .whitespaces).lowercased()
        let filtered = q.isEmpty ? speakers
            : speakers.filter { $0.name.lowercased().contains(q) }
        return filtered.sorted { a, b in
            switch sortKey {
            case .name: return a.name.localizedCaseInsensitiveCompare(b.name) == .orderedAscending
            case .samples: return a.sampleCount > b.sampleCount
            case .meetings: return (meetingCounts[a.name] ?? 0) > (meetingCounts[b.name] ?? 0)
            case .updated: return a.lastUpdated > b.lastUpdated
            }
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
                Text("\(speakers.count) speaker\(speakers.count == 1 ? "" : "s")")
                    .font(.caption)
                    .foregroundColor(.secondary)
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

                    let meetings = meetingCounts[speaker.name] ?? 0
                    Text("\(speaker.sampleCount) sample\(speaker.sampleCount == 1 ? "" : "s") · \(meetings) meeting\(meetings == 1 ? "" : "s")")
                        .font(.caption)
                        .foregroundColor(.secondary)

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
                lastUpdated: target.lastUpdated
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
                lastUpdated: kept.lastUpdated
            )
            speakers.removeAll { $0.id == speaker.id }
        } else if let index = speakers.firstIndex(where: { $0.id == speaker.id }) {
            speakers[index] = VoiceLibrarySpeaker(
                id: trimmed,
                name: trimmed,
                sampleCount: speaker.sampleCount,
                lastUpdated: speaker.lastUpdated
            )
        }
        editingId = nil
    }

    private func deleteSpeaker(_ speaker: VoiceLibrarySpeaker) {
        onDelete(speaker.name)
        speakers.removeAll { $0.id == speaker.id }
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
}
