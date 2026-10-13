import SwiftUI

/// What the reviewer's decisions say about speaker and meeting matching, and
/// what the app has tuned because of them. Shown in the Voice Library, where
/// the voices it is about are managed; collapsed until opened. Refreshes
/// itself (and re-tunes) when shown.
struct MatchingQualityView: View {
    @ObservedObject var viewModel: HiDockViewModel
    @State private var expanded = false

    var body: some View {
        DisclosureGroup(isExpanded: $expanded) {
            VStack(alignment: .leading, spacing: 6) {
                Text("Learned from what you apply, skip and confirm. Refine and meeting matching adjust themselves from this, within safe limits.")
                    .font(.caption).foregroundColor(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                if viewModel.reviewQualityLines.isEmpty {
                    Text(viewModel.reviewQualityBusy ? "Working it out…" : "Not measured yet.")
                        .font(.caption).foregroundColor(.secondary)
                } else {
                    ForEach(Array(viewModel.reviewQualityLines.enumerated()), id: \.offset) { _, line in
                        Text(line.trimmingCharacters(in: .whitespaces))
                            .font(.caption)
                            .padding(.leading, line.hasPrefix("  ") ? 14 : 0)
                            .foregroundColor(line.hasPrefix("  ") ? .secondary : .primary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                HStack {
                    if let updated = viewModel.reviewQualityUpdated {
                        Text("Updated \(updated.formatted(date: .omitted, time: .shortened))")
                            .font(.caption2).foregroundColor(.secondary)
                    }
                    Spacer()
                    if viewModel.reviewQualityBusy {
                        ProgressView().controlSize(.small)
                    } else {
                        Button("Refresh") { viewModel.onRefreshReviewQuality() }
                            .controlSize(.small)
                    }
                }
            }
            .padding(.top, 4)
        } label: {
            HStack(spacing: 6) {
                Image(systemName: "chart.line.uptrend.xyaxis").foregroundColor(.teal)
                Text("Matching quality").font(.subheadline.weight(.medium))
                if !expanded, let first = viewModel.reviewQualityLines.first {
                    Text(first).font(.caption).foregroundColor(.secondary).lineLimit(1)
                }
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 8)
        .onAppear { viewModel.onRefreshReviewQuality() }
    }
}
