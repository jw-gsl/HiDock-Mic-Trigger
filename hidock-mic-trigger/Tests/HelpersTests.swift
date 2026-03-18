import XCTest
@testable import hidock_mic_trigger

final class SyncErrorDescriptionTests: XCTestCase {

    func testErrno13ReturnsGenericUSBBusy() {
        let msg = syncErrorDescription("Errno 13: Permission denied")
        XCTAssertEqual(msg, "USB busy — another app has the device open. Close it and Refresh.")
    }

    func testHeldByExtractsOwner() {
        let msg = syncErrorDescription("Errno 13: Access denied, held by Chrome")
        XCTAssertEqual(msg, "USB busy — held by Chrome. Close it and Refresh.")
    }

    func testWebUSBMention() {
        let msg = syncErrorDescription("Access denied by browser WebUSB")
        XCTAssertTrue(msg.contains("browser (WebUSB)"))
    }

    func testUnrelatedErrorPassedThrough() {
        let err = "Connection timed out"
        XCTAssertEqual(syncErrorDescription(err), err)
    }
}

final class FormatRecordingDurationTests: XCTestCase {

    func testZeroSeconds() {
        XCTAssertEqual(formatRecordingDuration(0), "0:00")
    }

    func testThirtySeconds() {
        XCTAssertEqual(formatRecordingDuration(30), "0:30")
    }

    func testNinetySeconds() {
        XCTAssertEqual(formatRecordingDuration(90), "1:30")
    }

    func testHoursMinutesSeconds() {
        // 3661s = 1h 1m 1s
        XCTAssertEqual(formatRecordingDuration(3661), "1:01:01")
    }

    func testNegativeClampedToZero() {
        XCTAssertEqual(formatRecordingDuration(-5), "0:00")
    }
}

final class ShortenMicNameTests: XCTestCase {

    func testRemovesMicrophone() {
        XCTAssertEqual(shortenMicName("Blue Yeti Microphone"), "Blue Yeti")
    }

    func testRemovesUSB() {
        XCTAssertEqual(shortenMicName("USB Audio Codec"), "Audio Codec")
    }

    func testMultipleNoiseWords() {
        // When all words are noise, original name is returned
        XCTAssertEqual(shortenMicName("USB Microphone Device"), "USB Microphone Device")
    }

    func testAllNoiseReturnOriginal() {
        let name = "Microphone"
        XCTAssertEqual(shortenMicName(name), name)
    }

    func testNoNoiseWordsUnchanged() {
        XCTAssertEqual(shortenMicName("Blue Yeti"), "Blue Yeti")
    }

    func testCaseInsensitive() {
        XCTAssertEqual(shortenMicName("Blue microphone"), "Blue")
    }
}

final class SanitizeDeviceNameTests: XCTestCase {

    func testRemovesSerialInParentheses() {
        XCTAssertEqual(sanitizeDeviceName("HiDock_H1_(SN123)"), "HiDock H1")
    }

    func testRemovesSerialInBrackets() {
        XCTAssertEqual(sanitizeDeviceName("HiDock_H1_[SN123]"), "HiDock H1")
    }

    func testReplacesUnderscores() {
        XCTAssertEqual(sanitizeDeviceName("HiDock_P1"), "HiDock P1")
    }

    func testPlainHiDock() {
        XCTAssertEqual(sanitizeDeviceName("HiDock"), "HiDock")
    }

    func testTrimsWhitespace() {
        XCTAssertEqual(sanitizeDeviceName("  HiDock_H1  "), "HiDock H1")
    }
}
