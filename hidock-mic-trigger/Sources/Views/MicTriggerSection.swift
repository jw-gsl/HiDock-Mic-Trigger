import SwiftUI

struct MicTriggerSection: View {
    @ObservedObject var viewModel: HiDockViewModel
    @State private var pulseAnimation = false

    private let timeFormatter: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "HH:mm:ss"
        return f
    }()

    /// Elapsed-since string, matching AppDelegate.formatUptime's format.
    static func uptimeString(since start: Date, now: Date) -> String {
        let elapsed = max(0, Int(now.timeIntervalSince(start)))
        if elapsed < 60 { return "\(elapsed)s" }
        if elapsed < 3600 { return "\(elapsed / 60)m \(elapsed % 60)s" }
        return "\(elapsed / 3600)h \((elapsed % 3600) / 60)m"
    }

    private var isDevBuild: Bool {
        #if DEV_BUILD
        return true
        #else
        return false
        #endif
    }

    /// Three-state dot — green only when the CLI has confirmed it's
    /// polling devices (was the source of the "shows Running but doesn't
    /// work" bug). Amber means the process is up but stuck in
    /// waitForDevice. Grey means stopped.
    private var dotColor: Color {
        if isDevBuild { return .orange }
        if !viewModel.triggerRunning { return .gray }
        return viewModel.triggerHealthy ? .green : .orange
    }

    private var statusText: String {
        if !viewModel.triggerRunning { return "Stopped" }
        if !viewModel.triggerHealthy {
            // Distinguish "process just spawned, waiting for first
            // output" (transient — Starting…) from "process is alive
            // but blocked on waitForDevice for HiDock or the mic"
            // (potentially long-lived — Waiting). The wait message is
            // shown inline next to this label so the user can see
            // exactly what it's waiting for.
            return viewModel.triggerWaitMessage != nil ? "Waiting" : "Starting…"
        }
        return "Active"
    }

    private var statusColor: Color {
        if !viewModel.triggerRunning { return .secondary }
        if !viewModel.triggerHealthy { return .orange }
        return .green
    }

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 12) {
                // Status indicator
                HStack(spacing: 6) {
                    Circle()
                        .fill(dotColor)
                        .frame(width: 10, height: 10)
                        .shadow(color: viewModel.triggerHealthy ? Color.green.opacity(pulseAnimation ? 0.6 : 0.0) : .clear, radius: pulseAnimation ? 6 : 0)
                        .animation(.easeInOut(duration: 1.2).repeatForever(autoreverses: true), value: pulseAnimation)
                        .onChange(of: viewModel.triggerHealthy) { healthy in
                            pulseAnimation = healthy
                        }
                        .onAppear {
                            pulseAnimation = viewModel.triggerHealthy
                        }
                    Text("Mic Trigger")
                        .font(.headline)
                    if isDevBuild {
                        Text("(disabled in dev)")
                            .font(.caption)
                            .foregroundColor(.orange)
                    }
                }

                Text(statusText)
                    .font(.subheadline)
                    .foregroundColor(statusColor)
                if viewModel.triggerRunning, let pid = viewModel.triggerPID {
                    Text("pid \(pid)")
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
                // Connected time — shown only when actually connected
                // (healthy), not while waiting for a device.
                if viewModel.triggerRunning, viewModel.triggerHealthy,
                   let since = viewModel.triggerConnectedSince {
                    // Self-ticking: TimelineView updates only this label each
                    // second — it never writes shared view-model state, so the
                    // per-second tick can't re-render the rest of the window.
                    TimelineView(.periodic(from: since, by: 1)) { ctx in
                        Text(Self.uptimeString(since: since, now: ctx.date))
                            .font(.caption.monospacedDigit())
                            .foregroundColor(.secondary)
                    }
                }
                // Last-(re)start timestamp — passive proof that an
                // unplug/replug actually bounced the trigger. Useful
                // when notifications get coalesced or missed by
                // macOS. Format: "↻ 16:23:18".
                if let started = viewModel.triggerLastStartedAt {
                    Text("↻ \(timeFormatter.string(from: started))")
                        .font(.caption.monospacedDigit())
                        .foregroundColor(.secondary)
                        .help("Trigger last (re)started at this time")
                }

                // Live "Recording" badge — second-source confirmation
                // that the trigger is doing its job. Lights up when the
                // CLI's USB-mic watcher flips IN USE and ffmpeg is
                // holding the HiDock interface open. If you see this
                // pulse during a call, the trigger is unambiguously
                // working end-to-end.
                if viewModel.hidockRecordingActive {
                    HStack(spacing: 4) {
                        Circle()
                            .fill(Color.red)
                            .frame(width: 8, height: 8)
                            .shadow(color: Color.red.opacity(pulseAnimation ? 0.7 : 0.0), radius: pulseAnimation ? 5 : 0)
                        Text("Recording")
                            .font(.caption.weight(.semibold))
                            .foregroundColor(.red)
                        if let dev = viewModel.hidockRecordingDeviceName {
                            Text("· \(dev)")
                                .font(.caption)
                                .foregroundColor(.secondary)
                        }
                    }
                    .padding(.horizontal, 6)
                    .padding(.vertical, 2)
                    .background(Color.red.opacity(0.10), in: Capsule())
                } else if viewModel.triggerRunning, !viewModel.triggerHealthy,
                          let waiting = viewModel.triggerWaitMessage {
                    Text(waiting)
                        .font(.caption)
                        .foregroundColor(.orange)
                        .lineLimit(1)
                        .truncationMode(.tail)
                        .help(waiting)
                }

                Spacer()

                // Controls
                HStack(spacing: 8) {
                    Button {
                        viewModel.onStartTrigger()
                    } label: {
                        Label("Start", systemImage: "play.fill")
                    }
                    .disabled(viewModel.triggerRunning || isDevBuild)
                    .buttonStyle(.borderedProminent)
                    .tint(.green)
                    .opacity(viewModel.triggerRunning || isDevBuild ? 0.6 : 1.0)

                    Button {
                        viewModel.onStopTrigger()
                    } label: {
                        Label("Stop", systemImage: "stop.fill")
                    }
                    .disabled(!viewModel.triggerRunning || isDevBuild)
                    .buttonStyle(.bordered)

                    Divider()
                        .frame(height: 20)

                    Picker("Mic:", selection: Binding(
                        get: { viewModel.selectedMicName ?? "" },
                        set: { viewModel.onSelectMic($0) }
                    )) {
                        ForEach(viewModel.availableMics, id: \.self) { mic in
                            Text(mic).tag(mic)
                        }
                    }
                    .frame(maxWidth: 240)

                    Toggle("Auto-start", isOn: Binding(
                        get: { viewModel.autoStartOnLaunch },
                        set: { _ in viewModel.onToggleAutoStart() }
                    ))
                    .toggleStyle(.checkbox)
                    .font(.caption)
                }
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 14)
            .background(Color(nsColor: .controlBackgroundColor).opacity(0.5))
            .overlay(alignment: .bottom) {
                Rectangle()
                    .fill(Color(nsColor: .separatorColor).opacity(0.4))
                    .frame(height: 1)
                    .shadow(color: .black.opacity(0.08), radius: 2, y: 1)
            }
        }
    }
}
