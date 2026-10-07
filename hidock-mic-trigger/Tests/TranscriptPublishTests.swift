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

    func testRecordingStemsFromPipelineArguments() {
        let stems = TranscriptPublish.recordingStems(in: [
            "rename-speaker",
            "/Users/x/HiDock/Raw Transcripts/2026Oct06-132930-Rec48_diarized.json",
            "--speaker-id", "0",
            "/Users/x/HiDock/Recordings/2026Oct06-143340-Rec49.mp3",
            "/Users/x/HiDock/Raw Transcripts/2026Oct06-143340-Rec49_calendar.json",
        ])
        XCTAssertEqual(stems, ["2026Oct06-132930-Rec48", "2026Oct06-143340-Rec49"])
    }

    func testRecordingStemsIgnoresNonFileArguments() {
        XCTAssertEqual(TranscriptPublish.recordingStems(in: ["status", "--final-name", "Jeff Chow"]), [])
    }

    func testCommitMessageForOneTranscriptSummarisesEdits() {
        let entry = TranscriptPublish.PendingTranscript(
            lastActivity: Date(), reasons: ["Transcribed", "Renaming 1 → Jeff Chow", "Merging 2 into 1"])
        let (title, body) = TranscriptPublish.commitMessage(for: [("/t/Rec48.md", entry)])
        XCTAssertEqual(title, "Rec48: Merging 2 into 1 (+2 more edits)")
        XCTAssertEqual(body, "- Rec48: Transcribed; Renaming 1 → Jeff Chow; Merging 2 into 1")
    }

    func testCommitMessageBatchesSeveralTranscripts() {
        let a = TranscriptPublish.PendingTranscript(lastActivity: Date(), reasons: ["Transcribed"])
        let b = TranscriptPublish.PendingTranscript(lastActivity: Date(), reasons: [])
        let (title, body) = TranscriptPublish.commitMessage(for: [("/t/Rec50.md", a), ("/t/Rec49.md", b)])
        XCTAssertEqual(title, "Update 2 transcripts")
        XCTAssertEqual(body, "- Rec49: Updated\n- Rec50: Transcribed")
    }
}
