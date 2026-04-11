import AppKit
import SwiftUI
import AVFoundation

// MARK: - Data Models

struct VoiceClusterData: Identifiable, Codable {
    var id: Int { clusterId }
    let clusterId: Int
    var suggestedName: String?
    let confidence: Double
    var confirmed: Bool
    let enrolled: Bool
    let totalTalkTime: Int
    let meetingCount: Int
    let sampleCount: Int
    let meetings: [String]
    let enrolledSpeakers: [String]
    var samples: [VoiceSampleData]

    enum CodingKeys: String, CodingKey {
        case clusterId = "cluster_id"
        case suggestedName = "suggested_name"
        case confidence, confirmed, enrolled
        case totalTalkTime = "total_talk_time"
        case meetingCount = "meeting_count"
        case sampleCount = "sample_count"
        case meetings
        case enrolledSpeakers = "enrolled_speakers"
        case samples
    }
}

struct VoiceSampleData: Identifiable, Codable {
    var id: String { "\(meetingName)-\(start)" }
    let meetingName: String
    let meetingFile: String
    let speakerLabel: String
    let start: Double
    let end: Double
    let duration: Double
    let totalTalkTime: Int
    let textPreview: String
    let qualityScore: Double

    enum CodingKeys: String, CodingKey {
        case meetingName = "meeting_name"
        case meetingFile = "meeting_file"
        case speakerLabel = "speaker_label"
        case start, end, duration
        case totalTalkTime = "total_talk_time"
        case textPreview = "text_preview"
        case qualityScore = "quality_score"
    }
}

// MARK: - Audio Player

class TrainingSamplePlayer: ObservableObject {
    @Published var playingId: String?
    private var player: AVAudioPlayer?
    private var timer: Timer?

    func play(audioPath: String, start: Double, end: Double, sampleId: String) {
        stop()
        guard let url = URL(string: "file://\(audioPath)"),
              let player = try? AVAudioPlayer(contentsOf: url) else { return }
        self.player = player
        player.currentTime = start
        player.play()
        playingId = sampleId
        timer = Timer.scheduledTimer(withTimeInterval: end - start, repeats: false) { [weak self] _ in
            self?.stop()
        }
    }

    func stop() {
        player?.stop()
        player = nil
        timer?.invalidate()
        timer = nil
        playingId = nil
    }
}

// MARK: - Voice Training View

struct VoiceTrainingView: View {
    @State var clusters: [VoiceClusterData] = []
    @State var loading = true
    @State var editingClusterId: Int? = nil
    @State var editingName: String = ""
    @StateObject var player = TrainingSamplePlayer()
    let onEnroll: (String, String, Double, Double) -> Void
    let onRefresh: (@escaping ([VoiceClusterData]) -> Void) -> Void

