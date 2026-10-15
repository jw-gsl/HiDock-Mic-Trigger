import XCTest
@testable import hidock_mic_trigger

/// Clicking "N to tag" shows exactly the recordings that count; older ones
/// before the tagging cutoff stay in the full list but not in this view.
final class ToTagFilterTests: XCTestCase {
    private var savedCutoff: Any?
    private var savedHidden: Any?

    override func setUp() {
        // The view model persists these; keep the real app's settings intact.
        savedCutoff = UserDefaults.standard.object(forKey: "taggingIgnoreBefore")
        savedHidden = UserDefaults.standard.object(forKey: "hidockHiddenStatuses")
        UserDefaults.standard.removeObject(forKey: "hidockHiddenStatuses")
    }

    override func tearDown() {
        UserDefaults.standard.set(savedCutoff, forKey: "taggingIgnoreBefore")
        UserDefaults.standard.set(savedHidden, forKey: "hidockHiddenStatuses")
    }

    private func entry(_ name: String, date: String, transcribed: Bool = true,
                       tagged: Bool = false) -> HiDockSyncRecordingEntry {
        let recording = HiDockSyncRecording(
            name: name, createDate: date, createTime: "12:00:00",
            length: 1000, duration: 10.0, version: 1, mode: "normal",
            signature: name, outputPath: "/tmp/\(name).mp3", outputName: "\(name).mp3",
            downloaded: true, localExists: true, downloadedAt: nil,
            lastError: nil, status: "downloaded", humanLength: "1 KB",
            trimmed: nil, durationEstimated: nil, removed: nil
        )
        var e = HiDockSyncRecordingEntry(recording: recording, deviceProductId: 45068,
                                         deviceId: "hidock:45068", deviceName: "H1")
        e.transcribed = transcribed
        e.speakersTagged = tagged
        return e
    }

    private func makeModel() -> HiDockViewModel {
        let model = HiDockViewModel()
        model.syncEntries = [
            entry("new-untagged", date: "2026/10/10"),
            entry("new-tagged", date: "2026/10/11", tagged: true),
            entry("old-untagged", date: "2026/01/05"),
            entry("not-transcribed", date: "2026/10/12", transcribed: false),
        ]
        var cutoff = DateComponents()
        cutoff.year = 2026; cutoff.month = 9; cutoff.day = 1
        model.taggingIgnoreBefore = Calendar.current.date(from: cutoff)
        return model
    }

    func testCountAndFilterAgreeAndRespectCutoff() {
        let model = makeModel()
        XCTAssertEqual(model.needsTaggingCount, 1)
        XCTAssertEqual(model.visibleEntries.count, 4, "the cutoff never hides rows")

        model.toggleToTagFilter()
        XCTAssertEqual(model.visibleEntries.map(\.recording.name), ["new-untagged"])

        model.toggleToTagFilter()
        XCTAssertEqual(model.visibleEntries.count, 4)
    }

    func testClickingClearsOtherFiltersAndBeatsHide() {
        let model = makeModel()
        model.statusFilters = [.untranscribed]
        model.hiddenStatuses = [model.syncEntries[0].statusText]
        model.toggleToTagFilter()
        XCTAssertEqual(model.statusFilters, [.needsTagging])
        XCTAssertEqual(model.visibleEntries.map(\.recording.name), ["new-untagged"])
    }

    func testTaggingARowRemovesItFromTheView() {
        let model = makeModel()
        model.toggleToTagFilter()
        model.syncEntries[0].speakersTagged = true
        XCTAssertTrue(model.visibleEntries.isEmpty)
    }
}
