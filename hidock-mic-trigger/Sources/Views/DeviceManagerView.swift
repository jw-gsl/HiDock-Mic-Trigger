import SwiftUI

struct DeviceManagerView: View {
    @ObservedObject var viewModel: HiDockViewModel
    @State private var sortOrder: DeviceSortKey = .name
    @State private var filterType: String = "all" // "all", "hidock", "volume"
    @State private var searchText = ""

    var body: some View {
        VStack(spacing: 0) {
            // Header
            HStack {
                Text("Device Manager")
                    .font(.title2)
                    .fontWeight(.semibold)
                Spacer()
                Button {
                    viewModel.onPairDock()
                } label: {
                    Label("Pair HiDock", systemImage: "link.badge.plus")
                }
                .buttonStyle(.bordered)
                .controlSize(.small)
            }
            .padding(.horizontal, 20)
            .padding(.top, 16)
            .padding(.bottom, 12)

            Divider()

            // Toolbar: search + filter + sort
            HStack(spacing: 8) {
                Image(systemName: "magnifyingglass")
                    .foregroundColor(.secondary)
                TextField("Search devices...", text: $searchText)
                    .textFieldStyle(.roundedBorder)
                    .frame(maxWidth: 200)

                Divider().frame(height: 16)

                Text("Type:").font(.caption.weight(.medium))
                Picker("", selection: $filterType) {
                    Text("All").tag("all")
                    Text("HiDock").tag("hidock")
                    Text("Volume").tag("volume")
                }
                .pickerStyle(.segmented)
                .frame(width: 180)

                Divider().frame(height: 16)

                Text("Sort:").font(.caption.weight(.medium))
                Picker("", selection: $sortOrder) {
                    Text("Name").tag(DeviceSortKey.name)
                    Text("Type").tag(DeviceSortKey.type)
                    Text("Paired").tag(DeviceSortKey.pairedAt)
                }
                .pickerStyle(.segmented)
                .frame(width: 180)

                Spacer()
            }
            .padding(.horizontal, 20)
            .padding(.vertical, 8)

            Divider()

            // Device list
            if filteredDevices.isEmpty {
                VStack(spacing: 12) {
                    Spacer()
                    Image(systemName: "externaldrive.badge.questionmark")
                        .font(.system(size: 40))
                        .foregroundColor(.secondary)
                    Text("No devices paired")
                        .font(.headline)
                        .foregroundColor(.secondary)
                    Text("Use \"Pair\" in the toolbar to connect a HiDock,\nor pair a USB volume below.")
                        .font(.caption)
                        .foregroundColor(.secondary)
                        .multilineTextAlignment(.center)
                    Spacer()
                }
            } else {
                ScrollView {
                    LazyVStack(spacing: 0) {
                        ForEach(filteredDevices, id: \.deviceId) { device in
                            DeviceRowView(
                                device: device,
                                isConnected: viewModel.syncDeviceConnected[device.deviceId] ?? false,
                                onForget: { viewModel.onForgetDevice(device) }
                            )
                            Divider()
                                .padding(.horizontal, 16)
                        }
                    }
                    .padding(.vertical, 8)
                }
            }

            Divider()

            // Footer: Pair Volume
            HStack {
                Text("\(viewModel.syncPairedDevices.count) device\(viewModel.syncPairedDevices.count == 1 ? "" : "s") paired")
                    .font(.caption)
                    .foregroundColor(.secondary)
                Spacer()
                PairVolumeButton(viewModel: viewModel)
            }
            .padding(.horizontal, 20)
            .padding(.vertical, 10)
        }
        .frame(minWidth: 560, minHeight: 400)
    }

    private var filteredDevices: [HiDockPairedDevice] {
        var devices = viewModel.syncPairedDevices

        if filterType == "hidock" {
            devices = devices.filter { $0.deviceType == .hidock }
        } else if filterType == "volume" {
            devices = devices.filter { $0.deviceType == .volume }
        }

        if !searchText.isEmpty {
            let query = searchText.lowercased()
            devices = devices.filter {
                $0.cleanName.lowercased().contains(query) ||
                ($0.volumeName?.lowercased().contains(query) ?? false) ||
                $0.deviceId.lowercased().contains(query)
            }
        }

        devices.sort { a, b in
            switch sortOrder {
            case .name:
                return a.cleanName.localizedCaseInsensitiveCompare(b.cleanName) == .orderedAscending
            case .type:
                if a.deviceType != b.deviceType {
                    return a.deviceType.rawValue < b.deviceType.rawValue
                }
                return a.cleanName.localizedCaseInsensitiveCompare(b.cleanName) == .orderedAscending
            case .pairedAt:
                return (a.pairedAt ?? "") > (b.pairedAt ?? "")
            }
        }

        return devices
    }
}

private enum DeviceSortKey: Hashable {
    case name, type, pairedAt
}

// MARK: - Device Row

struct DeviceRowView: View {
    let device: HiDockPairedDevice
    let isConnected: Bool
    let onForget: () -> Void

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            // Icon
            Image(systemName: deviceIcon)
                .font(.title2)
                .foregroundColor(isConnected ? .green : .secondary)
                .frame(width: 28)
                .padding(.top, 2)

