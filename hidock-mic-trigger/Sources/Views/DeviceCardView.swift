import SwiftUI

/// One card per paired HiDock / volume, plus a lightweight variant for
/// imported recordings. Collapses what used to be four scattered widgets
/// (status dot on a filter chip, floating Recording pill, storage row,
/// reconnect icon) into a single panel so every fact about a device is
/// in one place.
///
/// The card has three possible states it communicates in its title chip:
///   - ✓ Connected (green)
///   - 🔴 Recording (red, pulsing) — mic-trigger is streaming from a HiDock
///   - ⚠ Unreachable (orange) — last status query failed, last-known data shown
///
/// Storage is rendered as a progress bar so headroom is visible at a glance.
/// Reconnect and Filter actions sit on the right of the card.
struct DeviceCardView: View {
    @ObservedObject var viewModel: HiDockViewModel
    let device: HiDockPairedDevice

    private var isActiveFilter: Bool {
        viewModel.syncFilterDeviceId == device.deviceId
    }

    private var connected: Bool {
        viewModel.syncDeviceConnected[device.deviceId] ?? false
    }

    private var lastError: (String, Date)? {
        viewModel.syncDeviceLastError[device.deviceId]
    }

    private var unreachable: Bool {
        lastError != nil
    }

    /// The mic-trigger's ffmpeg currently holds a HiDock open. Today this
    /// is device-agnostic (we don't know which HiDock). Only flag the card
    /// as "Recording" for HiDocks — volumes can't be a trigger target.
    private var recording: Bool {
        viewModel.hidockRecordingActive && device.deviceType == .hidock
    }

    private var stats: HiDockStorageStats? {
        viewModel.syncDeviceStorage[device.deviceId]
    }

    /// Known capacities. Matches the table in HiDockViewModel.storageSummary —
    /// kept in-sync manually until we have a device-info query in the protocol.
    private var capacityBytes: Int64? {
        switch device.shortName {
        case "H1", "H1E": return 32 * 1_073_741_824
        case "P1":        return 64 * 1_073_741_824
        default:          return nil
        }
    }

    var body: some View {
        HStack(alignment: .center, spacing: 12) {
            iconView
                .frame(width: 44, height: 44)

            VStack(alignment: .leading, spacing: 6) {
                titleRow
                if stats != nil || capacityBytes != nil {
                    storageRow
                }
                if unreachable, let (msg, when) = lastError {
                    unreachableNote(msg: msg, when: when)
                }
            }

            Spacer(minLength: 0)

            actionsColumn
        }
        .padding(10)
        .background(
            RoundedRectangle(cornerRadius: 8)
                .fill(cardBackground)
        )
        .overlay(
            RoundedRectangle(cornerRadius: 8)
                .stroke(cardBorder, lineWidth: isActiveFilter ? 2 : 1)
        )
    }

    // MARK: - Sub-views

    @ViewBuilder
    private var iconView: some View {
        if let img = hidockDeviceImage(device.shortName, deviceType: device.deviceType, recording: recording) {
            img
                .resizable()
                .scaledToFit()
        } else {
            Image(systemName: hidockDeviceIcon(device.shortName, deviceType: device.deviceType))
                .font(.title)
                .foregroundColor(.secondary)
        }
    }

    private var titleRow: some View {
        HStack(spacing: 8) {
            // cleanName strips the raw USB product string (e.g.
            // "actions-BOS-000") and surfaces the human "HiDock H1" form.
            // displayName would show the raw string.
            Text(device.cleanName)
                .font(.headline)
            stateChip
        }
    }

    /// One of three mutually-exclusive chips — precedence: Unreachable > Recording > Connected.
    @ViewBuilder
    private var stateChip: some View {
        if unreachable {
            chip(systemImage: "exclamationmark.triangle.fill",
                 text: "Unreachable",
                 foreground: .orange,
                 background: Color.orange.opacity(0.15))
        } else if recording {
            chip(systemImage: "record.circle.fill",
                 text: "Recording",
                 foreground: .red,
                 background: Color.red.opacity(0.12))
        } else if connected {
            chip(systemImage: "checkmark.circle.fill",
                 text: "Connected",
                 foreground: .green,
                 background: Color.green.opacity(0.12))
        } else {
            chip(systemImage: "circle.slash",
                 text: "Not connected",
                 foreground: .secondary,
                 background: Color.secondary.opacity(0.12))
        }
    }

    private func chip(systemImage: String, text: String, foreground: Color, background: Color) -> some View {
        HStack(spacing: 4) {
            Image(systemName: systemImage)
            Text(text)
        }
        .font(.caption.weight(.medium))
        .foregroundColor(foreground)
        .padding(.horizontal, 6)
        .padding(.vertical, 2)
        .background(background, in: Capsule())
    }

    private var storageRow: some View {
        let usedBytes = Int64(stats?.totalBytesReturned ?? 0)
        let capacity = capacityBytes
        let progress: Double? = capacity.map {
            min(1.0, Double(usedBytes) / Double($0))
        }
        let usedGB = Double(usedBytes) / 1_073_741_824
        let capGB  = capacity.map { Double($0) / 1_073_741_824 }

        return VStack(alignment: .leading, spacing: 2) {
            if let progress = progress, let capGB = capGB {
                ProgressView(value: progress)
                    .progressViewStyle(.linear)
                    .tint(progress > 0.85 ? .orange : .accentColor)
                HStack(spacing: 6) {
                    Text(formatStorage(used: usedGB, capacity: capGB, truncated: stats?.truncated ?? false))
                        .font(.caption.monospacedDigit())
                        .foregroundColor(.secondary)
                    if let files = stats?.totalFiles {
                        Text("· \(files) files")
                            .font(.caption)
                            .foregroundColor(.secondary)
                    }
                    Spacer(minLength: 0)
                }
            } else if let stats = stats {
                Text("\(String(format: "%.1f", usedGB)) GB · \(stats.totalFiles) files")
                    .font(.caption.monospacedDigit())
                    .foregroundColor(.secondary)
            }
        }
    }

