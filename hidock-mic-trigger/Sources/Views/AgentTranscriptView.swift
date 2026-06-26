import SwiftUI
import MarkdownUI

/// One tool invocation surfaced in the transcript (claude only).
struct AgentToolActivity: Identifiable, Equatable {
    let id: String
    var name: String
    var inputSummary: String?
    var status: Status
    var preview: String?
    enum Status: Equatable { case running, ok, failed }
}

/// An ordered transcript block — either a run of assistant markdown or a tool
/// invocation. Modelling them in one ordered list lets text and tool activity
/// interleave the way they actually happen, like a tidy version of the CLI.
struct AgentBlock: Identifiable, Equatable {
    let id: UUID
    var kind: Kind
    enum Kind: Equatable {
        case markdown(String)
        case tool(AgentToolActivity)
        case user(String)
    }
}

/// Accumulates normalized `AgentEvent`s into a renderable transcript. Mutate
/// only on the main thread (it drives `@Published` UI state).
final class AgentTranscript: ObservableObject {
    @Published var blocks: [AgentBlock] = []
    @Published var stages: [String] = []
    @Published var inputTokens: Int?
    @Published var outputTokens: Int?
    @Published var costUSD: Double?
    @Published var model: String?
    @Published var engine: String?
    @Published var errorMessage: String?
    @Published var running = false
    @Published var finished = false
    @Published var summaryPath: String?

    func reset() {
        blocks = []; stages = []
        inputTokens = nil; outputTokens = nil; costUSD = nil
        model = nil; engine = nil; errorMessage = nil
        running = true; finished = false; summaryPath = nil
    }

    /// Parse and ingest a raw stderr line (no-op if it isn't an event line).
    @discardableResult
    func ingest(line: String) -> Bool {
        guard let ev = AgentEvent.parse(line: line) else { return false }
        ingest(ev)
        return true
    }

    /// Append a distinct user-turn bubble (Ask AI). Subsequent assistant text
    /// starts a fresh block because the last block is no longer `.markdown`.
    func addUserMessage(_ text: String) {
        blocks.append(AgentBlock(id: UUID(), kind: .user(text)))
    }

    func ingest(_ event: AgentEvent) {
        switch event {
        case .stage(let label):
            if !label.isEmpty { stages.append(label) }
        case .text(let delta):
            appendText(delta)
        case .tool(let id, let name, let inputSummary):
            blocks.append(AgentBlock(id: UUID(), kind: .tool(
                AgentToolActivity(id: id, name: name, inputSummary: inputSummary,
                                  status: .running, preview: nil))))
        case .toolResult(let id, let ok, let preview):
            updateTool(id: id, ok: ok, preview: preview)
        case .usage(let i, let o, let c):
            inputTokens = i ?? inputTokens
            outputTokens = o ?? outputTokens
            costUSD = c ?? costUSD
        case .meta(let engine, _, let model):
            self.engine = engine ?? self.engine
            self.model = model ?? self.model
        case .error(let message):
            errorMessage = message
            running = false
        case .done(_, let path):
            finished = true
            running = false
            if let path = path { summaryPath = path }
        }
    }

    private func appendText(_ delta: String) {
        if let last = blocks.indices.last,
           case .markdown(let existing) = blocks[last].kind {
            blocks[last].kind = .markdown(existing + delta)
        } else {
            blocks.append(AgentBlock(id: UUID(), kind: .markdown(delta)))
        }
    }

    private func updateTool(id: String, ok: Bool, preview: String?) {
        for i in blocks.indices {
            if case .tool(var act) = blocks[i].kind, act.id == id {
                act.status = ok ? .ok : .failed
                act.preview = preview
                blocks[i].kind = .tool(act)
                return
            }
        }
    }
}

