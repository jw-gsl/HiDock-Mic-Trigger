import SwiftUI
import AVFoundation

// MARK: - Data Models

struct VoiceClusterData: Identifiable, Codable {
    var id: Int { clusterId }
    let clusterId: Int
    var suggestedName: String?
    let confidence: Double
    let totalTalkTime: Int
    let meetingCount: Int
    let sampleCount: Int
    var samples: [VoiceSampleData]

    enum CodingKeys: String, CodingKey {
        case clusterId = "cluster_id"
        case suggestedName = "suggested_name"
        case confidence
        case totalTalkTime = "total_talk_time"
        case meetingCount = "meeting_count"
        case sampleCount = "sample_count"
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
    let duration: Int
    let textPreview: String

    enum CodingKeys: String, CodingKey {
        case meetingName = "meeting_name"
        case meetingFile = "meeting_file"
        case speakerLabel = "speaker_label"
        case start, end, duration
        case textPreview = "text_preview"
    }
}

// MARK: - Audio Player

class SamplePlayer: ObservableObject {
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

// MARK: - View

struct VoiceTrainingView: View {
    @State var clusters: [VoiceClusterData] = []
    @State var loading = true
    @State var editingClusterId: Int? = nil
    @State var editingName: String = ""
    @StateObject var player = SamplePlayer()
    let onEnroll: (String, String, Double, Double) -> Void  // name, audioPath, start, end
    let onRefresh: (@escaping ([VoiceClusterData]) -> Void) -> Void

    var body: some View {
        VStack(spacing: 0) {
            // Header
            HStack {
                Image(systemName: "person.wave.2")
                    .foregroundColor(.secondary)
                Text("Voice Training")
                    .font(.headline)
                Spacer()

                Text("\(clusters.count) voices across \(totalMeetings) meetings")
                    .font(.caption)
                    .foregroundColor(.secondary)

                Button {
                    loading = true
                    onRefresh { newClusters in
                        clusters = newClusters
                        loading = false
                    }
                } label: {
                    Label("Scan", systemImage: "arrow.clockwise")
                }
                .buttonStyle(.bordered)
                .controlSize(.small)
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 10)
            .background(.ultraThinMaterial)

            Divider()

            if loading {
                VStack {
                    Spacer()
                    ProgressView("Scanning meetings...")
                    Spacer()
                }
            } else if clusters.isEmpty {
                VStack {
                    Spacer()
                    Image(systemName: "person.crop.circle.badge.questionmark")
                        .font(.system(size: 40))
                        .foregroundColor(.secondary)
                    Text("No voice samples found")
                        .foregroundColor(.secondary)
                    Text("Transcribe some meetings with Speaker Labels enabled")
                        .font(.caption)
                        .foregroundColor(.secondary)
                    Spacer()
                }
            } else {
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 12) {
                        ForEach(clusters) { cluster in
                            clusterCard(cluster)
                        }
                    }
                    .padding(16)
                }
            }
        }
        .frame(minWidth: 650, minHeight: 450)
        .onAppear {
            onRefresh { newClusters in
                clusters = newClusters
                loading = false
            }
        }
    }

    private var totalMeetings: Int {
        Set(clusters.flatMap { $0.samples.map(\.meetingName) }).count
    }

    // MARK: - Cluster Card

