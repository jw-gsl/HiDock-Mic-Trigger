import SwiftUI

// MARK: - Placeholder-capable TextEditor wrapper

/// A TextEditor that displays placeholder text when empty.
/// Uses a ZStack overlay so CMD+C / CMD+V and all standard shortcuts work natively.
struct FeedbackTextEditor: View {
    @Binding var text: String
    let placeholder: String

    var body: some View {
        ZStack(alignment: .topLeading) {
            TextEditor(text: $text)
            if text.isEmpty {
                Text(placeholder)
                    .foregroundColor(Color(nsColor: .placeholderTextColor))
                    .font(.body)
                    .padding(.horizontal, 5)
                    .padding(.vertical, 8)
                    .allowsHitTesting(false)
            }
        }
    }
}

// MARK: - Feedback Form View

struct FeedbackView: View {
    @State private var whatHappened: String = ""
    @State private var whatExpected: String = ""
    @State private var stepsToReproduce: String = ""

    var onSubmit: (String, String, String) -> Void
    var onCancel: () -> Void

    private let fieldHeight: CGFloat = 100
    private let fieldCornerRadius: CGFloat = 4

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("Send Feedback")
                .font(.title2.bold())
                .padding(.bottom, 4)

            // What happened
            VStack(alignment: .leading, spacing: 4) {
                Text("What happened?")
                    .font(.headline)
                FeedbackTextEditor(
                    text: $whatHappened,
                    placeholder: "e.g. I clicked Download, waited 30 seconds, and the app froze."
                )
                .frame(height: fieldHeight)
                .overlay(
                    RoundedRectangle(cornerRadius: fieldCornerRadius)
                        .stroke(Color(nsColor: .separatorColor), lineWidth: 1)
                )
                .cornerRadius(fieldCornerRadius)
            }

            // What did you expect
            VStack(alignment: .leading, spacing: 4) {
                Text("What did you expect to happen?")
                    .font(.headline)
                FeedbackTextEditor(
                    text: $whatExpected,
                    placeholder: "e.g. The download should complete and the recordings should appear in the list."
                )
                .frame(height: fieldHeight)
                .overlay(
                    RoundedRectangle(cornerRadius: fieldCornerRadius)
                        .stroke(Color(nsColor: .separatorColor), lineWidth: 1)
                )
                .cornerRadius(fieldCornerRadius)
            }

            // Steps to reproduce (optional)
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 6) {
                    Text("Steps to reproduce")
                        .font(.headline)
                    Text("(optional)")
                        .font(.subheadline)
                        .foregroundColor(.secondary)
                }
                FeedbackTextEditor(
                    text: $stepsToReproduce,
                    placeholder: "e.g.\n1. Open the app\n2. Click Download\n3. Wait 30 seconds\n4. App freezes"
                )
                .frame(height: fieldHeight)
                .overlay(
                    RoundedRectangle(cornerRadius: fieldCornerRadius)
                        .stroke(Color(nsColor: .separatorColor), lineWidth: 1)
                )
                .cornerRadius(fieldCornerRadius)
            }

            // Buttons
            HStack {
                Spacer()
                Button("Cancel") {
                    onCancel()
                }
                .keyboardShortcut(.cancelAction)

                Button("Send Feedback") {
                    onSubmit(whatHappened, whatExpected, stepsToReproduce)
                }
                .keyboardShortcut(.defaultAction)
                .disabled(whatHappened.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
        .padding(20)
        .frame(width: 540)
    }
}
