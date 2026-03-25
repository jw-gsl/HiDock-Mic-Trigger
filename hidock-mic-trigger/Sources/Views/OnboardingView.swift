import SwiftUI
import Combine

struct OnboardingView: View {
    @ObservedObject var viewModel: HiDockViewModel
    @State private var currentStep: Int = 0
    @State private var hidockConnected = false
    @State private var autoAdvanceScheduled = false
    @State private var stepStatus: [Int: StepStatus] = [:]  // track each step

    enum StepStatus {
        case completed, skipped
    }

    private let totalSteps = 5

    var body: some View {
        VStack(spacing: 0) {
            // Step content
            TabView(selection: $currentStep) {
                welcomeStep.tag(0)
                connectStep.tag(1)
                micStep.tag(2)
                modelStep.tag(3)
                allSetStep.tag(4)
            }
            .tabViewStyle(.automatic)
            .frame(minHeight: 360)

            // Step indicator dots with status
            HStack(spacing: 10) {
                ForEach(0..<totalSteps, id: \.self) { i in
                    if i == currentStep {
                        Circle()
                            .fill(Color.accentColor)
                            .frame(width: 10, height: 10)
                    } else if stepStatus[i] == .completed {
                        Image(systemName: "checkmark.circle.fill")
                            .foregroundColor(.green)
                            .font(.system(size: 12))
                    } else if stepStatus[i] == .skipped {
                        Image(systemName: "forward.circle.fill")
                            .foregroundColor(.orange)
                            .font(.system(size: 12))
                    } else {
                        Circle()
                            .fill(Color.secondary.opacity(0.3))
                            .frame(width: 8, height: 8)
                    }
                }
            }
            .padding(.bottom, 16)

            // Navigation bar
            HStack {
                if currentStep > 0 {
                    Button("Back") {
                        withAnimation { currentStep -= 1 }
                    }
                    .buttonStyle(.bordered)
                }

                Spacer()

                if currentStep > 0 && currentStep < totalSteps - 1 {
                    Button("Skip") {
                        stepStatus[currentStep] = .skipped
                        withAnimation { currentStep += 1 }
                    }
                    .buttonStyle(.plain)
                    .foregroundColor(.secondary)
                }

                if currentStep == 0 {
                    Button("Get Started") {
                        stepStatus[0] = .completed
                        withAnimation { currentStep = 1 }
                    }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.large)
                } else if currentStep < totalSteps - 1 {
                    Button("Next") {
                        stepStatus[currentStep] = .completed
                        withAnimation { currentStep += 1 }
                    }
                    .buttonStyle(.borderedProminent)
                } else {
                    Button("Start Using HiDock") {
                        viewModel.onCompleteOnboarding()
                    }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.large)
                }
            }
            .padding(.horizontal, 32)
            .padding(.bottom, 24)
        }
        .frame(width: 540, height: 480)
        .onAppear {
            // Skip completed steps on appear
            if viewModel.syncDeviceConnected.values.contains(true) {
                hidockConnected = true
            }
        }
    }

    // MARK: - Step 1: Welcome

    private var welcomeStep: some View {
        VStack(spacing: 16) {
            Spacer()
            Image(systemName: "waveform")
                .font(.system(size: 56))
                .foregroundColor(.accentColor)

            Text("Welcome to HiDock")
                .font(.title)
                .fontWeight(.bold)

            Text("HiDock Tools helps you get the most from your HiDock device. It can sync your recordings, transcribe them with AI, and monitor your microphone so recordings start and stop automatically.")
                .font(.body)
                .foregroundColor(.secondary)
                .multilineTextAlignment(.center)
                .frame(maxWidth: 400)
            Spacer()
        }
        .padding(.horizontal, 32)
    }

    // MARK: - Step 2: Connect HiDock

    private var connectStep: some View {
        VStack(spacing: 16) {
            Spacer()
            Image(systemName: hidockConnected ? "checkmark.circle.fill" : "externaldrive.connected.to.line.below")
                .font(.system(size: 56))
                .foregroundColor(hidockConnected ? .green : .accentColor)

            Text("Connect your HiDock")
                .font(.title)
                .fontWeight(.bold)

            if hidockConnected {
                Text("Your HiDock is connected and ready to go!")
                    .font(.body)
                    .foregroundColor(.green)
                    .multilineTextAlignment(.center)
                    .frame(maxWidth: 400)
            } else {
                Text("Plug your HiDock into this computer using a USB cable. We'll detect it automatically.")
                    .font(.body)
                    .foregroundColor(.secondary)
                    .multilineTextAlignment(.center)
                    .frame(maxWidth: 400)

                ProgressView()
                    .controlSize(.small)
                Text("Waiting for HiDock...")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
            Spacer()
        }
        .padding(.horizontal, 32)
        .onReceive(Timer.publish(every: 2, on: .main, in: .common).autoconnect()) { _ in
            guard currentStep == 1 else { return }
            if viewModel.syncDeviceConnected.values.contains(true) && !hidockConnected {
                hidockConnected = true
                stepStatus[1] = .completed
                // Auto-advance after 1 second
                if !autoAdvanceScheduled {
                    autoAdvanceScheduled = true
                    DispatchQueue.main.asyncAfter(deadline: .now() + 1) {
                        if currentStep == 1 {
                            withAnimation { currentStep = 2 }
                        }
                    }
                }
            }
        }
    }

    // MARK: - Step 3: Select Microphone

    private var micStep: some View {
        VStack(spacing: 16) {
            Spacer()
            Image(systemName: "mic")
                .font(.system(size: 56))
                .foregroundColor(.accentColor)

            Text("Choose your microphone")
                .font(.title)
                .fontWeight(.bold)

            Text("Select the microphone you use for meetings and calls. HiDock Tools watches this mic to know when you're recording, so it can start and stop automatically.")
                .font(.body)
                .foregroundColor(.secondary)
                .multilineTextAlignment(.center)
                .frame(maxWidth: 400)

            if viewModel.availableMics.isEmpty {
                Text("No microphones found. You can set this up later.")
                    .font(.caption)
                    .foregroundColor(.secondary)
            } else {
                Picker("Microphone", selection: Binding(
                    get: { viewModel.selectedMicName ?? viewModel.availableMics.first ?? "" },
                    set: { viewModel.onSelectMic($0) }
                )) {
                    ForEach(viewModel.availableMics, id: \.self) { mic in
                        Text(mic).tag(mic)
                    }
                }
                .frame(maxWidth: 300)
            }
            Spacer()
        }
        .padding(.horizontal, 32)
    }

    // MARK: - Step 4: Download Model

    private var modelStep: some View {
        VStack(spacing: 16) {
            Spacer()
            Image(systemName: viewModel.modelReady ? "checkmark.circle.fill" : "arrow.down.circle")
                .font(.system(size: 56))
                .foregroundColor(viewModel.modelReady ? .green : .accentColor)

            Text("Speech Recognition")
                .font(.title)
                .fontWeight(.bold)

            if viewModel.modelReady {
                Text("The speech recognition model is downloaded and ready!")
                    .font(.body)
                    .foregroundColor(.green)
                    .multilineTextAlignment(.center)
                    .frame(maxWidth: 400)
            } else if viewModel.modelDownloading {
                Text("Downloading the speech recognition model...")
                    .font(.body)
                    .foregroundColor(.secondary)
                    .multilineTextAlignment(.center)
                    .frame(maxWidth: 400)

                ProgressView(value: viewModel.modelDownloadProgress)
                    .frame(maxWidth: 300)

                Text(viewModel.modelDownloadStatus.isEmpty
                    ? "\(Int(viewModel.modelDownloadProgress * 100))%"
                    : viewModel.modelDownloadStatus)
                    .font(.caption.monospacedDigit())
                    .foregroundColor(.secondary)

                Button("Cancel") {
                    viewModel.onCancelModelDownload()
                }
                .buttonStyle(.plain)
                .foregroundColor(.red)
                .font(.caption)
            } else {
                Text("To transcribe your recordings, HiDock Tools needs to download a speech recognition model. This is about 550 MB and only needs to happen once. You can skip this and do it later.")
                    .font(.body)
                    .foregroundColor(.secondary)
                    .multilineTextAlignment(.center)
                    .frame(maxWidth: 400)

                Button("Download Now") {
                    viewModel.onDownloadModel()
                }
                .buttonStyle(.bordered)
            }
            Spacer()
        }
        .padding(.horizontal, 32)
    }

    // MARK: - Step 5: All Set

    private var allSetStep: some View {
        VStack(spacing: 16) {
            Spacer()
            Image(systemName: "checkmark.circle")
                .font(.system(size: 56))
                .foregroundColor(.green)

            Text("You're all set!")
                .font(.title)
                .fontWeight(.bold)

            Text("Here's what's ready:")
                .font(.body)
                .foregroundColor(.secondary)

            VStack(alignment: .leading, spacing: 10) {
                configRow(
                    done: hidockConnected,
                    text: hidockConnected ? "HiDock connected" : "HiDock not connected (you can connect later)"
                )
                configRow(
                    done: viewModel.selectedMicName != nil,
                    text: viewModel.selectedMicName != nil
                        ? "Microphone: \(viewModel.selectedMicName!)"
                        : "No microphone selected (you can choose later)"
                )
                configRow(
                    done: viewModel.modelReady,
                    text: viewModel.modelReady
                        ? "Speech recognition ready"
                        : "Speech model not downloaded (you can download later)"
                )
            }
            .frame(maxWidth: 400, alignment: .leading)

            Spacer()
        }
        .padding(.horizontal, 32)
    }

    private func configRow(done: Bool, text: String) -> some View {
        HStack(spacing: 8) {
            Image(systemName: done ? "checkmark.circle.fill" : "circle")
                .foregroundColor(done ? .green : .secondary)
            Text(text)
                .font(.body)
                .foregroundColor(done ? .primary : .secondary)
        }
    }
}
