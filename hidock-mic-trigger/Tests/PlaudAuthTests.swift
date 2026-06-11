import XCTest
@testable import hidock_mic_trigger

/// Tests for the Plaud token-refresh persistence logic. The extractor refreshes
/// the short-lived `pld_ut` user token and reports the rotated tokens in its
/// JSON `refreshedTokens` field; `PlaudSession.applyingRefreshedTokens` folds
/// those back into the stored session so the next sync uses a fresh token
/// instead of a stale one (which makes the Plaud cloud return an empty list).
final class PlaudRefreshTokenTests: XCTestCase {

    private func existing() -> PlaudSession {
        PlaudSession(
            accountId: "google-123",
            email: "u@example.com",
            displayName: "Test User",
            region: "us",
            accessToken: "OLD_ACCESS",
            refreshToken: "OLD_REFRESH"
        )
    }

    private func json(_ obj: [String: Any]) -> Data {
        try! JSONSerialization.data(withJSONObject: obj)
    }

    func testAppliesRotatedAccessAndRefresh() {
        let data = json(["recordings": [], "refreshedTokens": [
            "accessToken": "NEW_ACCESS", "refreshToken": "NEW_REFRESH", "region": "eu"
        ]])
        let updated = PlaudSession.applyingRefreshedTokens(data, to: existing())
        XCTAssertNotNil(updated)
        XCTAssertEqual(updated?.accessToken, "NEW_ACCESS")
        XCTAssertEqual(updated?.refreshToken, "NEW_REFRESH")
        XCTAssertEqual(updated?.region, "eu")
        // Identity fields are preserved from the existing session.
        XCTAssertEqual(updated?.accountId, "google-123")
        XCTAssertEqual(updated?.email, "u@example.com")
        XCTAssertEqual(updated?.displayName, "Test User")
    }

    func testKeepsExistingRefreshAndRegionWhenNotRotated() {
        // Plaud may issue a new user token without rotating the refresh token
        // or changing region — keep the existing values rather than nil them.
        let data = json(["refreshedTokens": ["accessToken": "NEW_ACCESS"]])
        let updated = PlaudSession.applyingRefreshedTokens(data, to: existing())
        XCTAssertEqual(updated?.accessToken, "NEW_ACCESS")
        XCTAssertEqual(updated?.refreshToken, "OLD_REFRESH")
        XCTAssertEqual(updated?.region, "us")
    }

    func testNilWhenNoRefreshedTokensField() {
        XCTAssertNil(PlaudSession.applyingRefreshedTokens(json(["recordings": []]), to: existing()))
    }

    func testNilWhenAccessTokenUnchanged() {
        // No-op: the extractor reported the same token we already hold.
        let data = json(["refreshedTokens": ["accessToken": "OLD_ACCESS"]])
        XCTAssertNil(PlaudSession.applyingRefreshedTokens(data, to: existing()))
    }

    func testNilWhenAccessTokenEmptyOrMissing() {
        XCTAssertNil(PlaudSession.applyingRefreshedTokens(json(["refreshedTokens": ["accessToken": ""]]), to: existing()))
        XCTAssertNil(PlaudSession.applyingRefreshedTokens(json(["refreshedTokens": ["region": "eu"]]), to: existing()))
    }

    func testNilOnMalformedJSON() {
        XCTAssertNil(PlaudSession.applyingRefreshedTokens(Data("not json".utf8), to: existing()))
    }
}
