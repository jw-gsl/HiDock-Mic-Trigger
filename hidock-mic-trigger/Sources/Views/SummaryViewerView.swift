import SwiftUI
import AppKit

/// In-app viewer for a generated summary markdown file — mirrors the
/// transcript opening in a window inside the app rather than launching the
/// user's external markdown editor. Lightweight block renderer covering what
/// the summary templates produce (headings, bullets, inline bold/italic/links).
struct SummaryViewerView: View {
    let summaryPath: String

    @State private var lines: [String] = []
    @State private var loadError: String?

    private var fileName: String { (summaryPath as NSString).lastPathComponent }

    var body: some View {
        VStack(spacing: 0) {
            // Header strip — title + actions (the external-open affordance
            // stays available, just no longer the default click).
            HStack(spacing: 8) {
                Image(systemName: "doc.text.fill").foregroundColor(.indigo)
                Text(fileName)
                    .font(.headline)
                    .lineLimit(1)
                    .truncationMode(.middle)
                Spacer()
                Button {
                    NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: summaryPath)])
                } label: {
                    Label("Reveal", systemImage: "folder")
                }
                .help("Reveal the summary file in Finder")
                Button {
                    NSWorkspace.shared.open(URL(fileURLWithPath: summaryPath))
                } label: {
                    Label("Open in Editor", systemImage: "square.and.pencil")
                }
                .help("Open the summary in your default markdown editor")
            }
            .controlSize(.small)
            .padding(.horizontal, 14)
            .padding(.vertical, 10)
            .background(Color(NSColor.windowBackgroundColor))
            Divider()

            if let err = loadError {
                VStack(spacing: 8) {
                    Spacer()
                    Image(systemName: "exclamationmark.triangle").foregroundColor(.orange)
                    Text(err).font(.callout).foregroundColor(.secondary)
                    Spacer()
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ScrollView {
                    VStack(alignment: .leading, spacing: 6) {
                        ForEach(Array(lines.enumerated()), id: \.offset) { _, line in
                            row(for: line)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .textSelection(.enabled)
                    .padding(20)
                }
            }
        }
        .frame(minWidth: 460, minHeight: 360)
        .onAppear(perform: load)
    }

    @ViewBuilder
    private func row(for raw: String) -> some View {
        let line = raw
        if line.trimmingCharacters(in: .whitespaces).isEmpty {
            Spacer().frame(height: 6)
        } else if line.hasPrefix("### ") {
            Text(inline(String(line.dropFirst(4))))
                .font(.headline)
                .padding(.top, 4)
        } else if line.hasPrefix("## ") {
            Text(inline(String(line.dropFirst(3))))
                .font(.title3.weight(.semibold))
                .padding(.top, 6)
        } else if line.hasPrefix("# ") {
            Text(inline(String(line.dropFirst(2))))
                .font(.title2.weight(.bold))
                .padding(.top, 4)
        } else if let m = bulletBody(line) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text("•").foregroundColor(.secondary)
                Text(inline(m))
            }
        } else if line.hasPrefix("---") || line.hasPrefix("***") {
            Divider().padding(.vertical, 2)
        } else {
            Text(inline(line))
        }
    }

    /// Returns the bullet body for "- x" / "* x" (and 2-space-indented
    /// nested bullets), or nil if the line isn't a bullet.
    private func bulletBody(_ line: String) -> String? {
        let trimmed = line.drop(while: { $0 == " " })
        if trimmed.hasPrefix("- ") { return String(trimmed.dropFirst(2)) }
        if trimmed.hasPrefix("* ") { return String(trimmed.dropFirst(2)) }
        return nil
    }

    /// Inline markdown (bold/italic/code/links) via AttributedString, falling
    /// back to plain text if parsing fails.
    private func inline(_ s: String) -> AttributedString {
        (try? AttributedString(markdown: s,
                               options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)))
            ?? AttributedString(s)
    }

    private func load() {
        do {
            let content = try String(contentsOfFile: summaryPath, encoding: .utf8)
            lines = content.components(separatedBy: "\n")
        } catch {
            loadError = "Couldn't read the summary file:\n\(error.localizedDescription)"
        }
    }
}
