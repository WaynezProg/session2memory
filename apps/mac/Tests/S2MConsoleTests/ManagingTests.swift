import XCTest

@testable import S2MConsole

final class ManagingTests: XCTestCase {
    private func project(_ path: String, active: Int = 1, retired: Int = 0) -> Project {
        Project(
            project: path, name: (path as NSString).lastPathComponent, active: active,
            superseded: 0, retired: retired, stale: 0, sessions: 1, sessionsPending: 0,
            lastActivity: nil)
    }

    func testWorkspacesWithoutMemoryAreLeftOutUnlessAskedFor() {
        let all = [
            project("/work/a"), project("/work/none", active: 0),
            project("/work/old", active: 0, retired: 2), project("/work/chosen", active: 0),
        ]
        func listed(_ withoutMemory: Bool, _ chosen: String? = nil) -> [String] {
            workspacesToList(all, filter: "", withoutMemory: withoutMemory, keeping: chosen)
                .map(\.name)
        }

        XCTAssertEqual(listed(false), ["a", "old"], "retired memory is memory too")
        XCTAssertEqual(listed(false, "/work/chosen"), ["a", "old", "chosen"])
        XCTAssertEqual(listed(true), ["a", "none", "old", "chosen"])
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
            ["search", "--query", "匯出 Parquet", "--status", "active", "--limit", "500"])
        XCTAssertEqual(
            Backend.searchArguments(query: "x", project: "/work/a", status: "all"),
            ["search", "--query", "x", "--status", "all", "--limit", "500", "--project", "/work/a"])
    }

    func testSessionFiltersAreOnlySentWhenSet() {
        XCTAssertEqual(
            Backend.sessionsArguments(
                project: "", tool: nil, query: "  ", pendingOnly: false, limit: 300),
            ["sessions", "--limit", "300"])
        XCTAssertEqual(
            Backend.sessionsArguments(
                project: "/work/a", tool: "cursor", scope: "coding", query: " hook ",
                pendingOnly: true, limit: 600),
            [
                "sessions", "--limit", "600", "--project", "/work/a", "--tool", "cursor",
                "--scope", "coding", "--query", "hook", "--pending",
            ])
    }

    func testMemoryTextIsCountedAsItWillBeStored() {
        XCTAssertEqual(tidyText("  第一行\n  第二行\t結尾 "), "第一行 第二行 結尾")
        XCTAssertEqual(tidyText(" \n "), "")
        XCTAssertEqual(tidyText(String(repeating: "長", count: 241)).count, memoryTextLimit + 1)
    }

    func testAChangeThatPartlyFailedIsShownAsAFailure() {
        let fine = ActionNote(BatchOutcome(done: 2), verb: "Retired")
        let partly = ActionNote(BatchOutcome(done: 1, failures: ["m_2 is retired"]), verb: "Retired")

        XCTAssertEqual(fine, ActionNote(text: "Retired 2.", failed: false))
        XCTAssertEqual(partly, ActionNote(text: "Retired 1, 1 failed: m_2 is retired", failed: true))
    }

    func testASessionSaysWhyTheRunWouldPassOverIt() throws {
        let json = """
            {"key": "codex:s1", "tool": "codex", "pending": false, "digest": "U: hi", "memories": [],
             "curation": {"blocked": ["it is one short exchange, too little to distil"], "note": null}}
            """
        let detail = try JSONDecoder().decode(SessionDetail.self, from: Data(json.utf8))
        XCTAssertEqual(detail.curation?.blocked.count, 1)
        XCTAssertNil(detail.curation?.note)

        // An older command line does not send the field.
        let older = try JSONDecoder().decode(
            SessionDetail.self, from: Data(#"{"key": "codex:s1", "memories": []}"#.utf8))
        XCTAssertNil(older.curation)
    }

    func testAWorkspaceShowsTheFolderItLiesIn() {
        XCTAssertEqual(parentFolder(of: "/Users/me/work/app", home: "/Users/me"), "~/work")
        XCTAssertEqual(parentFolder(of: "/Users/me/app", home: "/Users/me"), "~")
        XCTAssertEqual(parentFolder(of: "/opt/tools/app", home: "/Users/me"), "/opt/tools")
        XCTAssertEqual(parentFolder(of: "/Users/meadow/app", home: "/Users/me"), "/Users/meadow")
    }

    func testTheCommandIsFoundWithoutAsking() {
        func choose(
            command: String = "", directory: String = "", checkout: String? = nil,
            installed: Bool = false
        ) -> CommandChoice? {
            CommandFinder.choose(
                savedCommand: command, savedDirectory: directory, name: "session2memory",
                checkout: checkout, installed: installed, home: "/Users/me")
        }

        XCTAssertNil(choose(), "nothing typed, no checkout, not installed")
        XCTAssertEqual(
            choose(installed: true),
            CommandChoice(command: "session2memory", directory: "/Users/me", source: .installed))
        XCTAssertEqual(
            choose(checkout: "/work/s2m", installed: true),
            CommandChoice(
                command: "uv run session2memory", directory: "/work/s2m", source: .checkout),
            "the checkout the app belongs to comes before the installed command")
        XCTAssertEqual(
            choose(directory: "/elsewhere", checkout: "/work/s2m", installed: true),
            CommandChoice(
                command: "uv run session2memory", directory: "/elsewhere", source: .settings),
            "a folder typed in Settings wins")
        XCTAssertEqual(
            choose(command: "s2m-dev"),
            CommandChoice(command: "s2m-dev", directory: "/Users/me", source: .settings))
    }

    func testOnlyAPlainNameIsLookedUpInTheShell() async {
        let odd = await CommandFinder.isInstalled("zsh; echo hi")
        let real = await CommandFinder.isInstalled("zsh")
        let none = await CommandFinder.isInstalled("no-such-command-s2m")
        XCTAssertFalse(odd)
        XCTAssertTrue(real)
        XCTAssertFalse(none)
    }
}
