import XCTest

@testable import S2MConsole

/// The words, times and figures the redesigned panes show.
final class WordingTests: XCTestCase {
    private let taipei = TimeZone(secondsFromGMT: 8 * 3600)!

    private func run(_ report: String, id: String = "r1", started: String = "2026-11-14T05:53:27+00:00")
        throws -> RunRecord
    {
        let json = """
            {"run_id": "\(id)", "started_at": "\(started)", "finished_at": null, "report": \(report)}
            """
        return try JSONDecoder().decode(RunRecord.self, from: Data(json.utf8))
    }

    func testStampsAreShownInLocalTime() {
        XCTAssertEqual(localTime("2026-11-14T05:53:27+00:00", zone: taipei), "11/14 13:53")
        XCTAssertEqual(localTime("2026-11-14T05:53:27.123456+00:00", zone: taipei), "11/14 13:53")
        XCTAssertEqual(localTime("2026-11-13T18:17:00+00:00", zone: taipei), "11/14 02:17")
        XCTAssertEqual(localDay("2026-11-13T18:17:00+00:00", zone: taipei), "11 月 14 日")
        XCTAssertEqual(localClock("2026-11-14T05:53:27+00:00", zone: taipei), "13:53")
    }

    func testAStampThatCannotBeReadIsShownAsItCame() {
        XCTAssertEqual(localTime(nil, zone: taipei), "–")
        XCTAssertEqual(localTime("yesterday", zone: taipei), "yesterday")
    }

    func testNumbersAreGrouped() {
        XCTAssertEqual(grouped(7039), "7,039")
        XCTAssertEqual(grouped(82), "82")
    }

    func testABatchSaysHowManyAndWhatFailed() {
        XCTAssertEqual(BatchOutcome(done: 3).message("已停用", unit: "條"), "已停用 3 條。")
        XCTAssertEqual(
            BatchOutcome(done: 2, failures: ["m_1 is retired", "x"]).message("已停用", unit: "條"),
            "已停用 2 條，2 條沒有成功：m_1 is retired")
        XCTAssertTrue(
            ActionNote(BatchOutcome(done: 1, failures: ["x"]), verb: "已略過", unit: "段").failed)
    }

    func testWordsTheCommandLineDoesNotKnowAreShownAsTheyAre() {
        XCTAssertEqual(Lexicon.kind("pitfall"), "地雷")
        XCTAssertEqual(Lexicon.kind("ritual"), "ritual")
        XCTAssertEqual(Lexicon.status("superseded"), "已改寫")
        XCTAssertEqual(Lexicon.tool("claude"), "Claude Code")
        XCTAssertEqual(Lexicon.tool(nil), "未知工具")
        XCTAssertEqual(Lexicon.route("page_tree"), "文件目錄")
    }

    func testWhyASessionIsPassedOverIsSaidInWords() {
        XCTAssertEqual(Lexicon.blocked("it has no digest to read"), "沒有可讀的內容")
        XCTAssertEqual(Lexicon.blocked("its workspace is unknown"), "不知道屬於哪個專案")
        XCTAssertEqual(
            Lexicon.blocked("it is one short exchange, too little to distil"), "只有一來一往，內容太少")
        XCTAssertEqual(Lexicon.blocked("something new"), "something new")
    }

    func testTheChartScaleFollowsTheSecondLargestWhenOneRunDwarfsTheRest() {
        XCTAssertEqual(barScale([6558, 11, 61, 105]), 105)
        XCTAssertEqual(barScale([105, 94, 82]), 105)
        XCTAssertEqual(barScale([40]), 40)
        XCTAssertEqual(barScale([]), 1)
        XCTAssertEqual(barScale([0, 0]), 1)
    }

    func testTheChartShowsTheLatestRunsThatReadSessionsOldestFirst() throws {
        let runs = [
            try run(#"{"scan": {"sessions_new": 82}}"#, id: "c"),
            try run(#"{"reconcile": {"llm_calls": 5}}"#, id: "skip"),
            try run(#"{"scan": {"sessions_new": 94}}"#, id: "b"),
            try run(#"{"scan": {"sessions_new": 105}}"#, id: "a"),
        ]
        XCTAssertEqual(runBars(runs).map(\.id), ["a", "b", "c"])
        XCTAssertEqual(runBars(runs, limit: 2).map(\.count), [94, 82])
    }

    func testARunIsToldByWhatItDid() throws {
        let full = RunStory(
            run: try run(
                """
                {"scan": {"sessions_new": 82, "sessions_updated": 9, "seconds": 4.6},
                 "curate": {"llm_calls": 17, "llm_failures": 0, "cost_usd": 0.0249,
                            "memories_added": 46, "memories_confirmed": 0,
                            "memories_superseded": 0, "memories_retired": 0,
                            "sessions_curated": 45, "sessions_left": 0, "seconds": 150},
                 "reconcile": {"llm_calls": 5, "cost_usd": 0.0018, "projects_checked": 5,
                               "projects_left": 7, "memories_merged": 0, "memories_retired": 0,
                               "seconds": 22}}
                """))
        XCTAssertEqual(full.headline, "讀了 82 段新對話，新增 46 條記憶")
        XCTAssertEqual(full.modelCalls, 22)
        XCTAssertEqual(full.seconds, 177)
        XCTAssertEqual(full.cost, 0.0267, accuracy: 0.00001)
        XCTAssertEqual(full.sentences.count, 3)
        XCTAssertTrue(full.sentences[2].hasSuffix("還有 7 個專案排在後面。"))
        XCTAssertFalse(full.hasError)

        let reconcile = RunStory(run: try run(#"{"reconcile": {"llm_calls": 52}}"#))
        XCTAssertEqual(reconcile.headline, "檢查重複與矛盾的記憶")

        let trial = RunStory(run: try run(#"{"dry_run": true, "scan": {"sessions_new": 3}}"#))
        XCTAssertEqual(trial.headline, "試跑（不寫入）：讀了 3 段新對話")

        let broken = RunStory(run: try run(#"{"embed": {"error": "no model"}}"#))
        XCTAssertTrue(broken.hasError)
        XCTAssertEqual(broken.headline, "更新搜尋用的索引")
    }

    func testAnAnswerSaysHowSureItIsAndWhichWayFoundIt() throws {
        XCTAssertEqual(confidenceLabel(0.9).text, "把握高")
        XCTAssertEqual(confidenceLabel(0.6).text, "把握普通")
        XCTAssertEqual(confidenceLabel(0.34).text, "把握不高")

        let json = """
            {"answer": "a", "source": ["vector"], "confidence": 0.34,
             "trace": {"route": "vector", "steps": [
                {"kind": "routing_model", "detail": {}},
                {"kind": "wiki_lookup", "detail": {"hit": false}},
                {"kind": "vector_lookup", "detail": {}},
                {"kind": "lexical_lookup", "detail": {"hit": true}},
                {"kind": "merge", "detail": {"strategy": "rrf"}}]}}
            """
        let response = try JSONDecoder().decode(QueryResponse.self, from: Data(json.utf8))
        XCTAssertEqual(lookups(response).map(\.route), ["wiki", "vector", "lexical"])
        XCTAssertEqual(lookups(response).map(\.note), ["沒找到", "採用這個", "有找到，沒採用"])
    }
}
