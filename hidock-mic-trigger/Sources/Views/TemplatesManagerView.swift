import SwiftUI
import AppKit
import UniformTypeIdentifiers

/// One summary template file (a .md in ~/HiDock/Summary Templates/).
struct TemplateItem: Identifiable, Hashable {
    let id: String        // absolute path
    let url: URL
    let displayName: String
}

/// Lean templates manager (slice 5): list the user's summary templates with
/// Import, New/Iterate via Claude Code (reuses the CLI pane — no API keys),
/// Reveal in Finder, Open in editor, and Delete. No in-app markdown editor —
/// editing happens via Claude Code or the user's default editor.
struct TemplatesManagerView: View {
    @ObservedObject var viewModel: HiDockViewModel
    @State private var templates: [TemplateItem] = []
    @State private var showDeleteConfirm = false
    @State private var pendingDelete: TemplateItem?

    private var dirURL: URL {
        URL(fileURLWithPath: "\(NSHomeDirectory())/HiDock/Summary Templates")
    }

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 6) {
                Image(systemName: "doc.text")
                Text("Summary Templates").font(.headline)
                Spacer()
                Text("\(templates.count)")
                    .font(.caption).foregroundColor(.secondary)
            }
            .padding(.horizontal, 14).padding(.vertical, 10)
            Divider()

            if templates.isEmpty {
                VStack(spacing: 8) {
                    Spacer()
                    Image(systemName: "tray").font(.largeTitle).foregroundColor(.secondary)
                    Text("No templates yet")
                        .font(.callout)
                    Text("Import a .md template, or create one with Claude Code.")
                        .font(.caption).foregroundColor(.secondary)
                    Spacer()
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                List {
                    ForEach(templates) { t in
                        HStack(spacing: 8) {
                            Image(systemName: "doc.text.fill").foregroundColor(.indigo)
                            Text(t.displayName).lineLimit(1)
                            Spacer()
                            Button {
                                viewModel.onIterateTemplate(t.url)
                            } label: {
                                Label("Iterate", systemImage: "sparkles")
                            }
                            .buttonStyle(.borderless)
                            .help("Open Claude Code in the CLI pane to refine this template")
                            Menu {
                                Button {
                                    NSWorkspace.shared.activateFileViewerSelecting([t.url])
                                } label: {
                                    Label("Reveal in Finder", systemImage: "folder")
                                }
                                Button {
                                    NSWorkspace.shared.open(t.url)
                                } label: {
                                    Label("Open in Editor", systemImage: "square.and.pencil")
                                }
                                Divider()
                                Button(role: .destructive) {
                                    pendingDelete = t
                                    showDeleteConfirm = true
                                } label: {
                                    Label("Delete", systemImage: "trash")
                                }
                            } label: {
                                Image(systemName: "ellipsis.circle")
                            }
                            .menuStyle(.borderlessButton)
                            .fixedSize()
                        }
                        .padding(.vertical, 2)
                    }
                }
                .listStyle(.inset)
            }

            Divider()
            HStack(spacing: 8) {
                Button { importTemplates() } label: {
                    Label("Import…", systemImage: "square.and.arrow.down")
                }
                Button { viewModel.onCreateTemplate() } label: {
                    Label("New via Claude Code", systemImage: "sparkles")
                }
                Spacer()
                Button {
                    try? FileManager.default.createDirectory(at: dirURL, withIntermediateDirectories: true)
                    NSWorkspace.shared.activateFileViewerSelecting([dirURL])
                } label: {
                    Label("Open Folder", systemImage: "folder")
                }
            }
            .controlSize(.small)
            .padding(.horizontal, 14).padding(.vertical, 10)
        }
        .frame(minWidth: 460, minHeight: 360)
        .onAppear(perform: reload)
        .confirmationDialog("Delete this template?",
                            isPresented: $showDeleteConfirm,
                            titleVisibility: .visible) {
            Button("Delete", role: .destructive) {
                if let t = pendingDelete {
                    try? FileManager.default.removeItem(at: t.url)
                    reload()
                }
            }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text(pendingDelete?.displayName ?? "")
        }
    }

    private func reload() {
        let fm = FileManager.default
        try? fm.createDirectory(at: dirURL, withIntermediateDirectories: true)
        let urls = (try? fm.contentsOfDirectory(at: dirURL, includingPropertiesForKeys: nil)) ?? []
        templates = urls
            .filter { $0.pathExtension.lowercased() == "md" }
            .sorted { $0.lastPathComponent.localizedCaseInsensitiveCompare($1.lastPathComponent) == .orderedAscending }
            .map { TemplateItem(id: $0.path, url: $0,
                                displayName: cleanName($0.deletingPathExtension().lastPathComponent)) }
    }

    /// '👥 Job Interview' -> 'Job Interview' — mirrors typed_summarize._clean_name
    /// (drop a leading emoji/symbol prefix).
    private func cleanName(_ stem: String) -> String {
        let trimmed = stem.drop(while: { !$0.isLetter && !$0.isNumber })
        let result = trimmed.trimmingCharacters(in: .whitespaces)
        return result.isEmpty ? stem : result
    }

    private func importTemplates() {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [UTType(filenameExtension: "md") ?? .plainText]
        panel.allowsMultipleSelection = true
        panel.canChooseDirectories = false
        panel.message = "Choose markdown template(s) to import into Summary Templates"
        guard panel.runModal() == .OK else { return }
        let fm = FileManager.default
        try? fm.createDirectory(at: dirURL, withIntermediateDirectories: true)
        for src in panel.urls {
            var dest = dirURL.appendingPathComponent(src.lastPathComponent)
            // Avoid clobbering an existing template with the same name.
            if fm.fileExists(atPath: dest.path) {
                let base = src.deletingPathExtension().lastPathComponent
                dest = dirURL.appendingPathComponent("\(base) (imported).md")
            }
            try? fm.copyItem(at: src, to: dest)
        }
        reload()
    }
}
