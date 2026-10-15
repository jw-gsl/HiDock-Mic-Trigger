import SwiftUI

/// Shown when this Mac is missing something the pipeline needs.
///
/// After a migration every one of these failed silently: no Nemotron token
/// (a whole meeting under one speaker), no voice-naming code, a missing Python
/// package, no GitHub sign-in, and the old Mac still publishing. Each row says
/// what's wrong and how to fix it.
struct SetupDoctorBanner: View {
    @ObservedObject var viewModel: HiDockViewModel

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: "stethoscope")
                .foregroundColor(.orange)
            VStack(alignment: .leading, spacing: 6) {
                Text("Setup needs attention")
                    .font(.callout.weight(.semibold))
                ForEach(viewModel.setupProblems) { problem in
                    VStack(alignment: .leading, spacing: 1) {
                        Text("\(problem.title): \(problem.detail)")
                            .font(.caption.weight(.medium))
                        if !problem.fix.isEmpty {
                            Text(problem.fix)
                                .font(.caption)
                                .foregroundColor(.secondary)
                        }
                    }
                    .textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
                }
            }
            Spacer(minLength: 8)
            Button("Check again") { viewModel.onRecheckSetup() }
                .controlSize(.small)
            Button {
                viewModel.onDismissSetupProblems()
            } label: {
                Image(systemName: "xmark")
            }
            .buttonStyle(.borderless)
            .help("Hide until the next check finds something new")
        }
        .padding(10)
        .background(RoundedRectangle(cornerRadius: 8).fill(Color.orange.opacity(0.12)))
        .overlay(RoundedRectangle(cornerRadius: 8).stroke(Color.orange.opacity(0.45), lineWidth: 1))
    }
}
