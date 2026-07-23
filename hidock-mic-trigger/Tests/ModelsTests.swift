import XCTest
@testable import hidock_mic_trigger

final class DeviceTypeTests: XCTestCase {

    func testRawValues() {
        XCTAssertEqual(DeviceType.hidock.rawValue, "hidock")
        XCTAssertEqual(DeviceType.volume.rawValue, "volume")
    }

    func testCodableRoundTrip() throws {
        let encoder = JSONEncoder()
        let decoder = JSONDecoder()
        for dt in [DeviceType.hidock, DeviceType.volume] {
            let data = try encoder.encode(dt)
            let decoded = try decoder.decode(DeviceType.self, from: data)
            XCTAssertEqual(decoded, dt)
        }
    }
}

final class PairedDeviceHiDockTests: XCTestCase {

    func testHiDockInit() {
        let dev = HiDockPairedDevice(productId: 45068, displayName: "HiDock H1")
        XCTAssertEqual(dev.productId, 45068)
        XCTAssertEqual(dev.displayName, "HiDock H1")
        XCTAssertEqual(dev.deviceType, .hidock)
        XCTAssertNil(dev.volumeName)
        XCTAssertNil(dev.subpath)
        XCTAssertNotNil(dev.pairedAt)
    }

    func testHiDockDeviceId() {
        let dev = HiDockPairedDevice(productId: 45068, displayName: "HiDock H1")
        XCTAssertEqual(dev.deviceId, "hidock:45068")
    }

    func testHiDockShortName() {
        let dev = HiDockPairedDevice(productId: 45068, displayName: "HiDock H1")
        XCTAssertEqual(dev.shortName, "H1")
    }

    func testHiDockCodableRoundTrip() throws {
        let original = HiDockPairedDevice(productId: 45068, displayName: "HiDock H1")
        let data = try JSONEncoder().encode(original)
        let decoded = try JSONDecoder().decode(HiDockPairedDevice.self, from: data)
        XCTAssertEqual(decoded.productId, 45068)
        XCTAssertEqual(decoded.displayName, "HiDock H1")
        XCTAssertEqual(decoded.deviceType, .hidock)
        XCTAssertEqual(decoded.deviceId, original.deviceId)
    }
}

final class PairedDeviceVolumeTests: XCTestCase {

    func testVolumeInit() {
        let dev = HiDockPairedDevice(volumeName: "ZOOM_H1", displayName: "ZOOM_H1", subpath: "recordings")
        XCTAssertEqual(dev.deviceType, .volume)
        XCTAssertEqual(dev.volumeName, "ZOOM_H1")
        XCTAssertEqual(dev.subpath, "recordings")
        XCTAssertTrue(dev.productId > 0)
        XCTAssertNotNil(dev.pairedAt)
    }

    func testVolumeDeviceId() {
        let dev = HiDockPairedDevice(volumeName: "ZOOM_H1", displayName: "ZOOM_H1")
        XCTAssertEqual(dev.deviceId, "volume:ZOOM_H1")
    }

    func testVolumeShortName() {
        let dev = HiDockPairedDevice(volumeName: "ZOOM_H1", displayName: "ZOOM_H1")
        // cleanName runs sanitizeDeviceName, which humanises underscores to
        // spaces for display.
        XCTAssertEqual(dev.shortName, "ZOOM H1")
    }

    func testVolumeCodableRoundTrip() throws {
        let original = HiDockPairedDevice(volumeName: "USB_REC", displayName: "USB Recorder", subpath: "audio")
        let data = try JSONEncoder().encode(original)
        let decoded = try JSONDecoder().decode(HiDockPairedDevice.self, from: data)
        XCTAssertEqual(decoded.deviceType, .volume)
        XCTAssertEqual(decoded.volumeName, "USB_REC")
        XCTAssertEqual(decoded.subpath, "audio")
        XCTAssertEqual(decoded.deviceId, original.deviceId)
    }

    func testStableHashDeterminism() {
        let d1 = HiDockPairedDevice(volumeName: "MyDrive", displayName: "MyDrive")
        let d2 = HiDockPairedDevice(volumeName: "MyDrive", displayName: "MyDrive")
        XCTAssertEqual(d1.productId, d2.productId)
    }

    func testStableHashDiffers() {
        let d1 = HiDockPairedDevice(volumeName: "DriveA", displayName: "DriveA")
        let d2 = HiDockPairedDevice(volumeName: "DriveB", displayName: "DriveB")
        XCTAssertNotEqual(d1.productId, d2.productId)
    }
}

final class PairedDeviceBackwardsCompatTests: XCTestCase {

    func testOldJsonWithoutDeviceType() throws {
        // Old JSON format: only productId + displayName, no deviceType field
        let json = """
        {"productId": 45068, "displayName": "HiDock H1"}
        """
        let data = json.data(using: .utf8)!
        let decoded = try JSONDecoder().decode(HiDockPairedDevice.self, from: data)
        XCTAssertEqual(decoded.deviceType, .hidock, "Missing deviceType should default to .hidock")
        XCTAssertEqual(decoded.productId, 45068)
        XCTAssertEqual(decoded.displayName, "HiDock H1")
        XCTAssertNil(decoded.volumeName)
        XCTAssertNil(decoded.subpath)
        XCTAssertNil(decoded.pairedAt)
    }

    func testNewJsonWithVolume() throws {
        let json = """
        {"productId": 12345, "displayName": "ZOOM", "deviceType": "volume", "volumeName": "ZOOM_H1", "subpath": "rec"}
        """
        let data = json.data(using: .utf8)!
        let decoded = try JSONDecoder().decode(HiDockPairedDevice.self, from: data)
        XCTAssertEqual(decoded.deviceType, .volume)
        XCTAssertEqual(decoded.volumeName, "ZOOM_H1")
        XCTAssertEqual(decoded.subpath, "rec")
    }

