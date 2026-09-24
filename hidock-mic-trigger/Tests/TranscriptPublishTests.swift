import XCTest
@testable import hidock_mic_trigger

final class TranscriptPublishTests: XCTestCase {

    func testMarkdownPathFromDiarizedSidecar() {
        let path = TranscriptPublish.markdownPath(
            forDiarizedPath: "/Users/x/HiDock/Raw Transcripts/2026Sep21-155935-Rec30_diarized.json")
        XCTAssertEqual(path, "/Users/x/HiDock/Raw Transcripts/2026Sep21-155935-Rec30.md")
    }

    func testMarkdownPathToleratesNonSidecarName() {
        let path = TranscriptPublish.markdownPath(
            forDiarizedPath: "/tmp/Rec9.json")
        XCTAssertEqual(path, "/tmp/Rec9.md")
    }

    func testPublishReasonStripsBeforePrefix() {
        XCTAssertEqual(AppDelegate.publishReason(fromSnapshot: "Before renaming Alice → Bob"),
                       "Renaming Alice → Bob")
    }

    func testPublishReasonLeavesOtherReasonsAlone() {
        XCTAssertEqual(AppDelegate.publishReason(fromSnapshot: "Transcript update"),
                       "Transcript update")
    }
}
