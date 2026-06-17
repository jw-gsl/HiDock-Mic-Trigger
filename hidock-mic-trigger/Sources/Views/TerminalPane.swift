import SwiftUI
import AppKit
import SwiftTerm

/// Owns the single embedded-pane terminal: a persistent interactive login
/// shell that hosts "Ask Claude Code" (commands typed into the PTY) and
/// mirrors summarise activity (display-only feed). Created once and shared
/// between the SwiftUI pane (which displays it) and AppDelegate (which
/// drives it), so the shell session survives the pane being toggled shut
/// and reopened.
final class TerminalPaneController: NSObject, ObservableObject, LocalProcessTerminalViewDelegate {
    /// The live terminal NSView. Lazily created so merely owning the
    /// controller (e.g. from the view model) doesn't spin up a PTY before
    /// the user ever opens the pane.
    private(set) lazy var terminalView: LocalProcessTerminalView = makeTerminal()
    private var started = false

    private func makeTerminal() -> LocalProcessTerminalView {
        let tv = LocalProcessTerminalView(frame: .zero)
        tv.processDelegate = self
        tv.translatesAutoresizingMaskIntoConstraints = false
        return tv
    }

    /// Start the login shell once (idempotent). `-l -i` so ~/.zprofile,
    /// /etc/paths and ~/.zshrc are sourced — the user's full PATH (brew,
    /// npm, nvm) is needed for `claude` to be found.
    func ensureStarted() {
        guard !started else { return }
        started = true
        let shell = ProcessInfo.processInfo.environment["SHELL"] ?? "/bin/zsh"
        var env: [String] = []
        for (k, v) in ProcessInfo.processInfo.environment { env.append("\(k)=\(v)") }
        if !env.contains(where: { $0.hasPrefix("TERM=") }) { env.append("TERM=xterm-256color") }
        terminalView.startProcess(executable: shell, args: ["-l", "-i"], environment: env)
    }

    /// Type a command into the shell (as if the user typed it) and run it.
    /// PTY input is kernel-buffered, so this is safe even immediately after
    /// `ensureStarted()` while the shell is still coming up.
    func runCommand(_ cmd: String) {
        ensureStarted()
        let line = cmd.hasSuffix("\n") ? cmd : cmd + "\n"
        terminalView.send(data: Array(line.utf8)[...])
    }

    /// Display-only activity line — written to the terminal screen, NOT to
    /// the shell's stdin, so it can't collide with whatever the user is
    /// typing at the prompt. Used for summarise start/finish markers.
    /// `\r\n` so each line starts at column 0 in the raw terminal.
    func appendActivity(_ text: String) {
        ensureStarted()
        terminalView.feed(text: text + "\r\n")
    }

    // MARK: LocalProcessTerminalViewDelegate
    func sizeChanged(source: LocalProcessTerminalView, newCols: Int, newRows: Int) {}
    func setTerminalTitle(source: LocalProcessTerminalView, title: String) {}
    func hostCurrentDirectoryUpdate(source: SwiftTerm.TerminalView, directory: String?) {}
    func processTerminated(source: SwiftTerm.TerminalView, exitCode: Int32?) {
        // Shell exited (user typed `exit`). Allow a fresh shell next time
        // the pane is used.
        started = false
    }
}

/// SwiftUI host for the shared CLI pane. Header strip + the terminal view.
struct EmbeddedTerminalPane: View {
    @ObservedObject var controller: TerminalPaneController
    var onClose: () -> Void

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 6) {
                Image(systemName: "terminal")
                Text("CLI")
                    .font(.headline)
                Spacer()
                Button {
                    onClose()
                } label: {
                    Image(systemName: "xmark.circle.fill")
                        .foregroundColor(.secondary)
                }
                .buttonStyle(.plain)
                .help("Hide the CLI pane (the session keeps running)")
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 6)
            .background(Color(NSColor.windowBackgroundColor))
            Divider()
            TerminalPaneRepresentable(controller: controller)
        }
        .onAppear { controller.ensureStarted() }
    }
}

/// Returns the controller's persistent terminal view so the session is
/// preserved across show/hide toggles (SwiftUI re-adds the same NSView).
private struct TerminalPaneRepresentable: NSViewRepresentable {
    let controller: TerminalPaneController

    func makeNSView(context: Context) -> LocalProcessTerminalView {
        controller.ensureStarted()
        return controller.terminalView
    }

    func updateNSView(_ nsView: LocalProcessTerminalView, context: Context) {}
}
