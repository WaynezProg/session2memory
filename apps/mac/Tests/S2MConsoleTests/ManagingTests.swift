import XCTest

@testable import S2MConsole

final class ManagingTests: XCTestCase {
    private func project(_ path: String) -> Project {
        Project(
            project: path, name: (path as NSString).lastPathComponent, active: 1, superseded: 0,
            retired: 0, stale: 0, sessions: 1, sessionsPending: 0, lastActivity: nil)
    }

    func testWorkspacesAreFilteredByEveryWordOfThePath() {
        let all = [project("/work/Alpha-App"), project("/work/beta"), project("/home/alpha")]

        XCTAssertEqual(filterProjects(all, by: "").map(\.project), all.map(\.project))
        XCTAssertEqual(
            filterProjects(all, by: "ALPHA").map(\.project), ["/work/Alpha-App", "/home/alpha"])
        XCTAssertEqual(filterProjects(all, by: "work alpha").map(\.project), ["/work/Alpha-App"])
        XCTAssertTrue(filterProjects(all, by: "gamma").isEmpty)
    }

    func testABatchSaysWhatWasDoneAndWhatFailed() {
        XCTAssertEqual(BatchOutcome(done: 3).summary(verb: "Retired"), "Retired 3.")
        XCTAssertEqual(
            BatchOutcome(done: 2, failures: ["m_1 is retired", "x"]).summary(verb: "Retired"),
            "Retired 2, 2 failed: m_1 is retired")
    }

    func testSearchIsAskedInOneWorkspaceOrInAll() {
        XCTAssertEqual(
            Backend.searchArguments(query: "匯出 Parquet", project: nil, status: "active"),
            ["search", "--query", "匯出 Parquet", "--status", "active"])
        XCTAssertEqual(
            Backend.searchArguments(query: "x", project: "/work/a", status: "all"),
            ["search", "--query", "x", "--status", "all", "--project", "/work/a"])
    }

    func testSessionFiltersAreOnlySentWhenSet() {
        XCTAssertEqual(
            Backend.sessionsArguments(
                project: "", tool: nil, query: "  ", pendingOnly: false, limit: 300),
            ["sessions", "--limit", "300"])
        XCTAssertEqual(
            Backend.sessionsArguments(
                project: "/work/a", tool: "cursor", query: " hook ", pendingOnly: true, limit: 600),
            [
                "sessions", "--limit", "600", "--project", "/work/a", "--tool", "cursor",
                "--query", "hook", "--pending",
            ])
    }
}
