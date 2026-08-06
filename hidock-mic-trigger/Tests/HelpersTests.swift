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

final class HidockSKUTests: XCTestCase {

    func testP1MatchedOnFullName() {
        XCTAssertEqual(hidockSKU(for: "HiDock P1"), .p1)
    }

    func testH1MatchedOnFullName() {
        XCTAssertEqual(hidockSKU(for: "HiDock H1"), .h1)
    }

    func testH1eMatchedBeforeH1() {
        XCTAssertEqual(hidockSKU(for: "HiDock H1e"), .h1e)
        XCTAssertEqual(hidockSKU(for: "H1E"), .h1e)
    }

    func testDockAloneDoesNotMatchH1() {
        // Regression: "HiDock" alone (no model) used to match H1 via the "dock"
        // alias, which then broke "HiDock P1" because the H1 rule ran first.
        XCTAssertNil(hidockSKU(for: "HiDock"))
    }

    func testVolumeIsAlwaysNil() {
        XCTAssertNil(hidockSKU(for: "HiDock P1", deviceType: .volume))
    }

    func testDeviceIconUsesSKU() {
        XCTAssertEqual(hidockDeviceIcon("HiDock P1"), "waveform.and.mic")
        XCTAssertEqual(hidockDeviceIcon("HiDock H1"), "hifispeaker")
        XCTAssertEqual(hidockDeviceIcon("HiDock H1e"), "hifispeaker")
        XCTAssertEqual(hidockDeviceIcon("USB volume", deviceType: .volume), "externaldrive")
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

/// Invitees must belong to the event they were listed under.
///
/// The picker showed "16 invited" against both a 16-person all-hands and the
/// 4-person panel interview that followed it in the same reply, because the
/// organiser/invitee search ran over the whole answer and every parsed event was
/// handed the first one's list. An inflated invitee list is not cosmetic: it feeds
/// `allowed_names`, which restricts which identities speaker naming may assign.
final class CalendarAttendeeScopingTests: XCTestCase {

    /// The shape actually returned by the connector, from the 15:22 log entry.
    private let answer = """
    Title: Business Transformation Team - All Hands Q&A
    Time: 14:00 – 15:00
    Organiser: Jeff Chow
    Invitees: Kaushal Patel; Sarthak Sethi; Chris Wildsmith; Ian Reay; James Whiting

    Title: Volaris Business Transformation Specialist - Panel Interview
    Time: 15:00 – 16:00
    Organiser: Ian Reay
    Invitees: Chris Wildsmith; James Whiting; Jeevan Dulai
    """

    private func titleMatches(_ text: String) -> [NSTextCheckingResult] {
        let expression = try! NSRegularExpression(pattern: #"(?im)^\s*Title:\s*(.+?)\s*$"#)
        return expression.matches(in: text, range: NSRange(text.startIndex..., in: text))
    }

    func testEachEventGetsOnlyItsOwnInvitees() {
        let matches = titleMatches(answer)
        XCTAssertEqual(matches.count, 2)
        let length = (answer as NSString).length

        let first = calendarAttendeeNames(
            in: answer, region: regionForEvent(at: 0, in: matches, answerLength: length)
        )
        let second = calendarAttendeeNames(
            in: answer, region: regionForEvent(at: 1, in: matches, answerLength: length)
        )

        // Organiser counts as an attendee; both lists are their own.
        XCTAssertEqual(first, ["Chris Wildsmith", "Ian Reay", "James Whiting",
                               "Jeff Chow", "Kaushal Patel", "Sarthak Sethi"])
        XCTAssertEqual(second, ["Chris Wildsmith", "Ian Reay", "James Whiting", "Jeevan Dulai"])
        XCTAssertFalse(second.contains("Kaushal Patel"),
                       "the panel interview must not inherit the all-hands invitees")
    }

    func testLastEventRegionRunsToTheEndOfTheReply() {
        let matches = titleMatches(answer)
        let length = (answer as NSString).length
        let region = regionForEvent(at: 1, in: matches, answerLength: length)
        XCTAssertEqual(region.location + region.length, length)
    }

    func testUnavailableInviteesAreDiscarded() {
        let text = """
        Title: Some Meeting
        Time: 09:00 – 09:30
        Organiser: Ian Reay
        Invitees: unavailable
        """
        let matches = titleMatches(text)
        let names = calendarAttendeeNames(
            in: text,
            region: regionForEvent(at: 0, in: matches, answerLength: (text as NSString).length)
        )
        XCTAssertEqual(names, ["Ian Reay"])
    }
}

/// The Recording column should not restate the Created column.
final class CompactRecordingLabelTests: XCTestCase {

    func testDeviceFilenameKeepsOnlyTheRecLabel() {
        XCTAssertEqual(compactRecordingLabel("2026Jul31-150744-Rec99.mp3"), "Rec99")
        XCTAssertEqual(compactRecordingLabel("2026Jul29-135954-Rec88.mp3"), "Rec88")
        XCTAssertEqual(compactRecordingLabel("2025Oct29-130000-HiD08.mp3"), "HiD08")
    }

    func testNonNumericLabelsSurvive() {
        XCTAssertEqual(compactRecordingLabel("2026Apr17-130532-AiAccTrans.wav"), "AiAccTrans")
    }

    func testACustomNameIsKeptWhole() {
        // Nothing else in the table shows this, so shortening it would lose
        // information rather than remove a duplicate.
        let name = "Steve Jobs & Bill Gates- A Conversation.mp3"
        XCTAssertEqual(compactRecordingLabel(name), "Steve Jobs & Bill Gates- A Conversation")
    }

    func testExtensionIsAlwaysDropped() {
        XCTAssertFalse(compactRecordingLabel("2026Jul31-150744-Rec99.mp3").contains(".mp3"))
        XCTAssertFalse(compactRecordingLabel("something.hda").contains(".hda"))
    }

    func testMergedOutputNameStillReadable() {
        // Merge outputs are not in the device convention; keep them intact.
        XCTAssertEqual(compactRecordingLabel("merged-2026Jul10-abc.mp3"), "merged-2026Jul10-abc")
    }

    func testMergedOutputShowsTheSpanItCovers() {
        // The caller used to chop this at 28 characters, landing mid-"Rec01" so
        // the row read as a recording called "Rec00".
        XCTAssertEqual(
            compactRecordingLabel("Merged-2026Apr10-130151-Rec07-to-2026Apr10-130731-Rec09.mp3"),
            "Rec07→Rec09"
        )
    }

    func testMergedSplitPartsDropPartSuffixAndCollapseWhenRecombined() {
        // Recombining a split recording's own two halves is just "Rec01" again.
        // "-Part-N" exists only to tell the halves apart when shown on their
        // own; stripping it inside a merge span makes the two ends equal, so
        // the existing collapse-when-equal behaviour kicks in instead of
        // showing a redundant "Rec01→Rec01".
        XCTAssertEqual(
            compactRecordingLabel(
                "Merged-2026Jul31-175620-Rec01-Part-1-to-2026Jul31-175620-Rec01-Part-2.mp3"
            ),
            "Rec01"
        )
    }

    func testMergeEndingOnASplitPartDropsOnlyItsSuffix() {
        // A merge spanning a whole recording and a different recording's
        // split part — "-Part-2" is dropped, but "Rec00" and "Rec01" stay
        // distinct, so the arrow (and the span it communicates) survives.
        XCTAssertEqual(
            compactRecordingLabel(
                "Merged-2026Jul31-174035-Rec00-to-2026Jul31-175620-Rec01-Part-2.mp3"
            ),
            "Rec00→Rec01"
        )
    }

    func testMergedOutputNeverShowsARecNumberItDoesNotCover() {
        let label = compactRecordingLabel(
            "Merged-2026Jul31-175620-Rec01-Part-1-to-2026Jul31-175620-Rec01-Part-2.mp3"
        )
        XCTAssertFalse(label.contains("Rec00"))
    }

    func testMergeOfOneRecordingWithItselfCollapsesToOneLabel() {
        XCTAssertEqual(
            compactRecordingLabel("Merged-2026Apr10-130151-Rec07-to-2026Apr10-130151-Rec07.mp3"),
            "Rec07"
        )
    }
}

/// EventKit caps this app at four attendees, so a larger meeting's list has to be
/// fetched from the MCP. That reply gets a strict contract, unlike the prose parser
/// used for event discovery.
final class EnrichedAttendeeParsingTests: XCTestCase {

    func testParsesAPlainJSONObject() {
        let reply = #"{"attendees": ["Jeff Chow", "Ellen Barss", "Ian Reay"]}"#
        XCTAssertEqual(parseEnrichedAttendees(reply), ["Ellen Barss", "Ian Reay", "Jeff Chow"])
    }

    func testToleratesAFencedBlockAndSurroundingChatter() {
        let reply = """
        Here you go:
        ```json
        {"attendees": ["Chris Wildsmith", "James Whiting"]}
        ```
        """
        XCTAssertEqual(parseEnrichedAttendees(reply), ["Chris Wildsmith", "James Whiting"])
    }

    func testDeduplicatesCaseInsensitivelyAndTrims() {
        let reply = #"{"attendees": ["  Ian Reay ", "ian reay", "Jeff Chow"]}"#
        XCTAssertEqual(parseEnrichedAttendees(reply), ["Ian Reay", "Jeff Chow"])
    }

    func testUnavailablePlaceholderIsDropped() {
        let reply = #"{"attendees": ["unavailable", "Ellen Barss"]}"#
        XCTAssertEqual(parseEnrichedAttendees(reply), ["Ellen Barss"])
    }

    func testProseIsRejectedRatherThanGuessedAt() {
        // The discovery parser's failure mode was promoting prose to data. This one
        // must fail closed so the caller keeps EventKit's list.
        XCTAssertNil(parseEnrichedAttendees("No event overlaps that window."))
        XCTAssertNil(parseEnrichedAttendees("Invitees: Jeff Chow; Ellen Barss"))
    }

    func testEmptyListIsAFailureNotAReplacement() {
        // A valid "nobody listed" must not silently wipe a list we already have.
        XCTAssertNil(parseEnrichedAttendees(#"{"attendees": []}"#))
        XCTAssertNil(parseEnrichedAttendees(#"{"other": ["x"]}"#))
    }
}

/// The calendar assistant used to be a dead end: it would identify the right
/// meeting in prose and there was no way to act on it. A `CANDIDATE:` line makes
/// the answer actionable, and this is the parser that decides which meeting a
/// recording — and therefore its speaker names — gets attached to. It is
/// deliberately strict: a malformed line is dropped, never guessed at.
final class CalendarAssistantCandidateParsingTests: XCTestCase {

    private func iso(_ text: String) -> Date {
        ISO8601DateFormatter().date(from: text)!
    }

    func testParsesAWellFormedCandidate() {
        let reply = """
        Found it — the Zonal weekly sync at 13:30.
        CANDIDATE: Zonal weekly sync | 2026-08-04T13:30:00Z | 2026-08-04T14:00:00Z | Joe Kraft, Ian Reay
        """
        let found = AppDelegate.calendarCandidates(inAssistantReply: reply)
        XCTAssertEqual(found.count, 1)
        XCTAssertEqual(found[0].title, "Zonal weekly sync")
        XCTAssertEqual(found[0].start, iso("2026-08-04T13:30:00Z"))
        XCTAssertEqual(found[0].attendeeNames, ["Joe Kraft", "Ian Reay"])
    }

    func testTheCandidateLinesAreStrippedFromTheProse() {
        let reply = """
        Found it — the Zonal weekly sync.
        CANDIDATE: Zonal weekly sync | 2026-08-04T13:30:00Z | 2026-08-04T14:00:00Z | Joe Kraft
        """
        let shown = AppDelegate.strippingCandidateLines(reply)
        XCTAssertEqual(shown, "Found it — the Zonal weekly sync.")
        XCTAssertFalse(shown.contains("CANDIDATE"))
    }

    func testSeveralCandidatesAreAllOffered() {
        let reply = """
        Two possibilities.
        CANDIDATE: Zonal weekly sync | 2026-08-04T13:30:00Z | 2026-08-04T14:00:00Z | Joe Kraft
        CANDIDATE: AI accelerator | 2026-08-04T14:30:00Z | 2026-08-04T15:30:00Z | Janni Zesach
        """
        XCTAssertEqual(AppDelegate.calendarCandidates(inAssistantReply: reply).count, 2)
    }

    func testAttendeesAreOptional() {
        let reply = "CANDIDATE: Standup | 2026-08-04T09:00:00Z | 2026-08-04T09:15:00Z |"
        let found = AppDelegate.calendarCandidates(inAssistantReply: reply)
        XCTAssertEqual(found.count, 1)
        XCTAssertTrue(found[0].attendeeNames.isEmpty)
    }

    func testDuplicateLinesCollapse() {
        let line = "CANDIDATE: Standup | 2026-08-04T09:00:00Z | 2026-08-04T09:15:00Z | A"
        XCTAssertEqual(AppDelegate.calendarCandidates(inAssistantReply: "\(line)\n\(line)").count, 1)
    }

    // --- the refusals: a wrong candidate attaches the wrong meeting ---

    func testProseAloneOffersNothing() {
        let reply = "It was probably the Zonal weekly sync at 13:30, organised by Joe Kraft."
        XCTAssertTrue(AppDelegate.calendarCandidates(inAssistantReply: reply).isEmpty)
    }

    func testAnUnparseableDateIsDropped() {
        let reply = "CANDIDATE: Zonal weekly sync | today at 1330 | later | Joe Kraft"
        XCTAssertTrue(AppDelegate.calendarCandidates(inAssistantReply: reply).isEmpty)
    }

    func testTooFewFieldsIsDropped() {
        let reply = "CANDIDATE: Zonal weekly sync | 2026-08-04T13:30:00Z"
        XCTAssertTrue(AppDelegate.calendarCandidates(inAssistantReply: reply).isEmpty)
    }

    func testAnEndBeforeItsStartIsDropped() {
        let reply = "CANDIDATE: Backwards | 2026-08-04T14:00:00Z | 2026-08-04T13:30:00Z | A"
        XCTAssertTrue(AppDelegate.calendarCandidates(inAssistantReply: reply).isEmpty)
    }

    func testAnEmptyTitleIsDropped() {
        let reply = "CANDIDATE:  | 2026-08-04T13:30:00Z | 2026-08-04T14:00:00Z | A"
        XCTAssertTrue(AppDelegate.calendarCandidates(inAssistantReply: reply).isEmpty)
    }

    func testCommentaryMasqueradingAsATitleIsDropped() {
        // The same guard the structured search needs: a title is a name, not a
        // sentence explaining what the model did.
        let reply = "CANDIDATE: I searched your calendar and found that the meeting "
            + "you are looking for is probably the one at half past one | "
            + "2026-08-04T13:30:00Z | 2026-08-04T14:00:00Z | A"
        XCTAssertTrue(AppDelegate.calendarCandidates(inAssistantReply: reply).isEmpty)
    }

    func testStrippingLeavesAProseOnlyReplyUntouched() {
        let reply = "I couldn't find anything matching that time."
        XCTAssertEqual(AppDelegate.strippingCandidateLines(reply), reply)
    }
}

extension CalendarAssistantCandidateParsingTests {

    func testAPartialCandidateMarkerIsHiddenWhileStreaming() {
        // Deltas arrive a few characters at a time; the first observed was "C".
        for partial in ["C", "CAN", "CANDIDATE", "CANDIDATE:"] {
            let shown = AppDelegate.strippingCandidateLines(
                "Found it — the Zonal weekly sync.\n\(partial)", streaming: true
            )
            XCTAssertEqual(shown, "Found it — the Zonal weekly sync.",
                           "partial marker \(partial) leaked into the panel")
        }
    }

    func testRealProseIsNotMistakenForAPartialMarker() {
        let shown = AppDelegate.strippingCandidateLines(
            "Found it.\nCan you confirm the time?", streaming: true
        )
        XCTAssertTrue(shown.contains("Can you confirm the time?"))
    }
}
