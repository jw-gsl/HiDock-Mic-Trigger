import SwiftUI

/// One card per paired HiDock / volume, plus a lightweight variant for
/// imported recordings. Collapses what used to be four scattered widgets
/// (status dot on a filter chip, floating Recording pill, storage row,
/// reconnect icon) into a single panel so every fact about a device is
/// in one place.
///
/// The card communicates state via a compact **icon-only** title chip
/// (tooltip holds the label — text was removed because the labels + the
/// connecting spinner were reflowing and distracting when the detail pane
/// was open):
///   - ✓ Connected (green)
///   - 🔴 Recording (red) — mic-trigger is streaming from a HiDock
///   - ⚠ Unreachable (orange) — last status query failed, last-known data shown
///   - ↻ Connecting (blue, spinning) — probe in flight
///   - ⊘ Not connected (secondary)
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

    private var plaudSignedOut: Bool {
        guard device.deviceType == .plaud, let (msg, _) = lastError else { return false }
        return msg.localizedCaseInsensitiveContains("not signed in")
    }

    /// Region to re-authenticate this Plaud account against (its last-known
    /// region, defaulting to US). Used by the "Sign in required" affordances.
    private var plaudRegion: String { device.plaudRegion ?? "us" }

    /// The mic-trigger's ffmpeg currently holds a HiDock open.
    /// Pinned to the specific device it's attached to via the CLI
    /// output line "Using HiDock audio device: <name>". If we haven't
    /// parsed a name yet (trigger just starting) we fall back to the
    /// old device-agnostic flag so the chip still shows *somewhere*
    /// rather than nowhere.
    private var recording: Bool {
        guard viewModel.hidockRecordingActive, device.deviceType == .hidock else { return false }
        if let attached = viewModel.hidockRecordingDeviceName {
            return device.cleanName.caseInsensitiveCompare(attached) == .orderedSame
        }
        return true
    }

    private var stats: HiDockStorageStats? {
        viewModel.syncDeviceStorage[device.deviceId]
    }

    /// Known capacities. Matches the table in HiDockViewModel.storageSummary —
    /// kept in-sync manually until we have a device-info query in the protocol.
    private var capacityBytes: Int64? {
        // Plaud Note Pro ships with 64 GB of onboard storage. The app can't yet
        // tell Plaud models apart (no model field on the account), so assume Pro.
        if device.deviceType == .plaud {
            return 64 * 1_073_741_824
        }
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
                // Always reserve the storage strip so disconnected cards
                // stay the same height as connected ones. When offline we
                // draw an empty bar + a blank caption line (no "0 GB free"
                // lie); live stats only appear while connected.
                storageRow
                if unreachable, let (msg, when) = lastError {
                    unreachableNote(msg: msg, when: when)
                }
            }

            Spacer(minLength: 0)

            actionsColumn
        }
        .padding(10)
        // Fill the grid column so every card is the same width.
        .frame(maxWidth: .infinity, alignment: .leading)
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
        if device.deviceType == .volume {
            // Real Finder icon for the mounted volume — differentiates
            // multiple external drives visually (branded SD-card icon vs.
            // generic external SSD, etc.) instead of every volume showing
            // the same grey externaldrive SF Symbol.
            volumeIcon
        } else if let img = hidockDeviceImage(device.shortName, deviceType: device.deviceType, recording: recording) {
            // Product-photo asset (DeviceRecording*) — H1, H1E and P1 each
            // have distinct artwork, so no extra badge is needed to tell
            // them apart.
            img
                .resizable()
                .scaledToFit()
        } else if let glyph = hidockDeviceGlyph(device.shortName, deviceType: device.deviceType) {
            glyph
                .resizable()
                .scaledToFit()
                .foregroundColor(.secondary)
                .padding(6)
        } else {
            Image(systemName: hidockDeviceIcon(device.shortName, deviceType: device.deviceType))
                .font(.title)
                .foregroundColor(.secondary)
        }
    }

    @ViewBuilder
    private var volumeIcon: some View {
        if let name = device.volumeName,
           let nsImg = Self.fetchVolumeIcon(name: name) {
            Image(nsImage: nsImg)
                .resizable()
                .scaledToFit()
        } else {
            Image(systemName: "externaldrive")
                .font(.title)
                .foregroundColor(.secondary)
        }
    }

    /// Fetches the Finder icon for a mounted volume at /Volumes/<name>.
    /// Returns nil if the volume isn't mounted right now, which is fine —
    /// by then the card probably shouldn't render anyway (filtered out in
    /// DeviceStripView.visibleDevices).
    private static func fetchVolumeIcon(name: String) -> NSImage? {
        let path = "/Volumes/\(name)"
        guard FileManager.default.fileExists(atPath: path) else { return nil }
        return NSWorkspace.shared.icon(forFile: path)
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

    /// True if we've never heard back about this device (no success,
    /// no failure) AND a refresh is currently running — the probe is
    /// in flight. The chip transitions from Connecting → Connected /
    /// Unreachable when the probe resolves. Distinguishes an
    /// in-progress check from a device that's genuinely been declared
    /// "Not connected" so a physically-plugged-in HiDock doesn't
    /// briefly look absent during launch.
    private var connecting: Bool {
        // HiDock (USB) and Plaud (cloud) both have a slow async probe where the
        // "Connecting…" chip is worth showing on launch. Volume is a fast local
        // mount check, so it resolves before a chip would register.
        guard device.deviceType == .hidock || device.deviceType == .plaud else { return false }
        let hadSuccess = viewModel.syncDeviceLastOK[device.deviceId] != nil
        let hadFailure = viewModel.syncDeviceLastError[device.deviceId] != nil
        return !hadSuccess && !hadFailure && viewModel.syncBusy
    }

    /// One of five mutually-exclusive chips — precedence:
    /// Sign in > Unreachable > Recording > Connected > Connecting > Not connected.
    /// Icons only; full label lives in the tooltip so the chip never reflows.
    @ViewBuilder
    private var stateChip: some View {
        if plaudSignedOut {
            // The chip is the obvious thing to click when signed out — make it
            // actually launch the Plaud sign-in rather than just label the state.
            Button {
                viewModel.onPairPlaud(plaudRegion)
            } label: {
                chip(systemImage: "person.crop.circle.badge.exclamationmark",
                     help: "Sign in required — click to sign in to Plaud",
                     foreground: .orange,
                     background: Color.orange.opacity(0.15))
            }
            .buttonStyle(.plain)
            .help("Sign in to Plaud")
        } else if unreachable {
            chip(systemImage: "exclamationmark.triangle.fill",
                 help: "Unreachable",
                 foreground: .orange,
                 background: Color.orange.opacity(0.15))
        } else if recording {
            chip(systemImage: "record.circle.fill",
                 help: "Recording",
                 foreground: .red,
                 background: Color.red.opacity(0.12))
        } else if connected {
            chip(systemImage: "checkmark.circle.fill",
                 help: "Connected",
                 foreground: .green,
                 background: Color.green.opacity(0.12))
        } else if connecting {
            AnimatedConnectingChip()
        } else {
            chip(systemImage: "circle.slash",
                 help: "Not connected",
                 foreground: .secondary,
                 background: Color.secondary.opacity(0.12))
        }
    }

    /// Compact icon-only state pill. Label is tooltip-only so different
    /// states stay the same size and never shove neighbouring layout around.
    private func chip(systemImage: String, help helpText: String, foreground: Color, background: Color) -> some View {
        Image(systemName: systemImage)
            .font(.caption.weight(.medium))
            .foregroundColor(foreground)
            .frame(width: 18, height: 18)
            .padding(.horizontal, 5)
            .padding(.vertical, 2)
            .background(background, in: Capsule())
            .help(helpText)
            .accessibilityLabel(helpText)
    }

    private var storageRow: some View {
        let usedBytes = Int64(stats?.totalBytesReturned ?? 0)
        let capacity = capacityBytes
        let progress: Double? = capacity.map {
            min(1.0, Double(usedBytes) / Double($0))
        }
        let usedGB = Double(usedBytes) / 1_073_741_824
        let capGB  = capacity.map { Double($0) / 1_073_741_824 }
        // Live numbers only while connected — otherwise keep the same vertical
        // footprint with an empty bar + blank caption line.
        let showLive = connected && (progress != nil || stats != nil)

        return VStack(alignment: .leading, spacing: 2) {
            if showLive, let progress = progress, let capGB = capGB {
                ProgressView(value: progress)
                    .progressViewStyle(.linear)
                    .tint(progress > 0.85 ? .orange : .accentColor)
                // One tight line so three cards side-by-side still show
                // used/cap, % full, and file count without truncating.
                let line = formatStorage(
                    used: usedGB,
                    capacity: capGB,
                    truncated: stats?.truncated ?? false,
                    files: stats?.totalFiles
                )
                Text(line)
                    .font(.caption.monospacedDigit())
                    .foregroundColor(.secondary)
                    .lineLimit(1)
                    .minimumScaleFactor(0.85)
                    .allowsTightening(true)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .help(storageHelp(
                        used: usedGB,
                        capacity: capGB,
                        truncated: stats?.truncated ?? false,
                        files: stats?.totalFiles
                    ))
            } else if showLive, let stats = stats {
                Text("\(String(format: "%.1f", usedGB)) GB · \(stats.totalFiles) files")
                    .font(.caption.monospacedDigit())
                    .foregroundColor(.secondary)
                    .lineLimit(1)
            } else {
                // Placeholder: empty track + caption-height spacer so the card
                // matches a connected card's height without inventing numbers.
                ProgressView(value: 0)
                    .progressViewStyle(.linear)
                    .opacity(0.35)
                Text("\u{00A0}")
                    .font(.caption.monospacedDigit())
                    .accessibilityHidden(true)
            }
        }
    }

    /// Compact single-line caption, e.g. `6.3/32 GB · 20% · 375 files`.
    /// Percentage is how full the device is (used), not free — empty ≈ 0%.
    private func formatStorage(used: Double, capacity: Double, truncated: Bool, files: Int?) -> String {
        let usedFrac = capacity > 0 ? max(0, min(1, used / capacity)) : 0
        let pct = Int((usedFrac * 100).rounded())
        let usedPart = "\(String(format: "%.1f", used))\(truncated ? "+" : "")/\(String(format: "%.0f", capacity)) GB"
        let fullPart = truncated ? "≤\(pct)%" : "\(pct)%"
        if let files {
            return "\(usedPart) · \(fullPart) · \(files) file\(files == 1 ? "" : "s")"
        }
        return "\(usedPart) · \(fullPart)"
    }

    /// Full prose for the tooltip (hover) — not constrained by card width.
    private func storageHelp(used: Double, capacity: Double, truncated: Bool, files: Int?) -> String {
        let free = max(0, capacity - used)
        let usedFrac = capacity > 0 ? max(0, min(1, used / capacity)) : 0
        let pctFull = Int((usedFrac * 100).rounded())
        var parts = [
            "\(String(format: "%.1f", used))\(truncated ? "+" : "") / \(String(format: "%.0f", capacity)) GB used (\(pctFull)% full)",
            "\(String(format: "%.0f", free)) GB free",
        ]
        if let files {
            parts.append("\(files) file\(files == 1 ? "" : "s")")
        }
        if truncated { parts.append("used size may be under-counted") }
        return parts.joined(separator: " · ")
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
                if plaudSignedOut {
                    viewModel.onPairPlaud(plaudRegion)
                } else {
                    viewModel.onReconnectDevice(device.deviceId)
                }
            } label: {
                Image(systemName: plaudSignedOut
                        ? "person.crop.circle.badge.plus"
                        : (unreachable ? "arrow.clockwise.circle.fill" : "arrow.clockwise.circle"))
                    .font(.title3)
                    .foregroundColor(plaudSignedOut || unreachable ? .orange : .accentColor)
            }
            .buttonStyle(.plain)
            .help(plaudSignedOut ? "Sign in to Plaud again" : (unreachable ? "\(device.shortName) is unreachable — try reconnecting" : "Reconnect \(device.shortName)"))

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

/// Lays out the visible device cards in an adaptive grid so two cards fit
/// side-by-side at default window width and reflow to one column when the
/// window is narrow. Sits where the old status/storage/filter rows used to
/// be.
///
/// Visibility rules:
///   - HiDocks always render (paired devices are important signal even when
///     unreachable — that's what surfaces "plug me back in")
///   - Volumes only render when currently connected — unplugged SD cards /
///     external recorders shouldn't clutter the strip
///   - Imported files do NOT get a card. Imports live on the File menu and
///     as a table-level filter; a whole card for them was noise.
struct DeviceStripView: View {
    @ObservedObject var viewModel: HiDockViewModel

    /// HiDocks first, then connected volumes. Keeps the highest-signal
    /// cards in the first row when the grid reflows.
    private var visibleDevices: [HiDockPairedDevice] {
        viewModel.syncPairedDevices
            .filter { device in
                switch device.deviceType {
                case .hidock: return true
                case .volume: return viewModel.syncDeviceConnected[device.deviceId] == true
                case .plaud: return true
                }
            }
            .sorted { a, b in
                if a.deviceType != b.deviceType {
                    return a.deviceType == .hidock
                }
                return a.cleanName < b.cleanName
            }
    }

    // Narrower minimum so three cards fit on a row at typical widths (freeing
    // vertical space for the recordings list). Card content truncates to fit.
    private let columns = [GridItem(.adaptive(minimum: 260), spacing: 8)]

    var body: some View {
        LazyVGrid(columns: columns, alignment: .leading, spacing: 8) {
            ForEach(visibleDevices, id: \.deviceId) { device in
                DeviceCardView(viewModel: viewModel, device: device)
            }
        }
    }
}

/// Small blue icon-only chip with a rotating `arrow.triangle.2.circlepath`
/// shown while a HiDock/Plaud probe is in flight. Distinguishes an
/// in-progress connection check from a genuinely-not-connected device.
/// Fixed frame keeps the spin from affecting surrounding layout (which was
/// visibly bouncing the chip when the transcript detail pane was open).
private struct AnimatedConnectingChip: View {
    @State private var spinning = false

    var body: some View {
        Image(systemName: "arrow.triangle.2.circlepath")
            .font(.caption.weight(.medium))
            .foregroundColor(.blue)
            .rotationEffect(.degrees(spinning ? 360 : 0))
            .animation(
                .linear(duration: 1.2).repeatForever(autoreverses: false),
                value: spinning
            )
            // Frame *after* rotation so layout size stays fixed while the
            // glyph spins — avoids the chip "scrolling" neighbouring views.
            .frame(width: 18, height: 18)
            .padding(.horizontal, 5)
            .padding(.vertical, 2)
            .background(Color.blue.opacity(0.12), in: Capsule())
            .help("Connecting…")
            .accessibilityLabel("Connecting")
            .onAppear { spinning = true }
    }
}
