import SwiftUI

struct TrimAudioView: View {
    let filename: String
    let duration: Double
    let onTrim: (Double, Double, Bool) -> Void
    let onCancel: () -> Void

    @State private var startText: String = "00:00"
    @State private var endText: String = ""
    @State private var saveAsCopy: Bool = true
    @State private var errorText: String = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Trim Audio")
                .font(.headline)

            Text(filename)
                .font(.subheadline)
                .foregroundColor(.secondary)

            Text("Duration: \(formatTime(duration))")
                .font(.caption)
                .foregroundColor(.secondary)

            HStack {
                VStack(alignment: .leading) {
                    Text("Start").font(.caption.weight(.medium))
                    TextField("MM:SS", text: $startText)
                        .textFieldStyle(.roundedBorder)
                        .frame(width: 80)
                }

                VStack(alignment: .leading) {
                    Text("End").font(.caption.weight(.medium))
                    TextField("MM:SS", text: $endText)
                        .textFieldStyle(.roundedBorder)
                        .frame(width: 80)
                }
            }

            Toggle("Save as copy", isOn: $saveAsCopy)
                .toggleStyle(.checkbox)

            if !errorText.isEmpty {
                Text(errorText)
                    .font(.caption)
                    .foregroundColor(.red)
            }

            HStack {
                Spacer()
                Button("Cancel") { onCancel() }
                    .keyboardShortcut(.cancelAction)
                Button("Trim") { performTrim() }
                    .keyboardShortcut(.defaultAction)
            }
        }
        .padding()
        .frame(width: 300)
        .onAppear {
            endText = formatTime(duration)
        }
    }

    private func performTrim() {
        guard let start = parseTime(startText) else {
            errorText = "Invalid start time (use MM:SS or HH:MM:SS)"
            return
        }
        guard let end = parseTime(endText) else {
            errorText = "Invalid end time (use MM:SS or HH:MM:SS)"
            return
        }
        guard start < end else {
            errorText = "Start must be before end"
            return
        }
        guard end <= duration + 1 else {
            errorText = "End exceeds recording duration"
            return
        }
        errorText = ""
        onTrim(start, end, saveAsCopy)
    }

    private func formatTime(_ seconds: Double) -> String {
        let total = Int(seconds)
        let h = total / 3600
        let m = (total % 3600) / 60
        let s = total % 60
        if h > 0 {
            return String(format: "%d:%02d:%02d", h, m, s)
        }
        return String(format: "%02d:%02d", m, s)
    }

    private func parseTime(_ text: String) -> Double? {
        let parts = text.split(separator: ":").compactMap { Int($0) }
        switch parts.count {
        case 2: return Double(parts[0] * 60 + parts[1])
        case 3: return Double(parts[0] * 3600 + parts[1] * 60 + parts[2])
        default: return nil
        }
    }
}
