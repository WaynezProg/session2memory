import XCTest

@testable import S2MConsole

// Samples in the shape `session2memory auto console <view>` and `ks query` print.

final class DecodingTests: XCTestCase {
    private func console<T: Decodable>(_ type: T.Type, _ json: String) throws -> T {
        let envelope = try JSONDecoder().decode(Envelope<T>.self, from: Data(json.utf8))
        XCTAssertTrue(envelope.ok)
        return try XCTUnwrap(envelope.data)
    }

    private let session = """
        {"key": "codex:abc", "tool": "codex", "session_id": "abc", "agent": "codex",
         "scope": "project", "project": "/work/demo", "title": null, "summary": null,
         "summary_origin": null, "last_activity": "2026-11-13T05:00:00+00:00", "pending": true}
        """

    private let memory = """
        {"id": "m2", "project": "/work/demo", "scope": "project", "kind": "decision",
         "text": "Use uv.", "status": "active", "created_at": "2026-11-12T01:00:00+00:00",
         "last_confirmed_at": "2026-11-13T01:00:00+00:00", "supersedes_id": "m1",
         "evidence": ["codex:abc"]
        """

    func testOverview() throws {
        let overview = try console(
            Overview.self,
            """
            {"ok": true, "data": {"store": "/tmp/store",
             "counts": {"sessions": 3, "memories_active": 2},
             "last_run": {"run_id": "r1", "finished_at": "2026-11-13T06:05:59+00:00",
                          "report": {"dry_run": false, "scan": {"files_seen": 3}}}}}
            """)
        XCTAssertEqual(overview.counts["memories_active"], 2)
        XCTAssertEqual(overview.lastRun?.runId, "r1")

        let empty = try console(
            Overview.self,
            #"{"ok": true, "data": {"store": "/tmp/store", "counts": {}, "last_run": null}}"#)
        XCTAssertNil(empty.lastRun)
    }

    func testProjects() throws {
        let projects = try console(
            [Project].self,
            """
            {"ok": true, "data": [
             {"project": "/work/demo", "name": "demo", "active": 2, "superseded": 1,
              "retired": 0, "stale": 0, "sessions": 4, "sessions_pending": 1,
              "last_activity": "2026-11-13T05:00:00+00:00"},
             {"project": "", "name": "", "active": 0, "superseded": 0, "retired": 0,
              "stale": 0, "sessions": 1, "sessions_pending": 0, "last_activity": null}]}
            """)
        XCTAssertEqual(projects.map(\.sessionsPending), [1, 0])
        XCTAssertNil(projects[1].lastActivity)
    }

    func testMemoriesAndMemoryDetail() throws {
        let memories = try console([Memory].self, #"{"ok": true, "data": [\#(memory)}]}"#)
        XCTAssertEqual(memories.first?.supersedesId, "m1")

        let detail = try console(
            MemoryDetail.self,
            """
            {"ok": true, "data": \(memory), "replaced_by": [], "restorable": false,
             "events": [{"op": "add", "at": "2026-11-12T01:00:00+00:00",
                         "detail": {"reason": "first seen", "run_id": "r1"}}],
             "evidence_sessions": [\(session)]}}
            """)
        XCTAssertEqual(detail.events.first?.detail["reason"]?.stringValue, "first seen")
        XCTAssertEqual(detail.evidenceSessions.first?.key, "codex:abc")
        XCTAssertNil(detail.evidenceSessions.first?.title)
    }

    func testSessionsAndSessionDetail() throws {
        let sessions = try console([SessionSummary].self, #"{"ok": true, "data": [\#(session)]}"#)
        XCTAssertEqual(sessions.first?.pending, true)

        let full = session.replacingOccurrences(
            of: #""pending": true}"#,
            with: #""pending": true, "digest": "user: hi", "memories": [\#(memory)}]}"#)
        let detail = try console(SessionDetail.self, #"{"ok": true, "data": \#(full)}"#)
        XCTAssertEqual(detail.digest, "user: hi")
        XCTAssertEqual(detail.memories.count, 1)
    }

    func testRuns() throws {
        let runs = try console(
            [RunRecord].self,
            """
            {"ok": true, "data": [
             {"run_id": "r2", "started_at": "2026-11-13T06:04:15+00:00", "finished_at": null,
              "report": {}},
             {"run_id": "r1", "started_at": "2026-11-12T06:04:15+00:00",
              "finished_at": "2026-11-12T06:05:59+00:00",
              "report": {"dry_run": false, "scan": {"by_tool": {"codex": 57}, "seconds": 74.0},
                         "curate": {"rejections": [], "cost_usd": 0.0061}}}]}
            """)
        XCTAssertNil(runs[0].finishedAt)
        XCTAssertEqual(runs[1].report["scan"]?["by_tool"]?["codex"]?.numberValue, 57)
    }

    func testFailureEnvelope() throws {
        let envelope = try JSONDecoder().decode(
            Envelope<Overview>.self, from: Data(#"{"ok": false, "error": "no store at /x"}"#.utf8))
        XCTAssertFalse(envelope.ok)
        XCTAssertNil(envelope.data)
        XCTAssertEqual(envelope.error, "no store at /x")
    }

    func testQueryHit() throws {
        let response = try JSONDecoder().decode(
            QueryResponse.self,
            from: Data(
                """
                {"answer": "Borealis rewrites checkout.", "source": ["wiki"], "confidence": 1.0,
                 "trace": {"route": "wiki", "steps": [
                   {"kind": "wiki_lookup", "detail": {"slug": "project-borealis", "hit": true}},
                   {"kind": "merge", "detail": {"strategy": "rrf", "candidate_count": 6,
                                                "top_candidate": {"route": "wiki", "score": 1.0}}}]},
                 "evidence": [{"source_relpath": "project-borealis.txt", "route": "wiki",
                               "quote": "Borealis rewrites checkout."}],
                 "retrieval_score": 1.0, "calibrated_confidence": 1.0, "writeback_eligible": false}
                """.utf8))
        XCTAssertEqual(response.source, ["wiki"])
        XCTAssertEqual(response.evidence?.first?.sourceRelpath, "project-borealis.txt")
        XCTAssertNil(response.evidence?.first?.sectionPath)
        XCTAssertEqual(response.trace.steps.first?.detail["hit"], .bool(true))
    }

    func testQueryNoHit() throws {
        let response = try JSONDecoder().decode(
            QueryResponse.self,
            from: Data(
                """
                {"answer": "not found", "source": [], "confidence": 0.0,
                 "trace": {"route": "wiki", "steps": [
                   {"kind": "fallback", "detail": {"status": "no-hit",
                    "reason": "identifier_not_found", "identifiers": ["4012"]}}]},
                 "evidence": null}
                """.utf8))
        XCTAssertTrue(response.source.isEmpty)
        XCTAssertNil(response.evidence)
        XCTAssertEqual(response.trace.steps.first?.detail["status"]?.stringValue, "no-hit")
    }

    func testJSONValueBriefAndPretty() throws {
        let value = try JSONDecoder().decode(
            JSONValue.self, from: Data(#"{"n": 3, "f": 0.25, "s": "x", "a": [1, 2], "z": null}"#.utf8))
        XCTAssertEqual(value["n"]?.brief, "3")
        XCTAssertEqual(value["f"]?.brief, "0.25")
        XCTAssertEqual(value["a"]?.brief, "2 items")
        XCTAssertEqual(value["z"], .null)
        XCTAssertEqual(value.members.map(\.key), ["a", "f", "n", "s", "z"])
        XCTAssertEqual(JSONValue.object(["k": .string("a/b")]).pretty, "{\n  \"k\" : \"a/b\"\n}")
    }

    func testShortTime() {
        XCTAssertEqual(shortTime("2026-11-13T09:28:18+00:00"), "2026-11-13 09:28")
        XCTAssertEqual(shortTime(nil), "–")
        XCTAssertEqual(shortTime(""), "–")
    }
}
