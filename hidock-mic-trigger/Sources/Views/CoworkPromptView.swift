import SwiftUI

struct CoworkPromptView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var copied = false

    private let prompt = """
    Set up a scheduled project that monitors my HiDock transcripts and automatically generates summaries using the correct template.

    ## Folders
    - Transcriptions: ~/HiDock/Transcriptions/
    - Summary Templates: ~/HiDock/Summary Templates/
    - Summaries output: ~/HiDock/Summaries/

    ## Processing Rules

    ### 1. Check readiness
    For each transcript in ~/HiDock/Transcriptions/, find the matching _diarized.json file. Only process transcripts where ALL speakers have been named (no "Speaker 0", "Speaker 1" etc. remaining in speaker_names). Skip any transcript that already has a matching summary in ~/HiDock/Summaries/.

    ### 2. Assess meeting type
    Read the transcript content and determine the meeting type by analysing:
    - Number and roles of participants
    - Discussion topics and tone
    - Meeting structure (formal vs informal, status update vs deep-dive)

    ### 3. Select template
    Pick the best matching template from ~/HiDock/Summary Templates/:
    - "1 on 1 Meeting" — two participants, informal catch-up or coaching
    - "Client or External Meeting" — mixed internal/external attendees
    - "Job Interview" — candidate + interviewer dynamic
    - "Project Sync" — technical/delivery focused, sprint or milestone review
    - "Stand Up Meeting" — short, status-update format
    - "Brainstorming" — ideation, open-ended exploration
    - "Podcast" — interview/conversation format for publication
    - "Retrospective Meeting" — what went well / what to improve
    - "Weekly Team Meeting" — recurring team sync with multiple topics
    - "Project kick-off" — new initiative, roles and milestones
    - "Training or Workshop" — learning/teaching session
    - "General Meeting" — fallback if no clear match

    ### 4. Generate summary
    Apply the selected template to the transcript, following all extraction guidance within the template. Output to ~/HiDock/Summaries/ with filename format:
    YYYY-MM-DD - {Template Name} - {Area} - {Short Description}.md

    ### 5. Obsidian integration
    After generating the summary, copy it into the Obsidian vault with this frontmatter prepended:
    ---
    type: meeting
    date: YYYY-MM-DD
    template: {template name used}
    area: {extracted area from template}
    participants:
      - "[[Participant Name]]"
    tags: [meeting, {area-slug}, {template-slug}]
    source: {original transcript filename}
    ---

    Then run: obsidian open --path "{note path}"
    """

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                Label("Cowork Setup Prompt", systemImage: "sparkles")
                    .font(.headline)
                Spacer()
                Button("Done") { dismiss() }
                    .keyboardShortcut(.cancelAction)
            }

            Text("Copy this prompt and paste it into Claude Cowork to set up automated transcript summarisation.")
                .font(.caption)
                .foregroundColor(.secondary)

            ScrollView {
                Text(prompt)
                    .font(.system(.caption, design: .monospaced))
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(12)
                    .background(Color(nsColor: .textBackgroundColor))
                    .cornerRadius(8)
            }

            HStack {
                Spacer()
                Button {
                    NSPasteboard.general.clearContents()
                    NSPasteboard.general.setString(prompt, forType: .string)
                    copied = true
                    DispatchQueue.main.asyncAfter(deadline: .now() + 2) {
                        copied = false
                    }
                } label: {
                    Label(copied ? "Copied!" : "Copy to Clipboard", systemImage: copied ? "checkmark" : "doc.on.doc")
                }
                .buttonStyle(.borderedProminent)
                .controlSize(.large)
            }
        }
        .padding(20)
        .frame(width: 640, height: 520)
    }
}