    private func formatStorage(used: Double, capacity: Double, truncated: Bool) -> String {
        let free = max(0, capacity - used)
        let freeLabel = truncated ? "≤\(String(format: "%.0f", free)) GB free" : "\(String(format: "%.0f", free)) GB free"
        return "\(String(format: "%.1f", used))\(truncated ? "+" : "") / \(String(format: "%.0f", capacity)) GB · \(freeLabel)"
    }

    private func unreachableNote(msg: String, when: Date) -> some View {
        let f = DateFormatter()
        f.dateFormat = "HH:mm"
        let short = msg.split(separator: "—").first.map(String.init)?
            .trimmingCharacters(in: .whitespaces) ?? msg
        return Text("\(short) @ \(f.string(from: when))")
            .font(.caption2)
            .foregroundColor(.orange)
    }

    private var actionsColumn: some View {
        VStack(spacing: 4) {
            Button {
                viewModel.onReconnectDevice(device.deviceId)
            } label: {
                Image(systemName: unreachable ? "arrow.clockwise.circle.fill" : "arrow.clockwise.circle")
                    .font(.title3)
                    .foregroundColor(unreachable ? .orange : .accentColor)
            }
            .buttonStyle(.plain)
            .help(unreachable ? "\(device.shortName) is unreachable — try reconnecting" : "Reconnect \(device.shortName)")

            Button {
                // Toggle this card as the active filter; clicking an already-
                // active filter clears it.
                viewModel.onFilterByDevice(isActiveFilter ? nil : device.deviceId)
            } label: {
                Image(systemName: isActiveFilter ? "line.3.horizontal.decrease.circle.fill" : "line.3.horizontal.decrease.circle")
                    .font(.title3)
                    .foregroundColor(isActiveFilter ? .accentColor : .secondary)
            }
            .buttonStyle(.plain)
            .help(isActiveFilter ? "Showing only \(device.shortName) — click to clear filter" : "Filter table to \(device.shortName)")
        }
    }

    private var cardBackground: Color {
        if unreachable { return Color.orange.opacity(0.05) }
        if recording   { return Color.red.opacity(0.04) }
        return Color(nsColor: .controlBackgroundColor).opacity(0.6)
    }

    private var cardBorder: Color {
        if isActiveFilter { return .accentColor }
        if unreachable    { return Color.orange.opacity(0.4) }
        return Color.secondary.opacity(0.2)
    }
}

/// Lightweight card for the "Imported files" virtual device — no storage,
/// no reconnect, no recording. Just a count, filter, and an import button.
struct ImportsCardView: View {
    @ObservedObject var viewModel: HiDockViewModel

    private var isActiveFilter: Bool {
        viewModel.syncFilterDeviceId == IMPORTED_DEVICE_ID
    }

    private var importedEntries: [HiDockSyncRecordingEntry] {
        viewModel.syncEntries.filter { $0.deviceId == IMPORTED_DEVICE_ID }
    }

    var body: some View {
        HStack(alignment: .center, spacing: 12) {
            Image(systemName: "shippingbox")
                .font(.title)
                .foregroundColor(.secondary)
                .frame(width: 44, height: 44)

            VStack(alignment: .leading, spacing: 4) {
                Text("Imported files")
                    .font(.headline)
                Text("\(importedEntries.count) recording\(importedEntries.count == 1 ? "" : "s")")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }

            Spacer(minLength: 0)

            VStack(spacing: 4) {
                Button {
                    viewModel.onImportAudioFile()
                } label: {
                    Image(systemName: "square.and.arrow.down")
                        .font(.title3)
                        .foregroundColor(.accentColor)
                }
                .buttonStyle(.plain)
                .help("Import an audio or video file")

                Button {
                    viewModel.onFilterByDevice(isActiveFilter ? nil : IMPORTED_DEVICE_ID)
                } label: {
                    Image(systemName: isActiveFilter ? "line.3.horizontal.decrease.circle.fill" : "line.3.horizontal.decrease.circle")
                        .font(.title3)
                        .foregroundColor(isActiveFilter ? .accentColor : .secondary)
                }
                .buttonStyle(.plain)
                .help(isActiveFilter ? "Showing only imports — click to clear filter" : "Filter table to imports")
            }
        }
        .padding(10)
        .background(
            RoundedRectangle(cornerRadius: 8)
                .fill(Color(nsColor: .controlBackgroundColor).opacity(0.6))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 8)
                .stroke(isActiveFilter ? Color.accentColor : Color.secondary.opacity(0.2), lineWidth: isActiveFilter ? 2 : 1)
        )
    }
}

/// Stacks all paired-device cards plus an imports card when relevant.
/// Sits where the old status/storage/filter rows used to be.
struct DeviceStripView: View {
    @ObservedObject var viewModel: HiDockViewModel

    private var hasImports: Bool {
        viewModel.syncEntries.contains(where: { $0.deviceId == IMPORTED_DEVICE_ID })
    }

    var body: some View {
        VStack(spacing: 6) {
            ForEach(viewModel.syncPairedDevices, id: \.deviceId) { device in
                DeviceCardView(viewModel: viewModel, device: device)
            }
            if hasImports {
                ImportsCardView(viewModel: viewModel)
            }
        }
    }
}
