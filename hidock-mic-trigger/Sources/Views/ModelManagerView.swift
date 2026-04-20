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
                    LazyVStack(spacing: 0) {
                        ForEach(sortedModelKeys, id: \.self) { key in
                            if let status = viewModel.modelStatuses[key] {
                                ModelRowView(
                                    status: status,
                                    onDownload: { viewModel.onDownloadModelByKey(key) },
                                    onDelete: { viewModel.onDeleteModelByKey(key) }
                                )
                                Divider()
                                    .padding(.horizontal, 16)
                            }
                        }
                    }
                    .padding(.vertical, 8)
                }
            }
        }
        .frame(minWidth: 480, minHeight: 300)
    }

    /// Sort models: required first, then alphabetically by name.
    private var sortedModelKeys: [String] {
        let keys = Array(viewModel.modelStatuses.keys)
        return keys.sorted { a, b in
            let sa = viewModel.modelStatuses[a]!
            let sb = viewModel.modelStatuses[b]!
            if sa.sizeMB != sb.sizeMB {
                // Largest first (whisper at top)
                return sa.sizeMB > sb.sizeMB
            }
            return sa.name < sb.name
        }
    }
}

struct ModelRowView: View {
    let status: ModelStatus
    let onDownload: () -> Void
    let onDelete: () -> Void

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            // Icon
            Image(systemName: status.installed ? "checkmark.circle.fill" : "arrow.down.circle")
                .font(.title2)
                .foregroundColor(status.installed ? .green : .secondary)
                .frame(width: 28)
                .padding(.top, 2)

            // Text content
            VStack(alignment: .leading, spacing: 4) {
                HStack {
                    Text(status.name)
                        .font(.headline)
                    Spacer()
                    Text(formatSize(mb: status.sizeMB))
                        .font(.callout)
                        .foregroundColor(.secondary)
                }

                Text(status.description)
                    .font(.caption)
                    .foregroundColor(.secondary)

                if status.downloading {
                    ProgressView(value: status.progress)
                        .progressViewStyle(.linear)
                    Text("Downloading... \(Int(status.progress * 100))%")
                        .font(.caption2)
                        .foregroundColor(.secondary)
                }
            }

            // Action button
            VStack {
                if status.downloading {
                    ProgressView()
                        .controlSize(.small)
                } else if status.installed {
                    VStack(spacing: 4) {
                        Text("Installed")
                            .font(.caption)
                            .foregroundColor(.green)
                        Button(role: .destructive) {
                            onDelete()
                        } label: {
                            Text("Delete")
                                .font(.caption)
                        }
                        .buttonStyle(.bordered)
                        .controlSize(.small)
                    }
                } else {
                    Button {
                        onDownload()
                    } label: {
                        Text("Download")
                            .font(.caption)
                    }
                    .buttonStyle(.bordered)
                    .controlSize(.small)
                }
            }
            .frame(width: 80)
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 12)
    }
}
