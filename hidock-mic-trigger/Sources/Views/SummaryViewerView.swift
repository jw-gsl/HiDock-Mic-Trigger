import SwiftUI
import AppKit

/// In-app viewer for a generated summary markdown file. Shows a classification
/// header (how the transcript was classified + a one-line reason) above the
/// rendered markdown, with a Reclassify dropdown that re-runs the AI summary
/// against a different template. Mirrors the transcript opening in-app rather
/// than launching the external editor.
struct SummaryViewerView: View {
    let summaryPath: String
    /// Clean template names for the Reclassify dropdown.
    var templates: [String] = []
    /// (transcriptPath, templateName) -> re-run the summary with that template.
    var onReclassify: (String, String) -> Void = { _, _ in }

    @State private var meta: [String: String] = [:]
    @State private var bodyLines: [String] = []
    @State private var loadError: String?

    private var fileName: String { (summaryPath as NSString).lastPathComponent }
    private var classifiedType: String { meta["type"] ?? "" }
    private var transcriptPath: String { meta["transcript"] ?? "" }

    var body: some View {
        VStack(spacing: 0) {
            // Title strip + file actions.
            HStack(spacing: 8) {
                Image(systemName: "doc.text.fill").foregroundColor(.indigo)
                Text(meta["title"] ?? fileName)
                    .font(.headline).lineLimit(1).truncationMode(.middle)
                Spacer()
                Button { NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: summaryPath)]) } label: {
                    Label("Reveal", systemImage: "folder")
                }.help("Reveal the summary file in Finder")
                Button { NSWorkspace.shared.open(URL(fileURLWithPath: summaryPath)) } label: {
                    Label("Open in Editor", systemImage: "square.and.pencil")
                }.help("Open in your default markdown editor")
            }
            .controlSize(.small)
            .padding(.horizontal, 14).padding(.vertical, 10)
            .background(Color(NSColor.windowBackgroundColor))
            Divider()

            // Classification header card — how the transcript was classified,
            // a one-line reason, and the Reclassify control.
            if !classifiedType.isEmpty {
                classificationHeader
                Divider()
            }

            if let err = loadError {
                VStack(spacing: 8) {
                    Spacer()
                    Image(systemName: "exclamationmark.triangle").foregroundColor(.orange)
                    Text(err).font(.callout).foregroundColor(.secondary)
                    Spacer()
                }.frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                ScrollView {
                    VStack(alignment: .leading, spacing: 6) {
                        ForEach(Array(bodyLines.enumerated()), id: \.offset) { _, line in
                            row(for: line)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .textSelection(.enabled)
                    .padding(20)
                }
            }
        }
        .frame(minWidth: 360, minHeight: 300)   // hosted in a resizable pane now
        .onAppear(perform: load)
    }

    private var classificationHeader: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                Image(systemName: "tag.fill").foregroundColor(.indigo)
                Text("Classified as:").foregroundColor(.secondary)
                Text(classifiedType).fontWeight(.semibold)
                Spacer()
                reclassifyMenu
            }
            if let reason = meta["classified"], !reason.isEmpty {
                Text(reason)
                    .font(.callout)
                    .foregroundColor(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            HStack(spacing: 14) {
                if let area = meta["area"], !area.isEmpty {
                    Label(area, systemImage: "square.grid.2x2")
                }
                if let rec = meta["recorded"], !rec.isEmpty {
                    Label(rec, systemImage: "calendar")
                }
            }
            .font(.caption)
            .foregroundColor(.secondary)
        }
        .padding(.horizontal, 16).padding(.vertical, 10)
        .background(Color.indigo.opacity(0.06))
    }

    @ViewBuilder
    private var reclassifyMenu: some View {
        if templates.isEmpty || transcriptPath.isEmpty {
            EmptyView()
        } else {
            Menu {
                ForEach(templates, id: \.self) { name in
                    Button {
                        onReclassify(transcriptPath, name)
                    } label: {
                        if name == classifiedType {
                            Label(name, systemImage: "checkmark")
                        } else {
                            Text(name)
                        }
                    }
                }
            } label: {
                Label("Reclassify", systemImage: "arrow.triangle.2.circlepath")
            }
            .menuStyle(.borderlessButton)
            .fixedSize()
            .help("Re-run the AI summary using a different template")
        }
    }

    // MARK: - Markdown rendering

    @ViewBuilder
    private func row(for raw: String) -> some View {
        let line = raw
        if line.trimmingCharacters(in: .whitespaces).isEmpty {
            Spacer().frame(height: 6)
        } else if line.hasPrefix("### ") {
            Text(inline(String(line.dropFirst(4)))).font(.headline).padding(.top, 4)
        } else if line.hasPrefix("## ") {
            Text(inline(String(line.dropFirst(3)))).font(.title3.weight(.semibold)).padding(.top, 6)
        } else if line.hasPrefix("# ") {
            Text(inline(String(line.dropFirst(2)))).font(.title2.weight(.bold)).padding(.top, 4)
        } else if let m = bulletBody(line) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                Text("•").foregroundColor(.secondary)
                Text(inline(m))
            }
        } else if line.hasPrefix("---") || line.hasPrefix("***") {
            Divider().padding(.vertical, 2)
        } else if line.hasPrefix("> ") {
            Text(inline(String(line.dropFirst(2))))
                .foregroundColor(.secondary)
                .padding(.leading, 8)
        } else {
            Text(inline(line))
        }
    }

    private func bulletBody(_ line: String) -> String? {
        let trimmed = line.drop(while: { $0 == " " })
        if trimmed.hasPrefix("- ") { return String(trimmed.dropFirst(2)) }
        if trimmed.hasPrefix("* ") { return String(trimmed.dropFirst(2)) }
        return nil
    }

    private func inline(_ s: String) -> AttributedString {
        (try? AttributedString(markdown: s,
                               options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)))
            ?? AttributedString(s)
    }

    // MARK: - Load + frontmatter

    private func load() {
        do {
            let content = try String(contentsOfFile: summaryPath, encoding: .utf8)
            let (parsedMeta, body) = Self.splitFrontmatter(content)
            meta = parsedMeta
            bodyLines = body.components(separatedBy: "\n")
        } catch {
            loadError = "Couldn't read the summary file:\n\(error.localizedDescription)"
        }
    }

    /// Split a leading `--- … ---` one-line-per-key block from the body.
    /// Returns ([:], wholeText) when there's no frontmatter.
    static func splitFrontmatter(_ text: String) -> ([String: String], String) {
        let lines = text.components(separatedBy: "\n")
        guard lines.first?.trimmingCharacters(in: .whitespaces) == "---" else {
            return ([:], text)
        }
        var meta: [String: String] = [:]
        var idx = 1
        while idx < lines.count {
            let line = lines[idx]
            if line.trimmingCharacters(in: .whitespaces) == "---" {
                idx += 1
                break
            }
            if let colon = line.range(of: ": ") {
                let key = String(line[line.startIndex..<colon.lowerBound]).trimmingCharacters(in: .whitespaces)
                let value = String(line[colon.upperBound...])
                meta[key] = value
            }
            idx += 1
        }
        let body = lines[idx...].joined(separator: "\n").trimmingCharacters(in: .whitespacesAndNewlines)
        return (meta, body)
    }
}
