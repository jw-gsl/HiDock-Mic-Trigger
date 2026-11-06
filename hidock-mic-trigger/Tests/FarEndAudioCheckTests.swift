import XCTest
@testable import hidock_mic_trigger

/// The one-voice H1 warning: fires for the calls lost on 5–6 Nov 2026
/// (H1, 15–53 minutes, one speaker) and stays quiet for everything else.
final class FarEndAudioCheckTests: XCTestCase {
    private let now = Date()
    private var savedWarned: Any?

    override func setUp() {
        savedWarned = UserDefaults.standard.object(forKey: FarEndAudioCheck.warnedKey)
        UserDefaults.standard.removeObject(forKey: FarEndAudioCheck.warnedKey)
    }

    override func tearDown() {
        UserDefaults.standard.set(savedWarned, forKey: FarEndAudioCheck.warnedKey)
    }

    private func warns(device: String = "HiDock H1", type: DeviceType = .hidock,
                       minutes: Double = 43, speakers: Set<Int> = [0],
                       hoursAgo: Double = 1) -> Bool {
        FarEndAudioCheck.shouldWarn(deviceName: device, deviceType: type,
                                    duration: minutes * 60, speakerIds: speakers,
                                    recordedAt: now.addingTimeInterval(-hoursAgo * 3600), now: now)
    }

    func testWarnsForLongOneVoiceH1Recording() {
        XCTAssertTrue(warns())
        XCTAssertTrue(warns(minutes: 15))          // Rec04, the shortest lost call
        XCTAssertTrue(warns(device: "HiDock H1E"))
    }

    func testQuietWhenMoreThanOneVoice() {
        XCTAssertFalse(warns(speakers: [0, 1]))
    }

    func testQuietWithNoSpeech() {
        XCTAssertFalse(warns(speakers: []))
    }

    func testQuietForShortRecordings() {
        XCTAssertFalse(warns(minutes: 9))
    }

    func testQuietForOtherDevices() {
        XCTAssertFalse(warns(device: "HiDock P1"))
        XCTAssertFalse(warns(device: "ZOOM_H1", type: .volume))
        XCTAssertFalse(warns(device: "Plaud", type: .plaud))
    }

    func testQuietForOldRecordings() {
        XCTAssertFalse(warns(hoursAgo: 25))
    }

    func testUnknownRecordingTimeStillWarns() {
        XCTAssertTrue(FarEndAudioCheck.shouldWarn(deviceName: "HiDock H1", deviceType: .hidock,
                                                  duration: 1800, speakerIds: [0], recordedAt: nil))
    }

    func testParsesDockTimestamp() {
        let date = FarEndAudioCheck.recordedAt(createDate: "2026/11/06", createTime: "11:45:42")
        let parts = date.map { Calendar.current.dateComponents([.year, .month, .day, .hour, .minute], from: $0) }
        XCTAssertEqual(parts?.year, 2026)
        XCTAssertEqual(parts?.month, 11)
        XCTAssertEqual(parts?.day, 6)
        XCTAssertEqual(parts?.hour, 11)
        XCTAssertEqual(parts?.minute, 45)
        XCTAssertNil(FarEndAudioCheck.recordedAt(createDate: "", createTime: ""))
    }

    func testWarnsOncePerRecording() {
        XCTAssertFalse(FarEndAudioCheck.alreadyWarned("2026Nov06-114542-Rec09.mp3"))
        FarEndAudioCheck.markWarned("2026Nov06-114542-Rec09.mp3")
        XCTAssertTrue(FarEndAudioCheck.alreadyWarned("2026Nov06-114542-Rec09.mp3"))
    }

    func testAlertSaysToPowerCycle() {
        let text = FarEndAudioCheck.alertText(recordingName: "Rec09.mp3", duration: 2605)
        XCTAssertTrue(text.contains("43-minute"))
        XCTAssertTrue(text.contains("turn the H1 off and on again"))
    }
}
