import SwiftUI

struct FeedbackItem: Identifiable {
    let id: Int
    let title: String
    let body: String
    let url: String
    let number: Int
    let state: String
    let date: String

    var shortDate: String {
        if let d = ISO8601DateFormatter().date(from: date) {
            let fmt = DateFormatter()
            fmt.dateStyle = .medium
            fmt.timeStyle = .short
            return fmt.string(from: d)
        }
        return date
    }

    var stateIcon: String {
        state == "closed" ? "checkmark.circle.fill" : "circle.fill"
    }

    var stateColor: Color {
        state == "closed" ? .green : .blue
    }

    var cleanBody: String {
        body
            .replacingOccurrences(of: "<details>", with: "")
            .replacingOccurrences(of: "</details>", with: "")
            .replacingOccurrences(of: "<summary>System Information</summary>", with: "System Information:")
            .replacingOccurrences(of: "## ", with: "")
            .replacingOccurrences(of: "- **", with: "  ")
            .replacingOccurrences(of: "**", with: "")
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }
}

struct FeedbackHistoryView: View {
    let items: [FeedbackItem]
    @State private var selectedID: Int?

    var body: some View {
        HSplitView {
            // Left: scrollable list
            List(items, selection: $selectedID) { item in
                VStack(alignment: .leading, spacing: 4) {
                    HStack(spacing: 6) {
                        Image(systemName: item.stateIcon)
                            .foregroundColor(item.stateColor)
                            .font(.caption)
                        Text("#\(item.number)")
                            .font(.caption.monospacedDigit())
                            .foregroundColor(.secondary)
                        Text(item.title)
                            .font(.system(size: 12, weight: .medium))
                            .lineLimit(2)
                    }
                    Text(item.shortDate)
                        .font(.caption2)
                        .foregroundColor(.secondary)
                }
                .padding(.vertical, 4)
                .tag(item.id)
            }
            .listStyle(.sidebar)
            .frame(minWidth: 220, idealWidth: 260)

            // Right: detail
            if let selected = items.first(where: { $0.id == selectedID }) {
                VStack(spacing: 0) {
                    // Top bar with title and GitHub button
                    HStack(spacing: 8) {
                        Image(systemName: selected.stateIcon)
                            .foregroundColor(selected.stateColor)
                        Text("#\(selected.number)")
                            .font(.headline.monospacedDigit())
                        Text(selected.state == "closed" ? "Closed" : "Open")
                            .font(.caption)
                            .padding(.horizontal, 8)
                            .padding(.vertical, 2)
                            .background(selected.stateColor.opacity(0.15), in: Capsule())
                        Text(selected.shortDate)
                            .font(.caption)
                            .foregroundColor(.secondary)
                        Spacer()
                        if !selected.url.isEmpty {
                            Button {
                                if let url = URL(string: selected.url) {
                                    NSWorkspace.shared.open(url)
                                }
                            } label: {
                                Label("View on GitHub", systemImage: "arrow.up.right.square")
                            }
                            .buttonStyle(.bordered)
                            .controlSize(.small)
                        }
                    }
                    .padding(.horizontal, 16)
                    .padding(.vertical, 10)
                    .background(Color(nsColor: .controlBackgroundColor).opacity(0.5))

                    Divider()

                    // Scrollable body
                    ScrollView {
                        VStack(alignment: .leading, spacing: 12) {
                            Text(selected.title)
                                .font(.title3.weight(.semibold))

                            Text(selected.cleanBody)
                                .font(.system(size: 12))
                                .textSelection(.enabled)
                        }
                        .padding(16)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
                .frame(minWidth: 300)
            } else {
                VStack {
                    Spacer()
                    Image(systemName: "text.bubble")
                        .font(.system(size: 32))
                        .foregroundColor(.secondary.opacity(0.4))
                    Text("Select an issue to see details")
                        .font(.subheadline)
                        .foregroundColor(.secondary)
                    Spacer()
                }
                .frame(minWidth: 300)
            }
        }
        .frame(minWidth: 580, minHeight: 350)
        .onAppear {
            if selectedID == nil, let first = items.first {
                selectedID = first.id
            }
        }
    }
}
