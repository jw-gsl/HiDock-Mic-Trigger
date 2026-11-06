import Foundation

/// Spots HiDock H1 recordings that contain only one voice, the sign that the
/// dock has stopped recording the call audio played through it.
///
/// On 5–6 Nov 2026 the H1 got into a state where it kept recording its own
/// microphone but dropped everything Teams played to it, so six calls came
/// out as one speaker. Nothing in the app or the dock reported it; only the
/// diarizer's speaker count showed it. Turning the dock off and on fixed it
/// (docs/PLAN-missing-far-end-audio.md). This check turns that count into a
/// popup after transcription, so a stuck dock is caught after one call
/// instead of six.
enum FarEndAudioCheck {
    /// UserDefaults key for the Settings → Transcription toggle (default on).
    static let enabledKey = "farEndAudioWarningEnabled"
    /// Recordings already warned about, so a re-transcription doesn't repeat it.
    static let warnedKey = "farEndAudioWarnedRecordings"
    /// Shorter recordings are often voice notes, where one voice is expected.
    static let minimumDuration: TimeInterval = 10 * 60
    /// Only recent recordings say anything about the dock's state now; a
    /// backlog or a re-run of last month's files shouldn't raise a popup.
    static let maximumAge: TimeInterval = 24 * 60 * 60
    private static let warnedLimit = 200

    static var isEnabled: Bool {
        UserDefaults.standard.object(forKey: enabledKey) as? Bool ?? true
    }

    /// Whether a just-transcribed recording should raise the warning.
    ///
    /// - Parameters:
    ///   - deviceName: the sync entry's device name; only H1/H1E docks qualify
    ///     (the P1 and other sources don't carry call audio).
    ///   - speakerIds: speaker ids of the transcript's non-empty segments.
    ///   - recordedAt: when the dock started the recording, if known.
    static func shouldWarn(deviceName: String,
                           deviceType: DeviceType,
                           duration: TimeInterval,
                           speakerIds: Set<Int>,
                           recordedAt: Date?,
                           now: Date = Date()) -> Bool {
        guard let sku = hidockSKU(for: deviceName, deviceType: deviceType),
              sku == .h1 || sku == .h1e else { return false }
        guard duration >= minimumDuration else { return false }
        // No speech at all is a different problem (silence, failed ASR).
        guard speakerIds.count == 1 else { return false }
        if let recordedAt, now.timeIntervalSince(recordedAt) > maximumAge { return false }
        return true
    }

    /// The dock's own start time ("2026/11/06" + "11:45:42", local time).
    static func recordedAt(createDate: String, createTime: String) -> Date? {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = .current
        formatter.dateFormat = "yyyy/MM/dd HH:mm:ss"
        return formatter.date(from: "\(createDate) \(createTime)")
    }

    static func alreadyWarned(_ recordingName: String) -> Bool {
        (UserDefaults.standard.stringArray(forKey: warnedKey) ?? []).contains(recordingName)
    }

    static func markWarned(_ recordingName: String) {
        var names = UserDefaults.standard.stringArray(forKey: warnedKey) ?? []
        guard !names.contains(recordingName) else { return }
        names.append(recordingName)
        UserDefaults.standard.set(Array(names.suffix(warnedLimit)), forKey: warnedKey)
    }

    static func alertTitle() -> String {
        "Only one voice in this H1 recording"
    }

    static func alertText(recordingName: String, duration: TimeInterval) -> String {
        let minutes = max(1, Int((duration / 60).rounded()))
        return """
        \(recordingName) is a \(minutes)-minute recording from the HiDock H1, but only one speaker was found.

        If this was a call with other people, the H1 has probably stopped recording the call audio it plays. Your side is still recorded, but the other people are missing.

        To fix it, turn the H1 off and on again: unplug its power and USB cable for about 10 seconds, then plug them back in. Then make a Teams test call (Settings → Devices → Make a test call) and check that the recording includes the test call's voice.

        If this recording really had only one person speaking, you can ignore this.
        """
    }
}
