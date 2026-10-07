import SwiftUI

/// Persistent warning shown while calendar lookups can't reach the calendar.
///
/// Before this existed a lapsed connector was logged as "no match" and the
/// only symptom was meetings quietly missing their calendar entry. The banner
/// says what failed, what Claude actually replied, and how to fix it, and
/// stays up until a lookup gets through or the user dismisses it.
struct CalendarConnectorBanner: View {
    @ObservedObject var viewModel: HiDockViewModel
    let problem: HiDockViewModel.CalendarConnectorProblem
    @State private var showDetail = false

    private var affectedText: String {
        let count = problem.affectedPaths.count
        return count == 1 ? "1 recording has no meeting linked" : "\(count) recordings have no meeting linked"
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Image(systemName: "calendar.badge.exclamationmark")
                    .foregroundColor(.orange)
                VStack(alignment: .leading, spacing: 2) {
                    Text(problem.summary)
                        .font(.callout.weight(.semibold))
                    Text("\(affectedText) since \(problem.since.formatted(date: .abbreviated, time: .shortened)). \(problem.fix)")
                        .font(.caption)
                        .foregroundColor(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 8)
                Button("Fix in Terminal") { viewModel.onFixCalendarConnector() }
                    .buttonStyle(.borderedProminent)
                    .tint(.orange)
                    .controlSize(.small)
                Button {
                    viewModel.onRetryCalendarConnector()
                } label: {
                    if viewModel.calendarConnectorRetrying {
                        HStack(spacing: 4) {
                            ProgressView().controlSize(.mini)
                            Text("Checking…")
                        }
                    } else {
                        Text("Check again")
                    }
                }
                .controlSize(.small)
                .disabled(viewModel.calendarConnectorRetrying)
                Button {
                    viewModel.onDismissCalendarConnectorProblem()
                } label: {
                    Image(systemName: "xmark")
                }
                .buttonStyle(.borderless)
                .help("Hide until the next failed lookup")
            }
            if !problem.detail.isEmpty {
                DisclosureGroup(isExpanded: $showDetail) {
                    Text(problem.detail)
                        .font(.caption.monospaced())
                        .foregroundColor(.secondary)
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                } label: {
                    Text("What Claude said").font(.caption)
                }
            }
        }
        .padding(10)
        .background(
            RoundedRectangle(cornerRadius: 8)
                .fill(Color.orange.opacity(0.12))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 8)
                .stroke(Color.orange.opacity(0.45), lineWidth: 1)
        )
    }
}
