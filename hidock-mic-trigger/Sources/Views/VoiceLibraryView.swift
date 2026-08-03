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
    /// Membership badges for the All People tab: the live TitaNet matching
    /// library and/or the review-only WeSpeaker candidate library.
    let inMatchingLibrary: Bool
    let inCandidateLibrary: Bool

    init(
        id: String,
        name: String,
        sampleCount: Int,
        meetingCount: Int = 0,
        lastUpdated: String,
        profileStatus: String = "thin",
        inMatchingLibrary: Bool = true,
        inCandidateLibrary: Bool = false
    ) {
        self.id = id
        self.name = name
        self.sampleCount = sampleCount
        self.meetingCount = meetingCount
        self.lastUpdated = lastUpdated
        self.profileStatus = profileStatus
        self.inMatchingLibrary = inMatchingLibrary
        self.inCandidateLibrary = inCandidateLibrary
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

/// Tabs inside the Voice Library pane: the live matching library, every named
/// person across both libraries, or the people who appear to be enrolled twice.
enum VoiceLibraryTab: String, CaseIterable, Identifiable {
    case matching, people, duplicates
    var id: String { rawValue }
    var label: String {
        switch self {
        case .matching: return "Matching"
        case .people: return "All People"
        case .duplicates: return "Duplicates"
        }
    }
}

/// One pair of library entries that may be the same person twice.
///
/// The app holds two stores — the matching library the window lists, and whichever
/// candidate library is promoted for automatic naming — and they drift. A person
/// enrolled twice also competes with themselves: naming needs the best match to
/// lead the runner-up by a margin, so `Adam` at 0.822 and `Adam Gardner` at 0.807
/// cancelled out and that speaker was named nothing.
///
/// `verdict` is deliberately three-valued. A shared first name is not proof: of six
/// pairs examined on 2026-08-03, four were one person and two were different people
/// (`Ian` was not Ian Reay). So the evidence is shown and the user decides.
struct VoiceLibraryDuplicate: Identifiable {
    let names: [String]
    let suggestedKeep: String
    /// "same", "unclear", or "different".
    let verdict: String
    let meanSimilarity: Double?
    let sharedMeetings: [String]
    /// name → which stores hold it ("matching" / "naming").
    let stores: [String: [String]]
    let sampleCounts: [String: Int]

    var id: String { names.joined(separator: "|") }
    /// The other name — the one that disappears if the pair is merged.
    var absorbed: String { names.first { $0 != suggestedKeep } ?? names[0] }
}

/// People stranded in one store. Both directions are silent failures: someone
/// only in the naming library cannot be seen or edited here, and someone only in
/// the matching library can never be named automatically at all.
struct VoiceLibraryDrift {
    let matchingOnly: [String]
    let namingOnly: [String]
    let matchingCount: Int
    let namingCount: Int
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
    /// Which library view is showing. The people tab only appears when the
    /// host populated `allPeople`.
    @State private var tab: VoiceLibraryTab = .matching
    /// Union of live matching-library and review-only candidate people for
    /// the All People tab (empty when the host did not load candidate data).
    @State var allPeople: [VoiceLibrarySpeaker] = []
    /// All People tab multi-select state: tick exactly two people to enable
    /// the top Merge button (avoids scrolling the target picker).
    @State private var peopleSelectMode = false
    @State private var peopleSelection: Set<String> = []
    @State private var pairMerge = false
    @State private var pairKeepName: String = ""
    /// Duplicate pairs and store drift, loaded on demand by the Duplicates tab.
    @State var duplicates: [VoiceLibraryDuplicate] = []
    @State var drift: VoiceLibraryDrift? = nil
    @State private var duplicatesLoaded = false
    @State private var duplicatesLoading = false
    /// Pair awaiting confirmation, so a merge is never one stray click.
    @State private var pendingDuplicateMerge: VoiceLibraryDuplicate? = nil
    /// Show the pairs the evidence says are different people, which are hidden
    /// by default — they are the ones a name-only view would get wrong.
    @State private var showRejectedPairs = false
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
    /// Merge a person in every library that contains the source name (live
    /// matching library and/or review-only candidate library).
    var onMergePerson: ((String, String) -> Void)? = nil
    /// Load duplicate pairs + store drift across both libraries.
    var onLoadDuplicates: ((@escaping ([VoiceLibraryDuplicate], VoiceLibraryDrift?) -> Void) -> Void)? = nil
    /// The person who is the user themselves ("Me"), pinned atop person
    /// pickers. Tapping a row's star toggles it; nil means no Me set.
    @State var meName: String? = nil
    var onToggleMe: ((String) -> Void)? = nil

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
                    if tab == .people {
                        libraryTotal(value: allPeople.count, label: "person", plural: "people")
                        libraryTotal(value: allPeople.filter { $0.inMatchingLibrary }.count, label: "matching", plural: "matching")
                        libraryTotal(value: allPeople.filter { $0.inCandidateLibrary }.count, label: "review", plural: "review")
                    } else {
                        libraryTotal(value: speakers.count, label: "speaker", plural: "speakers")
                        libraryTotal(value: totalMeetingCount, label: "meeting", plural: "meetings")
                        libraryTotal(value: totalSampleCount, label: "sample", plural: "samples")
                    }
                }
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 12)
            .background(.ultraThinMaterial)

            Divider()

            if !allPeople.isEmpty {
                Picker("", selection: $tab) {
                    ForEach(VoiceLibraryTab.allCases) { tab in
                        // Badge the count of pairs actually worth acting on, so
                        // the tab is only loud when there is something to fix.
                        if tab == .duplicates, actionableDuplicateCount > 0 {
                            Text("\(tab.label) (\(actionableDuplicateCount))").tag(tab)
                        } else {
                            Text(tab.label).tag(tab)
                        }
                    }
                }
                .pickerStyle(.segmented)
                .labelsHidden()
                .padding(.horizontal, 16)
                .padding(.vertical, 8)
                .onChange(of: tab) { newTab in
                    if newTab == .duplicates { loadDuplicatesIfNeeded() }
                }

                Divider()
            }

            // Search — full-width row of its own (it was unreadably cramped
            // beside the tab controls)
            HStack(spacing: 8) {
                Image(systemName: "magnifyingglass").foregroundColor(.secondary)
                TextField(tab == .people ? "Search people…" : "Search speakers…", text: $search)
                    .textFieldStyle(.roundedBorder)
            }
            .padding(.horizontal, 16)
            .padding(.top, 8)
            .padding(.bottom, 4)

            // Controls row
            HStack(spacing: 8) {
                if tab == .matching {
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
                } else {
                    Spacer(minLength: 0)
                    if peopleSelectMode && !peopleSelection.isEmpty {
                        Text("\(peopleSelection.count) selected")
                            .font(.caption)
                            .foregroundColor(.secondary)
                    }
                    if peopleSelectMode && peopleSelection.count == 2 {
                        Button {
                            pairKeepName = defaultPairKeep()
                            pairMerge = true
                        } label: {
                            Label("Merge", systemImage: "arrow.triangle.merge")
                        }
                        .buttonStyle(.borderedProminent)
                        .help("Merge the two ticked people into one name")
                    }
                    Button(peopleSelectMode ? "Done" : "Select") {
                        peopleSelectMode.toggle()
                        if !peopleSelectMode { peopleSelection.removeAll() }
                    }
                    .buttonStyle(.bordered)
                    .help(peopleSelectMode ? "Finish selecting people" : "Tick two people, then merge them with one button")
                }
            }
            .padding(.horizontal, 16)
            .padding(.bottom, 8)
            .padding(.top, 4)

            Divider()

            if tab == .duplicates {
                duplicatesList
            } else if tab == .people {
                peopleList
            } else if speakers.isEmpty {
                emptyState
            } else {
                speakerList
            }
        }
        .frame(minWidth: 360, minHeight: 300)   // hosted in a resizable pane now
        .sheet(item: $mergingFrom) { source in
            mergeSheet(source: source)
        }
        .sheet(item: $samplesFor) { speaker in
            samplesSheet(for: speaker)
        }
        .sheet(isPresented: $pairMerge) {
            pairMergeSheet
        }
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

                    if let onToggleMe = onToggleMe {
                        Button {
                            meName = (meName == speaker.name) ? nil : speaker.name
                            onToggleMe(speaker.name)
                        } label: {
                            Image(systemName: meName == speaker.name ? "star.fill" : "star")
                        }
                        .buttonStyle(.borderless)
                        .foregroundColor(meName == speaker.name ? .yellow : .secondary)
                        .help(meName == speaker.name
                            ? "This is you — tap to unset"
                            : "Mark \(speaker.name) as Me — pinned to the top of name lists")
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

                    if meName == speaker.name {
                        meBadge()
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
                            mergeTargetName = defaultMergeTarget(excluding: speaker, pool: speakers)
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
    }

    // MARK: - All People

    /// Every named person across the matching and review-only candidate
    /// libraries, alphabetical. Merge is the only mutation offered here —
    /// deletes and sample inspection stay in the Matching tab.
    /// Pairs the evidence supports acting on. "different" pairs are excluded:
    /// they are name collisions, not duplicates, and badging them would push the
    /// user toward exactly the merge that would be wrong.
    private var actionableDuplicateCount: Int {
        duplicates.filter { $0.verdict != "different" }.count
    }

    private func loadDuplicatesIfNeeded() {
        guard !duplicatesLoaded, !duplicatesLoading, let load = onLoadDuplicates else { return }
        duplicatesLoading = true
        load { pairs, storeDrift in
            duplicates = pairs
            drift = storeDrift
            duplicatesLoading = false
            duplicatesLoaded = true
        }
    }

    /// One row per suspected duplicate, with the evidence that produced the
    /// verdict, plus a summary of people stranded in only one of the two stores.
    @ViewBuilder
    private var duplicatesList: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 10) {
                if duplicatesLoading {
                    HStack(spacing: 6) {
                        ProgressView().controlSize(.small)
                        Text("Comparing both libraries…").font(.caption).foregroundColor(.secondary)
                    }
                    .padding(.top, 12)
                } else {
                    driftSummary

                    let actionable = duplicates.filter { $0.verdict != "different" }
                    let rejected = duplicates.filter { $0.verdict == "different" }

                    if actionable.isEmpty {
                        Label("No duplicate profiles found.", systemImage: "checkmark.seal")
                            .font(.caption)
                            .foregroundColor(.green)
                    } else {
                        Text("Same person, enrolled twice")
                            .font(.caption.weight(.semibold))
                        Text("Merging keeps the fuller name and moves every sample onto it, in both "
                             + "libraries. A person enrolled twice competes with themselves and can "
                             + "stop their own voice being recognised.")
                            .font(.caption2)
                            .foregroundColor(.secondary)
                        ForEach(actionable) { pair in
                            duplicateRow(pair)
                        }
                    }

                    if !rejected.isEmpty {
                        Divider().padding(.vertical, 2)
                        Button {
                            showRejectedPairs.toggle()
                        } label: {
                            HStack(spacing: 4) {
                                Image(systemName: showRejectedPairs ? "chevron.down" : "chevron.right")
                                    .font(.caption2)
                                Text("\(rejected.count) share a first name but are different people")
                                    .font(.caption)
                            }
                        }
                        .buttonStyle(.plain)
                        .help("Shown so the decision is visible. The voices disagree, so merging "
                              + "these would put one person's words under another's name.")
                        if showRejectedPairs {
                            ForEach(rejected) { pair in
                                duplicateRow(pair)
                            }
                        }
                    }
                }
            }
            .padding(16)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .onAppear { loadDuplicatesIfNeeded() }
        .confirmationDialog(
            "Merge these two profiles?",
            isPresented: Binding(
                get: { pendingDuplicateMerge != nil },
                set: { if !$0 { pendingDuplicateMerge = nil } }
            ),
            presenting: pendingDuplicateMerge
        ) { pair in
            Button("Merge into \(pair.suggestedKeep)", role: .destructive) {
                onMergePerson?(pair.absorbed, pair.suggestedKeep)
                duplicates.removeAll { $0.id == pair.id }
                pendingDuplicateMerge = nil
                duplicatesLoaded = false   // re-read after the library changes
            }
            Button("Cancel", role: .cancel) { pendingDuplicateMerge = nil }
        } message: { pair in
            Text("\(pair.absorbed)'s samples move onto \(pair.suggestedKeep), and "
                 + "\(pair.absorbed) is removed. This applies to both libraries.")
        }
    }

    /// The two stores and anyone stranded in only one of them. Surfaced here
    /// because neither condition is visible anywhere else in the app: a
    /// naming-only person cannot be edited, and a matching-only person can never
    /// be named automatically.
    @ViewBuilder
    private var driftSummary: some View {
        if let drift {
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 6) {
                    Image(systemName: "arrow.left.arrow.right")
                        .font(.caption2).foregroundColor(.secondary)
                    Text("This list shows \(drift.matchingCount) people; automatic naming uses "
                         + "a library of \(drift.namingCount).")
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
                if !drift.matchingOnly.isEmpty {
                    Label("\(drift.matchingOnly.count) can never be recognised automatically: "
                          + drift.matchingOnly.joined(separator: ", "),
                          systemImage: "exclamationmark.triangle")
                        .font(.caption2)
                        .foregroundColor(.orange)
                        .help("The library that does the naming has no profile for them, so their "
                              + "voice can never be matched. Enrol them from a transcript.")
                }
                if !drift.namingOnly.isEmpty {
                    Label("\(drift.namingOnly.count) are used for naming but not listed above.",
                          systemImage: "eye.slash")
                        .font(.caption2)
                        .foregroundColor(.secondary)
                        .help(drift.namingOnly.joined(separator: ", "))
                }
            }
            .padding(.bottom, 4)
            Divider()
        }
    }

    private func duplicateRow(_ pair: VoiceLibraryDuplicate) -> some View {
        let isDifferent = pair.verdict == "different"
        return VStack(alignment: .leading, spacing: 3) {
            HStack(spacing: 6) {
                Image(systemName: isDifferent ? "person.2.slash"
                      : pair.verdict == "same" ? "person.2.fill" : "questionmark.circle")
                    .font(.caption)
                    .foregroundColor(isDifferent ? .secondary : pair.verdict == "same" ? .orange : .yellow)
                Text(pair.names.joined(separator: "  +  "))
                    .font(.caption.weight(.medium))
                Spacer(minLength: 6)
                if !isDifferent {
                    Button("Merge") { pendingDuplicateMerge = pair }
                        .buttonStyle(.bordered)
                        .controlSize(.small)
                        .help("Keep \(pair.suggestedKeep) and move \(pair.absorbed)'s samples onto it")
                }
            }
            // The evidence, always visible. A verdict the user cannot check is
            // just an assertion, and these decisions name real people.
            HStack(spacing: 8) {
                if !pair.sharedMeetings.isEmpty {
                    evidenceChip("same recording", .green,
                                 help: "Both profiles were built from "
                                 + pair.sharedMeetings.joined(separator: ", ")
                                 + " — one clip cannot be two people.")
                } else if let mean = pair.meanSimilarity {
                    evidenceChip(String(format: "voice match %.0f%%", mean * 100),
                                 isDifferent ? .secondary : .orange,
                                 help: "Mean similarity between every pair of samples, judged "
                                 + "against how similar each profile is to itself.")
                }
                ForEach(pair.names, id: \.self) { name in
                    let count = pair.sampleCounts[name] ?? 0
                    let stores = (pair.stores[name] ?? []).map {
                        $0 == "naming" ? "naming" : "listed"
                    }
                    evidenceChip("\(name.split(separator: " ").first ?? ""): \(count) sample(s)",
                                 .secondary,
                                 help: "\(name) is in the \(stores.joined(separator: " + ")) library")
                }
            }
            if isDifferent {
                Text("The voices do not match — keep both.")
                    .font(.system(size: 9))
                    .foregroundColor(.secondary.opacity(0.8))
            }
        }
        .padding(8)
        .background(RoundedRectangle(cornerRadius: 6)
            .fill(Color.secondary.opacity(isDifferent ? 0.04 : 0.09)))
    }

    private func evidenceChip(_ text: String, _ tint: Color, help: String) -> some View {
        Text(text)
            .font(.system(size: 9))
            .padding(.horizontal, 5)
            .padding(.vertical, 1)
            .background(Capsule().fill(tint.opacity(0.15)))
            .foregroundColor(tint)
            .help(help)
    }

    private var peopleList: some View {
        let q = search.trimmingCharacters(in: .whitespaces).lowercased()
        let visible = allPeople
            .filter { q.isEmpty || $0.name.lowercased().contains(q) }
            .sorted { $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending }
        return List {
            ForEach(visible) { person in
                HStack(spacing: 8) {
                    if peopleSelectMode {
                        Toggle(
                            "Select \(person.name)",
                            isOn: Binding(
                                get: { peopleSelection.contains(person.name) },
                                set: { selected in
                                    if selected { peopleSelection.insert(person.name) }
                                    else { peopleSelection.remove(person.name) }
                                }
                            )
                        )
                        .labelsHidden()
                        .toggleStyle(.checkbox)
                    }

                    if let onFilterToPerson = onFilterToPerson {
                        Button {
                            onFilterToPerson(person.name)
                        } label: {
                            Image(systemName: "line.3.horizontal.decrease.circle")
                        }
                        .buttonStyle(.borderless)
                        .foregroundColor(.accentColor)
                        .help("Show only meetings \(person.name) is in")
                    }

                    if let onToggleMe = onToggleMe {
                        Button {
                            meName = (meName == person.name) ? nil : person.name
                            onToggleMe(person.name)
                        } label: {
                            Image(systemName: meName == person.name ? "star.fill" : "star")
                        }
                        .buttonStyle(.borderless)
                        .foregroundColor(meName == person.name ? .yellow : .secondary)
                        .help(meName == person.name
                            ? "This is you — tap to unset"
                            : "Mark \(person.name) as Me — pinned to the top of name lists")
                    }

                    Text(person.name)
                        .font(.body)
                        .fontWeight(.medium)
                    if meName == person.name {
                        meBadge()
                    }
                    libraryBadge(person.inMatchingLibrary, label: "Matching", color: .accentColor)
                    libraryBadge(person.inCandidateLibrary, label: "Review", color: .purple)

                    Spacer()

                    Text("\(person.sampleCount) sample\(person.sampleCount == 1 ? "" : "s") · \(person.meetingCount) meeting\(person.meetingCount == 1 ? "" : "s")")
                        .font(.caption)
                        .foregroundColor(.secondary)

                    Button {
                        mergingFrom = person
                        mergeTargetName = defaultMergeTarget(excluding: person, pool: allPeople)
                    } label: {
                        Image(systemName: "arrow.triangle.merge")
                    }
                    .buttonStyle(.borderless)
                    .help("Merge into another person — applies in every library that has \(person.name)")
                }
                .padding(.vertical, 2)
            }
        }
    }

    private func libraryBadge(_ show: Bool, label: String, color: Color) -> some View {
        Group {
            if show {
                Text(label)
                    .font(.caption2.weight(.medium))
                    .foregroundColor(color)
                    .padding(.horizontal, 7)
                    .padding(.vertical, 3)
                    .background(color.opacity(0.12))
                    .clipShape(Capsule())
                    .help(label == "Matching"
                        ? "In the live voice-matching library used for speaker labels"
                        : "In the review-only candidate library used for identity suggestions")
            }
        }
    }

    private func meBadge() -> some View {
        Text("Me")
            .font(.caption2.weight(.semibold))
            .foregroundColor(.orange)
            .padding(.horizontal, 7)
            .padding(.vertical, 3)
            .background(Color.orange.opacity(0.15))
            .clipShape(Capsule())
            .help("This person is you — pinned to the top of name lists")
    }

    // MARK: - Merge

    /// Default merge target for a row action: Me when set (and not the row
    /// being merged), otherwise the first other person in the pool.
    private func defaultMergeTarget(excluding source: VoiceLibrarySpeaker, pool: [VoiceLibrarySpeaker]) -> String {
        if let me = meName, me != source.name, pool.contains(where: { $0.name == me }) {
            return me
        }
        return pool.first(where: { $0.id != source.id })?.name ?? ""
    }

    private func mergeSheet(source: VoiceLibrarySpeaker) -> some View {
        let pool = tab == .people ? allPeople : speakers
        let targets = pool
            .filter { $0.id != source.id }
            .sorted {
                if let me = meName {
                    if $0.name == me { return true }
                    if $1.name == me { return false }
                }
                return $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending
            }
        let detail = tab == .people
            ? "Move all voice samples from “\(source.name)” into another name in every library that contains it (matching and review-only), then remove “\(source.name)”. Use this for duplicates like a first-name-only profile."
            : "Move all voice samples from “\(source.name)” into another library name, then remove “\(source.name)”. Use this for typos (e.g. Wildmsith → Wildsmith)."
        return VStack(alignment: .leading, spacing: 16) {
            Text(tab == .people ? "Merge people" : "Merge speakers")
                .font(.headline)
            Text(detail)
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
                    if tab == .people {
                        commitPersonMerge(from: source, into: mergeTargetName)
                    } else {
                        commitMerge(from: source, into: mergeTargetName)
                    }
                }
                .keyboardShortcut(.defaultAction)
                .disabled(mergeTargetName.isEmpty || mergeTargetName == source.name)
                .buttonStyle(.borderedProminent)
            }
        }
        .padding(20)
        .frame(minWidth: 360)
    }

    /// Merge from the All People tab: the host applies the merge in every
    /// library that contains the source name; local state mirrors that here.
    private func commitPersonMerge(from source: VoiceLibrarySpeaker, into targetName: String) {
        guard !targetName.isEmpty, targetName != source.name else {
            mergingFrom = nil
            return
        }
        onMergePerson?(source.name, targetName)
        if let ti = allPeople.firstIndex(where: { $0.name == targetName }),
           let si = allPeople.firstIndex(where: { $0.id == source.id }) {
            let target = allPeople[ti]
            allPeople[ti] = VoiceLibrarySpeaker(
                id: target.id,
                name: target.name,
                sampleCount: target.sampleCount + source.sampleCount,
                meetingCount: target.meetingCount + source.meetingCount,
                lastUpdated: target.lastUpdated,
                profileStatus: target.profileStatus,
                inMatchingLibrary: target.inMatchingLibrary || source.inMatchingLibrary,
                inCandidateLibrary: target.inCandidateLibrary || source.inCandidateLibrary
            )
            allPeople.remove(at: si)
        } else if let si = allPeople.firstIndex(where: { $0.id == source.id }) {
            // Target not in the union yet — the source was effectively renamed.
            let old = allPeople[si]
            allPeople[si] = VoiceLibrarySpeaker(
                id: targetName,
                name: targetName,
                sampleCount: old.sampleCount,
                meetingCount: old.meetingCount,
                lastUpdated: old.lastUpdated,
                profileStatus: old.profileStatus,
                inMatchingLibrary: old.inMatchingLibrary,
                inCandidateLibrary: old.inCandidateLibrary
            )
        }
        mergingFrom = nil
    }

    /// Default name to keep when pair-merging: deeper profile wins (more
    /// meetings, then more samples); alphabetical on a tie.
    private func defaultPairKeep() -> String {
        let pair = peopleSelection.sorted { $0.localizedCaseInsensitiveCompare($1) == .orderedAscending }
        guard pair.count == 2,
              let a = allPeople.first(where: { $0.name == pair[0] }),
              let b = allPeople.first(where: { $0.name == pair[1] }) else {
            return pair.first ?? ""
        }
        if a.meetingCount != b.meetingCount { return a.meetingCount > b.meetingCount ? a.name : b.name }
        if a.sampleCount != b.sampleCount { return a.sampleCount > b.sampleCount ? a.name : b.name }
        return a.name
    }

    /// Sheet for merging two ticked people: choose which name to keep; the
    /// other is absorbed in every library that contains it.
    private var pairMergeSheet: some View {
        let pair = peopleSelection.sorted { $0.localizedCaseInsensitiveCompare($1) == .orderedAscending }
        let first = pair.first ?? ""
        let second = pair.count > 1 ? pair[1] : ""
        return VStack(alignment: .leading, spacing: 16) {
            Text("Merge people")
                .font(.headline)
            Text("Combine “\(first)” and “\(second)” into one person. All voice samples move to the name you keep — in every library that contains them — and the other name is removed.")
                .font(.callout)
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            Picker("Keep", selection: $pairKeepName) {
                Text(first).tag(first)
                Text(second).tag(second)
            }
            .pickerStyle(.segmented)
            .labelsHidden()

            HStack {
                Spacer()
                Button("Cancel") { pairMerge = false }
                    .keyboardShortcut(.cancelAction)
                Button("Merge") {
                    let keep = pairKeepName
                    let absorb = keep == first ? second : first
                    pairMerge = false
                    peopleSelection.removeAll()
                    peopleSelectMode = false
                    if let source = allPeople.first(where: { $0.name == absorb }) {
                        commitPersonMerge(from: source, into: keep)
                    }
                }
                .keyboardShortcut(.defaultAction)
                .disabled(pairKeepName.isEmpty || first.isEmpty || second.isEmpty)
                .buttonStyle(.borderedProminent)
            }
        }
        .padding(20)
        .frame(minWidth: 380)
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
