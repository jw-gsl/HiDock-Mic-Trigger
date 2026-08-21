import XCTest
@testable import hidock_mic_trigger

/// Covers computeTranscriptionTimeout's duration-plausibility guard, added
/// after a real Plaud recording (Opus muxed in Ogg, named ".mp3") got a
/// ~11s duration from AVFoundation's probe instead of its true ~3h06m,
/// producing a ~10-minute timeout that killed a transcription still
/// legitimately running.
final class TranscriptionTimeoutTests: XCTestCase {
    private func makeFile(sizeBytes: Int) throws -> URL {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString + ".mp3")
        let data = Data(count: sizeBytes)
        try data.write(to: url)
        addTeardownBlock { try? FileManager.default.removeItem(at: url) }
        return url
    }

    func testPlausibleKnownDurationIsUsed() throws {
        // ~1MB for 10 minutes of audio — an entirely ordinary voice-memo bitrate.
        let url = try makeFile(sizeBytes: 1_000_000)
        let timeout = AppDelegate.computeTranscriptionTimeout(for: url.path, knownDuration: 600)
        XCTAssertEqual(timeout, 600 * 1.5 + 600)
    }

    func testImplausibleKnownDurationFallsBackToFileSize() throws {
        // 45MB in 11 seconds implies ~4 MB/s — no real audio codec does that.
        let sizeBytes = 45 * 1024 * 1024
        let url = try makeFile(sizeBytes: sizeBytes)
        let timeout = AppDelegate.computeTranscriptionTimeout(for: url.path, knownDuration: 11)
        let fileSizeMB = Double(sizeBytes) / (1024 * 1024)
        XCTAssertEqual(timeout, min(14400.0, fileSizeMB * 60.0 + 600.0))
    }

    func testTimeoutIsCappedAtFourHours() throws {
        let url = try makeFile(sizeBytes: 1_000_000)
        let timeout = AppDelegate.computeTranscriptionTimeout(for: url.path, knownDuration: 100_000)
        XCTAssertEqual(timeout, 14400.0)
    }

    func testTinyPlausibleDurationIsNotRejected() throws {
        // A 1KB file with a 1-second duration is an entirely ordinary
        // bitrate — must use the duration formula, not fall through to the
        // (in this case wildly pessimistic) file-size heuristic.
        let url = try makeFile(sizeBytes: 1_000)
        let timeout = AppDelegate.computeTranscriptionTimeout(for: url.path, knownDuration: 1)
        XCTAssertEqual(timeout, 1 * 1.5 + 600)
    }
}
