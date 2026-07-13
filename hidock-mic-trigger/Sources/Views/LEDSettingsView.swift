import SwiftUI

/// Settings for the LED ticker, shown in a popover from the heatmap header gear.
/// Persists live through the `LEDSettings` `ObservableObject`.
struct LEDSettingsView: View {
    @ObservedObject var settings: LEDSettings

    var body: some View {
        Form {
            Toggle("Enable LED ticker", isOn: $settings.enabled)

            if settings.enabled {
                Section("Display") {
                    Picker("At rest", selection: $settings.defaultView) {
                        ForEach(LEDDefaultView.allCases) { Text($0.label).tag($0) }
                    }
                    Toggle("Take over the heatmap on events", isOn: $settings.eventTakeover)
                    Picker("Colour", selection: $settings.colorScheme) {
                        ForEach(LEDColorScheme.allCases) { Text($0.label).tag($0) }
                    }
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Brightness").font(.caption).foregroundColor(.secondary)
                        Slider(value: $settings.brightness, in: 0.3...1.0)
                    }
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Scroll speed").font(.caption).foregroundColor(.secondary)
                        Slider(value: $settings.scrollSpeed, in: 8...40)
                    }
                }

                Section("Idle ticker") {
                    Toggle("Show when idle", isOn: $settings.idleTickerEnabled)
                    if settings.idleTickerEnabled {
                        ForEach(LEDIdleContent.allCases) { c in
                            Toggle(c.label, isOn: idleBinding(c))
                        }
                    }
                }

                Section("Announce events") {
                    ForEach(LEDEventKind.allCases) { k in
                        Toggle(k.label, isOn: eventBinding(k))
                    }
                }
            }
        }
        .formStyle(.grouped)
        .frame(width: 320, height: 520)
    }

    private func eventBinding(_ k: LEDEventKind) -> Binding<Bool> {
        Binding(
            get: { settings.enabledEvents.contains(k) },
            set: { on in
                if on { settings.enabledEvents.insert(k) } else { settings.enabledEvents.remove(k) }
            }
        )
    }

    private func idleBinding(_ c: LEDIdleContent) -> Binding<Bool> {
        Binding(
            get: { settings.idleContents.contains(c) },
            set: { on in
                if on { settings.idleContents.insert(c) } else { settings.idleContents.remove(c) }
            }
        )
    }
}
