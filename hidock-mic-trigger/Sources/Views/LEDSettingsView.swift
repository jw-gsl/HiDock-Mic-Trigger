import SwiftUI

/// Settings for the LED ticker, shown in a popover from the heatmap header gear.
/// Persists live through the `LEDSettings` `ObservableObject`.
struct LEDSettingsView: View {
    @ObservedObject var settings: LEDSettings
    /// Sends a test announcement, to see the ticker work without waiting for
    /// an event.
    var onTest: (() -> Void)? = nil

    var body: some View {
        Form {
            Section {
                Toggle("Enable LED ticker", isOn: $settings.enabled)
                if settings.enabled, let onTest {
                    Button("Send a test message", action: onTest)
                }
            } footer: {
                Text("Shows over the meeting heatmap on the main window.")
            }

            if settings.enabled {
                Section("Display") {
                    Picker("At rest", selection: $settings.defaultView) {
                        ForEach(LEDDefaultView.allCases) { Text($0.label).tag($0) }
                    }
                    if settings.defaultView == .heatmap {
                        Toggle("Show events over the heatmap", isOn: $settings.eventTakeover)
                    }
                    Picker("Colour", selection: $settings.colorScheme) {
                        ForEach(LEDColorScheme.allCases) { Text($0.label).tag($0) }
                    }
                    Slider(value: $settings.brightness, in: 0.3...1.0) { Text("Brightness") }
                    Slider(value: $settings.scrollSpeed, in: 8...40) { Text("Scroll speed") }
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
