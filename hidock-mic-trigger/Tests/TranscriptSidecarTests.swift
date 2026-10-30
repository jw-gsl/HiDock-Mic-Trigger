import XCTest
@testable import hidock_mic_trigger

final class TranscriptSidecarSaveTests: XCTestCase {

    private let existing = """
    {
      "version": 1,
      "audio_file": "rec.mp3",
      "backend": "nemotron",
      "speaker_names": {"0": "Speaker 1"},
      "speaker_meta": {"0": {"source": "auto", "verified": false}},
      "segments": [{"start": 0.0, "end": 2.0, "speaker_id": 0, "text": "Hello"}],
      "calendar_context": {"calendar_event_title": "[BT] All-hands quarterly"},
      "speaker_count_strategy": "arrival-order"
    }
    """

    private func decoded() throws -> DiarizedTranscript {
        try JSONDecoder().decode(DiarizedTranscript.self, from: Data(existing.utf8))
    }

    private func object(_ data: Data) throws -> [String: Any] {
        try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
    }

    /// Rec77 lost its linked meeting at the first edit in the viewer.
    func testSaveKeepsKeysTheViewerDoesNotModel() throws {
        var transcript = try decoded()
        transcript.speakerNames["0"] = "Chris Wildsmith"
        let saved = try object(DiarizedTranscript.sidecarData(for: transcript, existing: Data(existing.utf8)))
        XCTAssertEqual((saved["speaker_names"] as? [String: String])?["0"], "Chris Wildsmith")
        XCTAssertEqual((saved["calendar_context"] as? [String: Any])?["calendar_event_title"] as? String,
                       "[BT] All-hands quarterly")
        XCTAssertEqual(saved["speaker_count_strategy"] as? String, "arrival-order")
    }

    /// A modelled key the transcript no longer has is removed, not kept stale.
    func testClearingAModelledKeyRemovesIt() throws {
        var transcript = try decoded()
        transcript.speakerMeta = nil
        let saved = try object(DiarizedTranscript.sidecarData(for: transcript, existing: Data(existing.utf8)))
        XCTAssertNil(saved["speaker_meta"])
        XCTAssertNotNil(saved["calendar_context"])
    }

    func testNoExistingFileWritesTheTranscript() throws {
        let saved = try object(DiarizedTranscript.sidecarData(for: try decoded(), existing: nil))
        XCTAssertEqual(saved["backend"] as? String, "nemotron")
        XCTAssertNil(saved["calendar_context"])
    }
}

final class UnderSplitWarningTests: XCTestCase {

    private let rec77 = SpeakerCountCheck(invitees: 20, inviteeBasis: "invited", voicesFound: 7,
                                          backend: "nemotron", backendMaxSpeakers: 8)

    func testWarnsWhenDetectionHitItsLimit() throws {
        let text = try XCTUnwrap(rec77.warning(speakersNow: 7))
        XCTAssertTrue(text.contains("at most 8 voices"))
        XCTAssertTrue(text.contains("20 people were invited"))
    }

    func testClearsOnceTheReviewerHasSplitThemOut() {
        XCTAssertNil(rec77.warning(speakersNow: 18))
        XCTAssertNil(rec77.warning(speakersNow: 20))
    }

    func testSmallMeetingWithAFewNoShowsIsNotFlagged() {
        let check = SpeakerCountCheck(invitees: 6, inviteeBasis: "invited", voicesFound: 4,
                                      backend: "nemotron", backendMaxSpeakers: 8)
        XCTAssertNil(check.warning(speakersNow: 4))
    }

    func testFarFewerVoicesThanInviteesWithoutALimit() {
        let check = SpeakerCountCheck(invitees: 10, inviteeBasis: "accepted", voicesFound: 3,
                                      backend: "lite", backendMaxSpeakers: nil)
        XCTAssertNotNil(check.warning(speakersNow: 3))
    }
}

final class RefineScoreLabelTests: XCTestCase {

    func testMissingVoiceIsLabelledNotZero() {
        XCTAssertEqual(VoiceCheckPanel.match("David McGregor", nil), "David McGregor: no saved voice")
        XCTAssertEqual(VoiceCheckPanel.match("Jeff Chow", 0.571), "Jeff Chow 0.57")
    }

    func testNamingLibraryOutcomeSaysWhatHappened() {
        let saved = Data(#"{"enrolled": true, "clip_start": 1774.2, "clip_end": 1788.6, "verification": "new voice", "library": "redimnet2_b6"}"#.utf8)
        XCTAssertEqual(AppDelegate.namingLibraryOutcome(saved, name: "Salman Qayyum"),
                       "Naming library (redimnet2_b6): saved Salman Qayyum's voice from 29:34–29:48 (new voice)")
        let refused = Data(#"{"status": "completed", "event": {"enrolled": false, "not_enrolled_reason": "too short", "review_only_model": "redimnet2_b6"}}"#.utf8)
        XCTAssertEqual(AppDelegate.namingLibraryOutcome(refused, name: "George"),
                       "Naming library (redimnet2_b6): George's voice NOT saved: too short")
    }
}
