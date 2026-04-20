import SwiftUI
import AppKit
import SwiftTerm

/// Embedded terminal panel — runs a login shell in a PTY so the user can
/// authenticate CLIs (e.g. `claude auth login`) without leaving the app.
///
/// The terminal inherits the user's PATH from their login shell so tools
/// installed via brew/npm/nvm are discoverable.
struct EmbeddedTerminalView: View {
    @State private var title: String = "Terminal"
    let initialCommand: String?

    init(initialCommand: String? = nil) {
        self.initialCommand = initialCommand
    }

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Image(systemName: "terminal")
                Text(title)
                    .font(.headline)
                Spacer()
                Text("⌘W to close")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            .background(Color(NSColor.windowBackgroundColor))
            Divider()
            TerminalRepresentable(initialCommand: initialCommand, title: $title)
                .frame(minWidth: 720, minHeight: 420)
        }
    }
}

/// NSViewRepresentable wrapper around SwiftTerm's LocalProcessTerminalView.
private struct TerminalRepresentable: NSViewRepresentable {
    let initialCommand: String?
    @Binding var title: String

    func makeCoordinator() -> Coordinator {
        Coordinator(title: $title)
    }

    func makeNSView(context: Context) -> LocalProcessTerminalView {
        let tv = LocalProcessTerminalView(frame: .zero)
        tv.processDelegate = context.coordinator
        tv.translatesAutoresizingMaskIntoConstraints = false

        // Discover the user's login shell. Falls back to zsh (macOS default).
        let shell = ProcessInfo.processInfo.environment["SHELL"] ?? "/bin/zsh"

        // -l for login shell so ~/.zprofile, /etc/paths etc. are sourced.
        // -i forces interactive so aliases, PATH additions from .zshrc load too.
        var args: [String] = ["-l", "-i"]

        // If the caller supplied an initial command, run it after the shell
        // starts interactively. We echo the command so the user sees what
        // ran, then drop into an interactive shell so the user can continue.
        if let cmd = initialCommand, !cmd.isEmpty {
            let escaped = cmd.replacingOccurrences(of: "'", with: "'\"'\"'")
            let script = "echo '$ \(escaped)'; \(cmd); exec \(shell) -i"
            args = ["-l", "-c", script]
        }

        // Inherit user's environment — essential for PATH, HOME, NVM, etc.
        var env: [String] = []
        for (k, v) in ProcessInfo.processInfo.environment {
            env.append("\(k)=\(v)")
        }
        if !env.contains(where: { $0.hasPrefix("TERM=") }) {
            env.append("TERM=xterm-256color")
        }

        tv.startProcess(executable: shell, args: args, environment: env)
        return tv
    }

    func updateNSView(_ nsView: LocalProcessTerminalView, context: Context) {}

    final class Coordinator: NSObject, LocalProcessTerminalViewDelegate {
        @Binding var title: String

        init(title: Binding<String>) {
            self._title = title
        }

        func sizeChanged(source: LocalProcessTerminalView, newCols: Int, newRows: Int) {}

        func setTerminalTitle(source: LocalProcessTerminalView, title newTitle: String) {
            DispatchQueue.main.async {
                self.title = newTitle.isEmpty ? "Terminal" : newTitle
            }
        }

        func hostCurrentDirectoryUpdate(source: SwiftTerm.TerminalView, directory: String?) {}

        func processTerminated(source: SwiftTerm.TerminalView, exitCode: Int32?) {
            DispatchQueue.main.async {
                let code = exitCode.map { String($0) } ?? "?"
                self.title = "Terminal (exited \(code))"
            }
        }
    }
}