    func testArrayWithMixedOldAndNew() throws {
        let json = """
        [
            {"productId": 45068, "displayName": "HiDock H1"},
            {"productId": 99999, "displayName": "ZOOM", "deviceType": "volume", "volumeName": "ZOOM_H1"}
        ]
        """
        let data = json.data(using: .utf8)!
        let decoded = try JSONDecoder().decode([HiDockPairedDevice].self, from: data)
        XCTAssertEqual(decoded.count, 2)
        XCTAssertEqual(decoded[0].deviceType, .hidock)
        XCTAssertEqual(decoded[1].deviceType, .volume)
        XCTAssertEqual(decoded[1].volumeName, "ZOOM_H1")
    }
}

final class PairedDeviceEqualityTests: XCTestCase {

    func testEqualityByDeviceId() {
        let a = HiDockPairedDevice(productId: 45068, displayName: "HiDock H1")
        let b = HiDockPairedDevice(productId: 45068, displayName: "Different Name")
        XCTAssertEqual(a, b, "Devices with same deviceId should be equal")
    }

    func testInequalityDifferentProductId() {
        let a = HiDockPairedDevice(productId: 45068, displayName: "H1")
        let b = HiDockPairedDevice(productId: 45070, displayName: "H1")
        XCTAssertNotEqual(a, b)
    }

    func testInequalityDifferentType() {
        let hidock = HiDockPairedDevice(productId: 45068, displayName: "Test")
        let volume = HiDockPairedDevice(volumeName: "Test", displayName: "Test")
        XCTAssertNotEqual(hidock, volume)
    }

    func testDeviceIdUniqueness() {
        let h1 = HiDockPairedDevice(productId: 45068, displayName: "H1")
        let h2 = HiDockPairedDevice(productId: 45070, displayName: "P1")
        let v1 = HiDockPairedDevice(volumeName: "USB1", displayName: "USB1")
        let v2 = HiDockPairedDevice(volumeName: "USB2", displayName: "USB2")
        let ids = Set([h1.deviceId, h2.deviceId, v1.deviceId, v2.deviceId])
        XCTAssertEqual(ids.count, 4)
    }
}

final class SyncRecordingEntryTests: XCTestCase {

    func testEntryIdUsesDeviceId() {
        let recording = HiDockSyncRecording(
            name: "test.raw", createDate: "2026-01-01", createTime: "12:00",
            length: 1000, duration: 10.0, version: 1, mode: "normal",
            signature: "abc", outputPath: "/tmp/test.wav", outputName: "test.wav",
            downloaded: false, localExists: false, downloadedAt: nil,
            lastError: nil, status: "on_device", humanLength: "1 KB",
            trimmed: nil, durationEstimated: nil, removed: nil
        )
        let entry = HiDockSyncRecordingEntry(
            recording: recording,
            deviceProductId: 45068,
            deviceId: "hidock:45068",
            deviceName: "H1"
        )
        XCTAssertEqual(entry.id, "hidock:45068-test.raw")
        XCTAssertEqual(entry.deviceId, "hidock:45068")
        XCTAssertEqual(entry.deviceProductId, 45068)
    }
}

final class SegmentSelectionTests: XCTestCase {

    func testSelectionCoversBoundaryAndMiddleSegments() {
        let selection = SegmentSelection(
            anchor: WordPosition(segmentIndex: 1, wordIndex: 2),
            focus: WordPosition(segmentIndex: 3, wordIndex: 1)
        )

        XCTAssertEqual(selection.wordRange(for: 1, wordCount: 5), 2...4)
        XCTAssertEqual(selection.wordRange(for: 2, wordCount: 4), 0...3)
        XCTAssertEqual(selection.wordRange(for: 3, wordCount: 6), 0...1)
        XCTAssertNil(selection.wordRange(for: 0, wordCount: 4))
    }

    func testReverseSelectionNormalisesToTranscriptOrder() {
        let selection = SegmentSelection(
            anchor: WordPosition(segmentIndex: 3, wordIndex: 1),
            focus: WordPosition(segmentIndex: 1, wordIndex: 2)
        )

        XCTAssertEqual(selection.start, WordPosition(segmentIndex: 1, wordIndex: 2))
        XCTAssertEqual(selection.end, WordPosition(segmentIndex: 3, wordIndex: 1))
        XCTAssertTrue(selection.contains(WordPosition(segmentIndex: 2, wordIndex: 0)))
        XCTAssertFalse(selection.contains(WordPosition(segmentIndex: 1, wordIndex: 1)))
    }
}

final class TranscriptRediarizeStatusTests: XCTestCase {

    func testRunningStateIsRepresented() {
        let status = TranscriptRediarizeStatus.running
        if case .running = status {
            XCTAssertTrue(true)
        } else {
            XCTFail("Expected running state")
        }
    }

    func testSuccessWithChangesIsMarkedChanged() {
        let summary = TranscriptRediarizeSummary(
            beforeSpeakerCount: 2,
            afterSpeakerCount: 3,
            changedSegmentAssignments: 7
        )
        XCTAssertTrue(summary.hasChanges)
    }

    func testSuccessWithoutChangesIsMarkedStable() {
        let summary = TranscriptRediarizeSummary(
            beforeSpeakerCount: 2,
            afterSpeakerCount: 2,
            changedSegmentAssignments: 0
        )
        XCTAssertFalse(summary.hasChanges)
    }

    func testFailureStateCarriesMessage() {
        let status = TranscriptRediarizeStatus.failed("audio unavailable")
        if case .failed(let message) = status {
            XCTAssertEqual(message, "audio unavailable")
        } else {
            XCTFail("Expected failure state")
        }
    }
}
