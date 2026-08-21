import XCTest
@testable import hidock_mic_trigger

/// Covers extractorFailureMessage, added after a stalled download (Rec57,
/// 2026-08-19) produced an NSAlert of ~2,150 raw PROGRESS lines — the
/// subprocess's entire stderr, dumped verbatim, with the one line that
/// actually mattered (the TimeoutError) buried at the very end.
final class ExtractorFailureMessageTests: XCTestCase {
    private func data(_ s: String) -> Data { s.data(using: .utf8)! }

    func testDropsProgressNoiseAndKeepsTheRealError() {
        let progressLines = (0..<2000).map { "PROGRESS:\($0 * 8180):17593644:\(min(99, $0 / 20))" }
        let raw = (progressLines + ["TimeoutError: transfer stalled after 17587000 of 17593644 bytes for rec.hda"])
            .joined(separator: "\n")
        let message = AppDelegate.extractorFailureMessage(from: data(raw))
        XCTAssertTrue(message.contains("TimeoutError"))
        XCTAssertFalse(message.contains("PROGRESS:"))
        XCTAssertLessThan(message.count, 1300)
    }

    func testNoProgressLinesPassesThroughUnchanged() {
        let raw = "Device not found"
        XCTAssertEqual(AppDelegate.extractorFailureMessage(from: data(raw)), "Device not found")
    }

    func testEmptyDataReturnsEmptyString() {
        XCTAssertEqual(AppDelegate.extractorFailureMessage(from: Data()), "")
    }

    func testAllProgressLinesFallsBackToRawTailRatherThanNothing() {
        let raw = (0..<50).map { "PROGRESS:\($0):100:\($0)" }.joined(separator: "\n")
        let message = AppDelegate.extractorFailureMessage(from: data(raw))
        XCTAssertFalse(message.isEmpty)
    }

    func testVeryLongSingleLineIsTruncatedFromTheFront() {
        let raw = "ERROR: " + String(repeating: "x", count: 5000)
        let message = AppDelegate.extractorFailureMessage(from: data(raw), maxLength: 1200)
        XCTAssertLessThanOrEqual(message.count, 1201)  // 1200 + the "…" marker
        XCTAssertTrue(message.hasPrefix("…"))
        XCTAssertTrue(message.hasSuffix("x"))
    }
}