    var body: some View {
        VStack(spacing: 0) {
            headerBar
            Divider()

            if loading {
                VStack { Spacer(); ProgressView("Scanning meetings..."); Spacer() }
            } else if clusters.isEmpty {
                emptyState
            } else {
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 16) {
                        let unconfirmed = clusters.filter { !$0.confirmed }
                        let confirmed = clusters.filter { $0.confirmed }

                        if !unconfirmed.isEmpty {
                            sectionHeader("Needs Review", count: unconfirmed.count, color: .orange)
                            ForEach(unconfirmed) { cluster in
                                clusterCard(cluster)
                            }
                        }

                        if !confirmed.isEmpty {
                            sectionHeader("Confirmed", count: confirmed.count, color: .green)
                            ForEach(confirmed) { cluster in
                                clusterCard(cluster)
                            }
                        }
                    }
                    .padding(16)
                }
            }
        }
        .frame(minWidth: 700, minHeight: 500)
        .onAppear {
            onRefresh { clusters = $0; loading = false }
        }
    }

    // MARK: - Header

    private var headerBar: some View {
        HStack {
            Image(systemName: "person.wave.2")
                .foregroundColor(.accentColor)
            Text("Voice Training")
                .font(.headline)

            Spacer()

            let unconfirmed = clusters.filter { !$0.confirmed }.count
            if unconfirmed > 0 {
                Label("\(unconfirmed) need review", systemImage: "exclamationmark.circle")
                    .font(.caption)
                    .foregroundColor(.orange)
            }

            Text("\(clusters.count) voices · \(totalMeetings) meetings")
                .font(.caption)
                .foregroundColor(.secondary)

            Button {
                loading = true
                onRefresh { clusters = $0; loading = false }
            } label: {
                Label("Scan", systemImage: "arrow.clockwise")
            }
            .buttonStyle(.bordered)
            .controlSize(.small)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .background(.ultraThinMaterial)
    }

    private var totalMeetings: Int {
        Set(clusters.flatMap(\.meetings)).count
    }

    private var emptyState: some View {
        VStack(spacing: 8) {
            Spacer()
            Image(systemName: "person.crop.circle.badge.questionmark")
                .font(.system(size: 40))
                .foregroundColor(.secondary)
            Text("No voice samples found")
                .font(.headline)
                .foregroundColor(.secondary)
            Text("Transcribe meetings with Speaker Labels enabled, then scan again")
                .font(.caption)
                .foregroundColor(.secondary)
            Spacer()
        }
    }

    // MARK: - Section Header

    @ViewBuilder
    private func sectionHeader(_ title: String, count: Int, color: Color) -> some View {
        HStack {
            Circle().fill(color).frame(width: 8, height: 8)
            Text(title).font(.subheadline.weight(.semibold))
            Text("(\(count))").font(.caption).foregroundColor(.secondary)
            Spacer()
        }
        .padding(.top, 8)
    }

    // MARK: - Cluster Card

    @ViewBuilder
    private func clusterCard(_ cluster: VoiceClusterData) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                Image(systemName: cluster.confirmed ? "checkmark.circle.fill" : "questionmark.circle")
                    .foregroundColor(cluster.confirmed ? .green : .orange)
                    .font(.title3)

                if editingClusterId == cluster.clusterId {
                    nameEditor(cluster: cluster)
                } else {
                    Button {
                        editingClusterId = cluster.clusterId
                        editingName = cluster.suggestedName ?? ""
                    } label: {
                        VStack(alignment: .leading, spacing: 1) {
                            Text(cluster.suggestedName ?? "Unknown Voice")
                                .font(.body.weight(.medium))
                            if cluster.confidence > 0 {
                                Text("Confidence: \(Int(cluster.confidence * 100))%")
                                    .font(.caption2)
                                    .foregroundColor(.secondary)
                            }
                        }
                    }
                    .buttonStyle(.plain)
                }

                Spacer()

                VStack(alignment: .trailing, spacing: 1) {
                    Text("\(cluster.meetingCount) meetings")
                        .font(.caption)
                        .foregroundColor(.secondary)
                    Text(formatDuration(cluster.totalTalkTime))
                        .font(.caption.monospacedDigit())
                        .foregroundColor(.secondary)
                }

                if !cluster.confirmed {
                    Button {
                        confirmCluster(cluster)
                    } label: {
                        Label("Confirm", systemImage: "checkmark")
                    }
                    .buttonStyle(.bordered)
                    .controlSize(.small)
                    .tint(.green)
                    .disabled(cluster.suggestedName == nil)
                }
            }

            ForEach(cluster.samples) { sample in
                sampleRow(sample: sample, cluster: cluster)
            }
        }
        .padding(12)
        .background(cluster.confirmed ? Color.green.opacity(0.03) : Color.orange.opacity(0.03))
        .cornerRadius(10)
        .overlay(
            RoundedRectangle(cornerRadius: 10)
                .stroke(cluster.confirmed ? Color.green.opacity(0.2) : Color.orange.opacity(0.2), lineWidth: 1)
        )
    }

    // MARK: - Name Editor

    @ViewBuilder
    private func nameEditor(cluster: VoiceClusterData) -> some View {
        HStack(spacing: 6) {
            TextField("Speaker name", text: $editingName, onCommit: {
                saveName(cluster: cluster)
            })
            .textFieldStyle(.roundedBorder)
            .frame(width: 160)

            if !cluster.enrolledSpeakers.isEmpty {
                Menu {
                    ForEach(cluster.enrolledSpeakers, id: \.self) { name in
                        Button(name) {
                            editingName = name
                            saveName(cluster: cluster)
                        }
                    }
                } label: {
                    Image(systemName: "person.crop.circle.badge.plus")
                }
                .menuStyle(.borderlessButton)
                .frame(width: 24)
                .help("Assign to known speaker")
            }

            Button("Save") { saveName(cluster: cluster) }
                .controlSize(.small)
            Button("Cancel") { editingClusterId = nil }
                .controlSize(.small)
        }
    }

    // MARK: - Sample Row

    @ViewBuilder
    private func sampleRow(sample: VoiceSampleData, cluster: VoiceClusterData) -> some View {
        HStack(spacing: 8) {
            Button {
                if player.playingId == sample.id {
                    player.stop()
                } else {
                    player.play(audioPath: sample.meetingFile, start: sample.start, end: sample.end, sampleId: sample.id)
                }
            } label: {
                Image(systemName: player.playingId == sample.id ? "stop.circle.fill" : "play.circle.fill")
                    .font(.title2)
                    .foregroundColor(player.playingId == sample.id ? .red : .accentColor)
            }
            .buttonStyle(.plain)

            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Text(sample.meetingName)
                        .font(.caption.weight(.medium))
                        .lineLimit(1)

                    Text("[\(formatTime(sample.start))]")
                        .font(.caption2.monospacedDigit())
                        .foregroundColor(.secondary)

                    if sample.qualityScore > 0.8 {
                        Image(systemName: "star.fill")
                            .font(.caption2)
                            .foregroundColor(.yellow)
                    }
                }

                Text(sample.textPreview)
                    .font(.caption)
                    .foregroundColor(.secondary)
                    .lineLimit(2)
            }

            Spacer()

            // Per-sample reassignment
            Menu {
                if !cluster.enrolledSpeakers.isEmpty {
                    Section("Assign to...") {
                        ForEach(cluster.enrolledSpeakers, id: \.self) { name in
                            Button(name) {
                                onEnroll(name, sample.meetingFile, sample.start, sample.end)
                            }
                        }
                    }
                }
                Button("New person...") {
                    editingClusterId = cluster.clusterId
                    editingName = ""
                }
                Divider()
                Button("Wrong cluster") {
                    // Remove from this cluster visually
                    if let idx = clusters.firstIndex(where: { $0.clusterId == cluster.clusterId }) {
                        clusters[idx].samples.removeAll { $0.id == sample.id }
                    }
                }
            } label: {
                Image(systemName: "arrow.triangle.branch")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
            .menuStyle(.borderlessButton)
            .frame(width: 20)
            .help("Reassign this sample")
        }
        .padding(.leading, 28)
        .padding(.vertical, 2)
    }

    // MARK: - Actions

    private func saveName(cluster: VoiceClusterData) {
        let name = editingName.trimmingCharacters(in: .whitespaces)
        guard !name.isEmpty else { editingClusterId = nil; return }

        if let idx = clusters.firstIndex(where: { $0.clusterId == cluster.clusterId }) {
            clusters[idx].suggestedName = name
        }

        for sample in cluster.samples {
            onEnroll(name, sample.meetingFile, sample.start, sample.end)
        }

        editingClusterId = nil
    }

    private func confirmCluster(_ cluster: VoiceClusterData) {
        guard cluster.suggestedName != nil else { return }

        if let idx = clusters.firstIndex(where: { $0.clusterId == cluster.clusterId }) {
            clusters[idx].confirmed = true
        }

        for sample in cluster.samples {
            if let name = cluster.suggestedName {
                onEnroll(name, sample.meetingFile, sample.start, sample.end)
            }
        }
    }

    // MARK: - Formatting

    private func formatTime(_ seconds: Double) -> String {
        let total = Int(seconds)
        return String(format: "%d:%02d", total / 60, total % 60)
    }

    private func formatDuration(_ seconds: Int) -> String {
        if seconds < 60 { return "\(seconds)s" }
        let m = seconds / 60
        return seconds % 60 == 0 ? "\(m)min" : "\(m)m\(seconds % 60)s"
    }
}