    @ViewBuilder
    private func clusterCard(_ cluster: VoiceClusterData) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            // Header: name + confidence + stats
            HStack {
                // Editable name
                if editingClusterId == cluster.clusterId {
                    HStack(spacing: 4) {
                        TextField("Name", text: $editingName, onCommit: {
                            confirmName(cluster: cluster)
                        })
                        .textFieldStyle(.roundedBorder)
                        .frame(width: 150)

                        Button("Save") { confirmName(cluster: cluster) }
                            .controlSize(.small)
                        Button("Cancel") { editingClusterId = nil }
                            .controlSize(.small)
                    }
                } else {
                    Button {
                        editingClusterId = cluster.clusterId
                        editingName = cluster.suggestedName ?? ""
                    } label: {
                        HStack(spacing: 6) {
                            Image(systemName: cluster.suggestedName != nil ? "person.crop.circle.fill" : "person.crop.circle.badge.questionmark")
                                .foregroundColor(cluster.suggestedName != nil ? .green : .orange)

                            Text(cluster.suggestedName ?? "Unknown Voice")
                                .font(.headline)

                            if cluster.confidence > 0 {
                                Text("\(Int(cluster.confidence * 100))%")
                                    .font(.caption)
                                    .foregroundColor(.secondary)
                                    .padding(.horizontal, 4)
                                    .padding(.vertical, 1)
                                    .background(Color.secondary.opacity(0.1))
                                    .cornerRadius(4)
                            }
                        }
                    }
                    .buttonStyle(.plain)
                }

                Spacer()

                Text("\(cluster.meetingCount) meetings")
                    .font(.caption)
                    .foregroundColor(.secondary)

                Text(formatDuration(cluster.totalTalkTime))
                    .font(.caption.monospacedDigit())
                    .foregroundColor(.secondary)
            }

            // Samples list — each with play button
            ForEach(cluster.samples) { sample in
                HStack(spacing: 8) {
                    // Play button
                    Button {
                        if player.playingId == sample.id {
                            player.stop()
                        } else {
                            player.play(
                                audioPath: sample.meetingFile,
                                start: sample.start,
                                end: sample.end,
                                sampleId: sample.id
                            )
                        }
                    } label: {
                        Image(systemName: player.playingId == sample.id ? "stop.circle.fill" : "play.circle")
                            .font(.title3)
                            .foregroundColor(player.playingId == sample.id ? .blue : .accentColor)
                    }
                    .buttonStyle(.plain)

                    VStack(alignment: .leading, spacing: 2) {
                        HStack(spacing: 4) {
                            Text(sample.meetingName)
                                .font(.caption.weight(.medium))
                                .lineLimit(1)

                            Text("[\(formatTime(sample.start))-\(formatTime(sample.end))]")
                                .font(.caption2.monospacedDigit())
                                .foregroundColor(.secondary)

                            Text("\(sample.duration)s talk")
                                .font(.caption2)
                                .foregroundColor(.secondary)
                        }

                        Text(sample.textPreview)
                            .font(.caption)
                            .foregroundColor(.secondary)
                            .lineLimit(1)
                    }

                    Spacer()
                }
                .padding(.leading, 24)
            }
        }
        .padding(12)
        .background(Color.secondary.opacity(0.04))
        .cornerRadius(8)
        .overlay(
            RoundedRectangle(cornerRadius: 8)
                .stroke(cluster.suggestedName != nil ? Color.green.opacity(0.3) : Color.orange.opacity(0.3), lineWidth: 1)
        )
    }

    // MARK: - Actions

    private func confirmName(cluster: VoiceClusterData) {
        let name = editingName.trimmingCharacters(in: .whitespaces)
        guard !name.isEmpty else {
            editingClusterId = nil
            return
        }

        // Update the cluster name
        if let idx = clusters.firstIndex(where: { $0.clusterId == cluster.clusterId }) {
            clusters[idx].suggestedName = name
        }

        // Enroll each sample under this name
        for sample in cluster.samples {
            onEnroll(name, sample.meetingFile, sample.start, sample.end)
        }

        editingClusterId = nil
    }

    // MARK: - Formatting

    private func formatTime(_ seconds: Double) -> String {
        let total = Int(seconds)
        let m = total / 60
        let s = total % 60
        return String(format: "%d:%02d", m, s)
    }

    private func formatDuration(_ seconds: Int) -> String {
        if seconds < 60 { return "\(seconds)s" }
        let m = seconds / 60
        let s = seconds % 60
        if s == 0 { return "\(m)min" }
        return "\(m)m\(s)s"
    }
}
