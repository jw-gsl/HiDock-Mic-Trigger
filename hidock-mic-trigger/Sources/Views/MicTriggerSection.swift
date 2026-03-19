import SwiftUI

struct MicTriggerSection: View {
    @ObservedObject var viewModel: HiDockViewModel

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 12) {
                // Status indicator
                HStack(spacing: 6) {
                    Circle()
                        .fill(viewModel.triggerRunning ? Color.green : Color.gray)
                        .frame(width: 8, height: 8)
                    Text("Mic Trigger")
                        .font(.headline)
                }

                if viewModel.triggerRunning {
                    Text("Running")
                        .font(.subheadline)
                        .foregroundColor(.green)
                    if let pid = viewModel.triggerPID {
                        Text("pid \(pid)")
                            .font(.caption)
                            .foregroundColor(.secondary)
                    }
                    if !viewModel.triggerUptime.isEmpty {
                        Text(viewModel.triggerUptime)
                            .font(.caption.monospacedDigit())
                            .foregroundColor(.secondary)
                    }
                } else {
                    Text("Stopped")
                        .font(.subheadline)
                        .foregroundColor(.secondary)
                }

                Spacer()

                // Controls
                HStack(spacing: 8) {
                    Button {
                        viewModel.onStartTrigger()
                    } label: {
                        Label("Start", systemImage: "play.fill")
                    }
                    .disabled(viewModel.triggerRunning)
                    .buttonStyle(.bordered)
                    .tint(.green)

                    Button {
                        viewModel.onStopTrigger()
                    } label: {
                        Label("Stop", systemImage: "stop.fill")
                    }
                    .disabled(!viewModel.triggerRunning)
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
            .padding(.vertical, 10)
            .background(Color(nsColor: .controlBackgroundColor).opacity(0.5))
        }
    }
}
