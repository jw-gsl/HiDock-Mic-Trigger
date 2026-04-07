import SwiftUI

struct FeedbackItem: Identifiable {
    let id: Int
    let title: String
    let body: String
    let url: String
    let number: Int
    let state: String
    let date: String

    var parsedDate: Date? {
        ISO8601DateFormatter().date(from: date)
    }

    var shortDate: String {
        if let d = parsedDate {
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

    /// Extract the category label from the title prefix (e.g. "Recording & downloads:")
    var category: String {
        if title.hasPrefix("Feature:") { return "Suggestion" }
        let parts = title.split(separator: ":", maxSplits: 1)
        if parts.count == 2 {
            return String(parts[0]).trimmingCharacters(in: .whitespaces)
        }
        return "General"
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

enum FeedbackFilter: String, CaseIterable {
    case all = "All"
    case open = "Open"
    case closed = "Closed"
}

enum FeedbackSort: String, CaseIterable {
    case newest = "Newest First"
    case oldest = "Oldest First"
    case issueNumber = "Issue Number"
}

struct FeedbackHistoryView: View {
    let items: [FeedbackItem]
    @State private var selectedID: Int?
    @State private var filter: FeedbackFilter = .all
    @State private var sort: FeedbackSort = .newest
    @State private var searchText: String = ""

    private var filteredItems: [FeedbackItem] {
        var result = items

        // Filter by state
        switch filter {
        case .all: break
        case .open: result = result.filter { $0.state != "closed" }
        case .closed: result = result.filter { $0.state == "closed" }
        }

        // Search
        if !searchText.isEmpty {
            let query = searchText.lowercased()
            result = result.filter {
                $0.title.lowercased().contains(query) ||
                $0.body.lowercased().contains(query) ||
                "#\($0.number)".contains(query)
            }
        }

        // Sort
        switch sort {
        case .newest:
            result.sort { ($0.parsedDate ?? .distantPast) > ($1.parsedDate ?? .distantPast) }
        case .oldest:
            result.sort { ($0.parsedDate ?? .distantPast) < ($1.parsedDate ?? .distantPast) }
        case .issueNumber:
            result.sort { $0.number > $1.number }
        }

        return result
    }

    private var openCount: Int { items.filter { $0.state != "closed" }.count }
    private var closedCount: Int { items.filter { $0.state == "closed" }.count }

    var body: some View {
        HSplitView {
            // Left: filter bar + list
            VStack(spacing: 0) {
                // Filter/sort controls
                VStack(spacing: 6) {
                    // Status filter pills
                    HStack(spacing: 4) {
                        ForEach(FeedbackFilter.allCases, id: \.self) { f in
                            Button {
                                filter = f
                            } label: {
                                HStack(spacing: 3) {
                                    Text(f.rawValue)
                                    if f == .open {
                                        Text("\(openCount)")
                                            .font(.caption2.weight(.semibold))
                                            .padding(.horizontal, 5)
                                            .padding(.vertical, 1)
                                            .background(Color.blue.opacity(0.2), in: Capsule())
                                    } else if f == .closed {
                                        Text("\(closedCount)")
                                            .font(.caption2.weight(.semibold))
                                            .padding(.horizontal, 5)
                                            .padding(.vertical, 1)
                                            .background(Color.green.opacity(0.2), in: Capsule())
                                    }
                                }
                                .font(.caption)
                            }
                            .buttonStyle(.bordered)
                            .tint(filter == f ? .accentColor : nil)
                            .controlSize(.small)
                        }
                        Spacer()
                        Picker("", selection: $sort) {
                            ForEach(FeedbackSort.allCases, id: \.self) { s in
                                Text(s.rawValue).tag(s)
                            }
                        }
                        .pickerStyle(.menu)
                        .frame(width: 130)
                        .controlSize(.small)
                    }

                    // Search
                    HStack {
                        Image(systemName: "magnifyingglass")
                            .foregroundColor(.secondary)
                            .font(.caption)
                        TextField("Search feedback...", text: $searchText)
                            .textFieldStyle(.plain)
                            .font(.caption)
                    }
                    .padding(.horizontal, 8)
                    .padding(.vertical, 4)
                    .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 6))
                }
                .padding(.horizontal, 10)
                .padding(.vertical, 8)

                Divider()

                // List
                if filteredItems.isEmpty {
                    VStack {
                        Spacer()
                        Text("No matching feedback")
                            .font(.caption)
                            .foregroundColor(.secondary)
                        Spacer()
                    }
                } else {
                    List(filteredItems, selection: $selectedID) { item in
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
                            HStack(spacing: 8) {
                                Text(item.shortDate)
                                    .font(.caption2)
                                    .foregroundColor(.secondary)
                                Text(item.category)
                                    .font(.caption2)
                                    .padding(.horizontal, 5)
                                    .padding(.vertical, 1)
                                    .background(Color.secondary.opacity(0.12), in: Capsule())
                                    .foregroundColor(.secondary)
                            }
                        }
                        .padding(.vertical, 4)
                        .tag(item.id)
                    }
                    .listStyle(.sidebar)
                }
            }
            .frame(minWidth: 240, idealWidth: 280)

            // Right: detail
            if let selected = filteredItems.first(where: { $0.id == selectedID }) {
                VStack(spacing: 0) {
                    // Top bar
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

                    ScrollView {
                        VStack(alignment: .leading, spacing: 12) {
                            Text(selected.title)
                                .font(.title3.weight(.semibold))

                            // Category badge
                            Text(selected.category)
                                .font(.caption)
                                .padding(.horizontal, 8)
                                .padding(.vertical, 3)
                                .background(Color.secondary.opacity(0.12), in: Capsule())

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
        .frame(minWidth: 600, minHeight: 380)
        .onAppear {
            if selectedID == nil, let first = filteredItems.first {
                selectedID = first.id
            }
        }
    }
}