            // Text content
            VStack(alignment: .leading, spacing: 4) {
                HStack {
                    Text(device.cleanName)
                        .font(.headline)
                    if isConnected {
                        Text("Connected")
                            .font(.caption2)
                            .foregroundColor(.green)
                            .padding(.horizontal, 6)
                            .padding(.vertical, 2)
                            .background(Color.green.opacity(0.1))
                            .cornerRadius(4)
                    }
                    Spacer()
                    Text(device.deviceType == .hidock ? "HiDock" : "Volume")
                        .font(.caption)
                        .foregroundColor(.secondary)
                        .padding(.horizontal, 6)
                        .padding(.vertical, 2)
                        .background(Color.secondary.opacity(0.1))
                        .cornerRadius(4)
                }

                HStack(spacing: 12) {
                    if device.deviceType == .hidock {
                        Label("Product ID: \(device.productId)", systemImage: "number")
                            .font(.caption)
                            .foregroundColor(.secondary)
                    }
                    if let vol = device.volumeName {
                        Label(vol, systemImage: "externaldrive")
                            .font(.caption)
                            .foregroundColor(.secondary)
                    }
                    if let sub = device.subpath {
                        Label(sub, systemImage: "folder")
                            .font(.caption)
                            .foregroundColor(.secondary)
                    }
                    if let ts = device.pairedAt {
                        Label(formatPairedDate(ts), systemImage: "clock")
                            .font(.caption)
                            .foregroundColor(.secondary)
                    }
                }
            }

            // Actions
            VStack(spacing: 4) {
                Button(role: .destructive) {
                    onForget()
                } label: {
                    Text("Forget")
                        .font(.caption)
                }
                .buttonStyle(.bordered)
                .controlSize(.small)
            }
            .frame(width: 70)
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 12)
    }

    private var deviceIcon: String {
        return hidockDeviceIcon(device.shortName, deviceType: device.deviceType)
    }

    private static let isoFormatter = ISO8601DateFormatter()
    private static let relativeFormatter: RelativeDateTimeFormatter = {
        let f = RelativeDateTimeFormatter()
        f.unitsStyle = .abbreviated
        return f
    }()

    private func formatPairedDate(_ iso: String) -> String {
        guard let date = Self.isoFormatter.date(from: iso) else { return iso }
        return Self.relativeFormatter.localizedString(for: date, relativeTo: Date())
    }
}

// MARK: - Pair Volume Button

struct PairVolumeButton: View {
    @ObservedObject var viewModel: HiDockViewModel
    @State private var showPopover = false
    @State private var volumeName = ""
    @State private var subpath = ""
    @State private var scannedVolumes: [VolumeScanResult] = []
    @State private var scanning = false

    var body: some View {
        Button {
            showPopover.toggle()
            if showPopover {
                scanForVolumes()
            }
        } label: {
            Label("Pair Volume", systemImage: "externaldrive.badge.plus")
        }
        .buttonStyle(.bordered)
        .controlSize(.small)
        .popover(isPresented: $showPopover) {
            VStack(alignment: .leading, spacing: 12) {
                Text("Pair USB Volume")
                    .font(.headline)

                // Discovered volumes
                if scanning {
                    HStack(spacing: 6) {
                        ProgressView()
                            .controlSize(.small)
                        Text("Scanning volumes...")
                            .font(.caption)
                            .foregroundColor(.secondary)
                    }
                } else if !scannedVolumes.isEmpty {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("Discovered volumes:")
                            .font(.caption)
                            .foregroundColor(.secondary)
                        ForEach(scannedVolumes, id: \.volumeName) { vol in
                            Button {
                                volumeName = vol.volumeName
                            } label: {
                                HStack {
                                    Image(systemName: "externaldrive")
                                        .foregroundColor(.accentColor)
                                    VStack(alignment: .leading) {
                                        Text(vol.volumeName)
                                            .font(.callout.weight(.medium))
                                        Text("\(vol.audioFileCount) audio file\(vol.audioFileCount == 1 ? "" : "s")")
                                            .font(.caption)
                                            .foregroundColor(.secondary)
                                    }
                                    Spacer()
                                    if volumeName == vol.volumeName {
                                        Image(systemName: "checkmark")
                                            .foregroundColor(.accentColor)
                                    }
                                }
                                .contentShape(Rectangle())
                            }
                            .buttonStyle(.plain)
                            .padding(.vertical, 2)
                        }
                    }
                    .frame(maxWidth: 280)

                    Divider()
                }

                if scannedVolumes.isEmpty && !scanning {
                    Button("Scan for Volumes") {
                        scanForVolumes()
                    }
                    .font(.caption)
                }

                TextField("Volume name (e.g. ZOOM_H1)", text: $volumeName)
                    .textFieldStyle(.roundedBorder)
                    .frame(width: 280)

                TextField("Subfolder (optional)", text: $subpath)
                    .textFieldStyle(.roundedBorder)
                    .frame(width: 280)

                HStack {
                    Button("Cancel") {
                        showPopover = false
                    }
                    .keyboardShortcut(.cancelAction)

                    Button("Pair") {
                        let sub = subpath.isEmpty ? nil : subpath
                        viewModel.onPairVolume(volumeName, sub)
                        volumeName = ""
                        subpath = ""
                        showPopover = false
                    }
                    .keyboardShortcut(.defaultAction)
                    .disabled(volumeName.trimmingCharacters(in: .whitespaces).isEmpty)
                }
            }
            .padding(16)
        }
    }

    private func scanForVolumes() {
        scanning = true
        scannedVolumes = []
        viewModel.onScanVolumes { results in
            DispatchQueue.main.async {
                self.scannedVolumes = results
                self.scanning = false
            }
        }
    }
}
