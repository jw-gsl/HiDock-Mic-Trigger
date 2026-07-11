import SwiftUI

// MARK: - Data Model

struct VoiceLibrarySpeaker: Identifiable {
    let id: String
    let name: String
    let sampleCount: Int
    let lastUpdated: String
}

// MARK: - VoiceLibraryView

struct VoiceLibraryView: View {
    @State var speakers: [VoiceLibrarySpeaker]
    @State private var editingId: String? = nil
    @State private var editingName: String = ""
    let onDelete: (String) -> Void
    let onRename: (String, String) -> Void

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

            if speakers.isEmpty {
                emptyState
            } else {
                speakerList
            }
        }
        .frame(minWidth: 400, minHeight: 300)
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
            ForEach(speakers) { speaker in
                HStack {
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

                    Text("\(speaker.sampleCount) sample\(speaker.sampleCount == 1 ? "" : "s")")
                        .font(.caption)
                        .foregroundColor(.secondary)

                    if !speaker.lastUpdated.isEmpty {
                        Text(formatDate(speaker.lastUpdated))
                            .font(.caption)
                            .foregroundColor(.secondary)
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

    // MARK: - Actions

    private func commitRename(speaker: VoiceLibrarySpeaker) {
        let trimmed = editingName.trimmingCharacters(in: .whitespaces)
        guard !trimmed.isEmpty, trimmed != speaker.name else {
            editingId = nil
            return
        }
        onRename(speaker.name, trimmed)
        // Update local state
        if let index = speakers.firstIndex(where: { $0.id == speaker.id }) {
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
