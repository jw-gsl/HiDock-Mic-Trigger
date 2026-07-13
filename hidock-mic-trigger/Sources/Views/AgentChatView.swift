import SwiftUI

/// Read-only formatted readout for a summarise / reclassify run. Header + the
/// transcript view (with the stage checklist). No input box.
struct SummaryReadoutPane: View {
    @ObservedObject var transcript: AgentTranscript
    var onOpenRawTerminal: () -> Void
    var onClose: () -> Void

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 6) {
                Image(systemName: "text.badge.checkmark")
                Text(transcript.running ? "Summarising…" : "Summary")
                    .font(.headline)
                Spacer()
                Button { onOpenRawTerminal() } label: {
                    Image(systemName: "terminal")
                }
                .buttonStyle(.plain)
                .help("Open the raw terminal (for sign-in / power use)")
                Button { onClose() } label: {
                    Image(systemName: "xmark.circle.fill").foregroundColor(.secondary)
                }
                .buttonStyle(.plain)
                .help("Hide the CLI pane")
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 6)
            .background(Color(NSColor.windowBackgroundColor))
            Divider()
            AgentTranscriptView(transcript: transcript, showStages: true)
        }
    }
}

/// Conversational Ask-AI pane: a header, the formatted transcript, and an input
/// box for multi-turn follow-ups. The transcript and run-state live on the view
/// model; submitting calls `onSend`, which AppDelegate turns into the next
/// engine turn (claude `--resume` for multi-turn).
struct AgentChatView: View {
    @ObservedObject var viewModel: HiDockViewModel
    var onClose: () -> Void

    @State private var draft: String = ""
    @FocusState private var inputFocused: Bool

    var body: some View {
        VStack(spacing: 0) {
            header
            Divider()
            AgentTranscriptView(transcript: viewModel.chatTranscript, showStages: false)
            Divider()
            inputBar
        }
        .onAppear { inputFocused = true }
    }

    private var header: some View {
        HStack(spacing: 6) {
            Image(systemName: "sparkles")
            Text(viewModel.chatTitle)
                .font(.headline)
                .lineLimit(1)
                .truncationMode(.middle)
            Spacer()
            Button {
                viewModel.onOpenRawTerminal()
            } label: {
                Image(systemName: "terminal")
            }
            .buttonStyle(.plain)
            .help("Open the raw terminal (for sign-in / power use)")
            Button {
                onClose()
            } label: {
                Image(systemName: "xmark.circle.fill").foregroundColor(.secondary)
            }
            .buttonStyle(.plain)
            .help("Hide the CLI pane")
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 6)
        .background(Color(NSColor.windowBackgroundColor))
    }

    private var inputBar: some View {
        HStack(alignment: .bottom, spacing: 6) {
            TextField("Ask a follow-up…", text: $draft, axis: .vertical)
                .textFieldStyle(.plain)
                .lineLimit(1...6)
                .focused($inputFocused)
                .onSubmit(send)
                .padding(8)
                .background(Color(NSColor.textBackgroundColor))
                .cornerRadius(8)
                .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.secondary.opacity(0.2)))
            Button(action: send) {
                Image(systemName: "arrow.up.circle.fill").font(.title2)
            }
            .buttonStyle(.plain)
            .disabled(!canSend)
            .foregroundColor(canSend ? .accentColor : .secondary)
            .help("Send")
        }
        .padding(8)
        .background(Color(NSColor.windowBackgroundColor))
    }

    private var canSend: Bool {
        !viewModel.chatRunning && !draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    private func send() {
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty, !viewModel.chatRunning else { return }
        draft = ""
        viewModel.onSendChat(text)
    }
}
