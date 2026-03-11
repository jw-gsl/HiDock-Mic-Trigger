import XCTest
@testable import hidock_mic_trigger

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
