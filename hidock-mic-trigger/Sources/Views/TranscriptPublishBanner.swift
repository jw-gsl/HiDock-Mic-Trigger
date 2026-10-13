import SwiftUI

/// Shown while settled transcripts can't be pushed to the GitHub repo.
/// Publishing is best-effort and silent by design, so without this a lapsed
/// GitHub login or a diverged remote would go unnoticed until someone looked
/// for a transcript that was never published.
struct TranscriptPublishBanner: View {
    @ObservedObject var viewModel: HiDockViewModel
    let problem: String

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Image(systemName: "icloud.slash")
                .foregroundColor(.orange)
            VStack(alignment: .leading, spacing: 2) {
                Text("Transcripts aren't reaching GitHub")
                    .font(.callout.weight(.semibold))
                Text("\(problem). They're kept and retried every 15 minutes. If it's a sign-in problem, run `gh auth login` in the terminal.")
                    .font(.caption)
                    .foregroundColor(.secondary)
                    .textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 8)
            Button("Retry now") { viewModel.onRetryTranscriptPublish() }
                .controlSize(.small)
            Button("Status…") { viewModel.onShowTranscriptPublishStatus() }
                .controlSize(.small)
            Button {
                viewModel.onDismissTranscriptPublishProblem()
            } label: {
                Image(systemName: "xmark")
            }
            .buttonStyle(.borderless)
            .help("Hide until the next failed publish")
        }
        .padding(10)
        .background(RoundedRectangle(cornerRadius: 8).fill(Color.orange.opacity(0.12)))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.orange.opacity(0.45), lineWidth: 1))
    }
}