/// Read-only formatted rendering of an `AgentTranscript`: markdown assistant
/// text, tool-activity chips, a stage checklist header, a streaming indicator,
/// and a usage/cost footer. Used by the summary readout and embedded in the
/// chat view.
struct AgentTranscriptView: View {
    @ObservedObject var transcript: AgentTranscript
    /// When false, the stage checklist header is hidden (chat doesn't need it).
    var showStages: Bool = true

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                VStack(alignment: .leading, spacing: 10) {
                    if showStages && !transcript.stages.isEmpty {
                        stagesHeader
                    }
                    ForEach(transcript.blocks) { block in
                        switch block.kind {
                        case .markdown(let text):
                            Markdown(text)
                                .markdownTextStyle { FontSize(12) }
                                .markdownTextStyle(\.code) { FontFamilyVariant(.monospaced); FontSize(11) }
                                .textSelection(.enabled)
                                // Accept the proposed (bounded) width and grow
                                // only vertically, so long lines wrap instead of
                                // overflowing the narrow pane.
                                .fixedSize(horizontal: false, vertical: true)
                                .frame(maxWidth: .infinity, alignment: .leading)
                        case .tool(let activity):
                            ToolActivityChip(activity: activity)
                        case .user(let text):
                            UserMessageBubble(text: text)
                        }
                    }
                    if transcript.running { StreamingIndicator() }
                    if let err = transcript.errorMessage { errorRow(err) }
                    if transcript.finished || transcript.costUSD != nil { footer }
                    Color.clear.frame(height: 1).id("agent-bottom")
                }
                .padding(12)
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .onChange(of: transcript.blocks) { _ in
                withAnimation(.easeOut(duration: 0.15)) { proxy.scrollTo("agent-bottom", anchor: .bottom) }
            }
        }
    }

    private var stagesHeader: some View {
        VStack(alignment: .leading, spacing: 3) {
            ForEach(Array(transcript.stages.enumerated()), id: \.offset) { idx, stage in
                let isLast = idx == transcript.stages.count - 1
                HStack(spacing: 6) {
                    Image(systemName: (isLast && transcript.running) ? "circle.dotted" : "checkmark.circle.fill")
                        .foregroundColor((isLast && transcript.running) ? .secondary : .green)
                        .font(.caption)
                    Text(stage)
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
            }
        }
        .padding(.bottom, 4)
    }

    private func errorRow(_ message: String) -> some View {
        HStack(alignment: .top, spacing: 6) {
            Image(systemName: "exclamationmark.triangle.fill").foregroundColor(.orange)
            Text(message).font(.callout).foregroundColor(.primary)
        }
        .padding(8)
        .background(Color.orange.opacity(0.12))
        .cornerRadius(6)
    }

    private var footer: some View {
        HStack(spacing: 12) {
            if let model = transcript.model {
                Label(shortModel(model), systemImage: "cpu").font(.caption2)
            } else if let engine = transcript.engine {
                Label(engine, systemImage: "cpu").font(.caption2)
            }
            if let inT = transcript.inputTokens, let outT = transcript.outputTokens {
                Text("\(inT) in / \(outT) out tok").font(.caption2)
            }
            if let cost = transcript.costUSD, cost > 0 {
                Text(String(format: "$%.4f", cost)).font(.caption2)
            }
            Spacer()
        }
        .foregroundColor(.secondary)
        .padding(.top, 4)
    }

    private func shortModel(_ m: String) -> String {
        // "claude-opus-4-8" → "opus-4-8"
        m.replacingOccurrences(of: "claude-", with: "")
    }
}

/// Compact, expandable chip for a single tool invocation.
private struct ToolActivityChip: View {
    let activity: AgentToolActivity
    @State private var expanded = false

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Button {
                if activity.preview != nil { expanded.toggle() }
            } label: {
                HStack(spacing: 6) {
                    Image(systemName: icon).font(.caption).frame(width: 16)
                    Text(activity.name).font(.caption.weight(.medium))
                    if let summary = activity.inputSummary {
                        Text(summary)
                            .font(.caption)
                            .foregroundColor(.secondary)
                            .lineLimit(1)
                            .truncationMode(.middle)
                    }
                    Spacer(minLength: 4)
                    statusIcon
                    if activity.preview != nil {
                        Image(systemName: expanded ? "chevron.down" : "chevron.right")
                            .font(.caption2).foregroundColor(.secondary)
                    }
                }
            }
            .buttonStyle(.plain)
            if expanded, let preview = activity.preview {
                Text(preview)
                    .font(.system(.caption, design: .monospaced))
                    .foregroundColor(.secondary)
                    .textSelection(.enabled)
                    .padding(8)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(Color(NSColor.textBackgroundColor))
                    .cornerRadius(4)
            }
        }
        .padding(.vertical, 5)
        .padding(.horizontal, 8)
        .background(Color(NSColor.controlBackgroundColor))
        .cornerRadius(6)
        .overlay(RoundedRectangle(cornerRadius: 6).stroke(Color.secondary.opacity(0.15)))
    }

    private var icon: String {
        switch activity.name {
        case "Read", "NotebookRead": return "doc.text"
        case "Write", "Edit", "NotebookEdit": return "pencil"
        case "Bash", "BashOutput", "KillShell": return "terminal"
        case "Grep", "Glob": return "magnifyingglass"
        case "WebFetch", "WebSearch": return "globe"
        case "Task", "Agent": return "person.2"
        case "TodoWrite": return "checklist"
        default: return "wrench.and.screwdriver"
        }
    }

    @ViewBuilder private var statusIcon: some View {
        switch activity.status {
        case .running: ProgressView().controlSize(.small).scaleEffect(0.7)
        case .ok: Image(systemName: "checkmark.circle.fill").font(.caption2).foregroundColor(.green)
        case .failed: Image(systemName: "xmark.circle.fill").font(.caption2).foregroundColor(.red)
        }
    }
}

/// A user turn in the Ask-AI conversation, styled as a trailing bubble.
private struct UserMessageBubble: View {
    let text: String
    var body: some View {
        HStack {
            Spacer(minLength: 40)
            Text(text)
                .font(.callout)
                .textSelection(.enabled)
                .padding(.horizontal, 10)
                .padding(.vertical, 7)
                .background(Color.accentColor.opacity(0.15))
                .cornerRadius(10)
                .frame(alignment: .trailing)
        }
    }
}

/// A small "assistant is working" affordance shown while events still stream.
private struct StreamingIndicator: View {
    @State private var phase = 0.0
    private let timer = Timer.publish(every: 0.4, on: .main, in: .common).autoconnect()
    var body: some View {
        HStack(spacing: 4) {
            ForEach(0..<3) { i in
                Circle()
                    .frame(width: 5, height: 5)
                    .opacity(Int(phase) % 3 == i ? 1.0 : 0.3)
            }
        }
        .foregroundColor(.secondary)
        .onReceive(timer) { _ in phase += 1 }
        .padding(.vertical, 2)
    }
}
