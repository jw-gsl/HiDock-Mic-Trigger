import SwiftUI

/// The file list's GitHub publish indicator, shown next to the folder icon
/// while auto-publish is on. Shows the transcript's state and how many
/// commits it has; clicking opens it on GitHub (right-click for its history).
struct PublishStatusIcon: View {
    @ObservedObject var viewModel: HiDockViewModel
    /// Recording stem — the transcript is `<stem>.md`.
    let stem: String

    private struct Look {
        let symbol: String
        let color: Color
        let help: String
    }

    private var status: TranscriptPublish.FileStatus? { viewModel.publishFileStatus[stem] }
    private var commits: Int { status?.commits ?? 0 }
    private var isSettling: Bool { viewModel.publishSettlingStems.contains(stem) }

    private var look: Look? {
        let failing = viewModel.transcriptPublishProblem != nil
        let history = commits == 1 ? "1 commit" : "\(commits) commits"
        if isSettling {
            if failing {
                return Look(symbol: "exclamationmark.icloud", color: .red,
                            help: "Waiting to publish, but GitHub publishing is failing — see the banner")
            }
            return Look(symbol: "clock.arrow.circlepath", color: .orange,
                        help: "Changed — commits once it has been quiet for 10 minutes and speaker matching has finished (\(history) so far)")
        }
        switch status?.state {
        case "synced":
            return Look(symbol: "checkmark.icloud", color: .green,
                        help: "On GitHub, up to date — \(history). Click to open; right-click for history")
        case "unpushed":
            return Look(symbol: "icloud.and.arrow.up", color: failing ? .red : .orange,
                        help: "Committed but not yet pushed to GitHub — \(history)")
        case "changed", "new":
            return Look(symbol: "icloud.and.arrow.up", color: .orange,
                        help: "Changed since it was last published — queued")
        case "unpublished":
            return Look(symbol: "icloud", color: .secondary,
                        help: "Not on GitHub — from before auto-publish was turned on. Edit it, or use Sync to GitHub Now, to publish it")
        case "excluded":
            return Look(symbol: "icloud.slash", color: .secondary,
                        help: "Not published — part of a merged recording; the merged transcript is published instead")
        default:
            return nil
        }
    }

    var body: some View {
        if viewModel.transcriptPublishEnabled, let look {
            Button {
                viewModel.onOpenPublishedTranscript(stem, false)
            } label: {
                HStack(spacing: 1) {
                    Image(systemName: look.symbol)
                    if commits > 0 {
                        Text("\(commits)")
                            .font(.caption2.monospacedDigit())
                    }
                }
                .foregroundColor(look.color)
            }
            .buttonStyle(.plain)
            .disabled(commits == 0)
            .help(look.help)
            .contextMenu {
                Button("Open on GitHub") { viewModel.onOpenPublishedTranscript(stem, false) }
                    .disabled(commits == 0)
                Button("Show history on GitHub") { viewModel.onOpenPublishedTranscript(stem, true) }
                    .disabled(commits == 0)
            }
        }
    }
}
