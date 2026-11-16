import XCTest

@testable import S2MConsole

final class MemoryOrganizationTests: XCTestCase {
    private func memory(_ id: String, project: String = "/work/one", topic: String? = nil,
                        kind: String = "constraint") throws -> Memory {
        var data: [String: Any] = [
            "id": id, "project": project, "scope": "coding", "kind": kind, "text": "保留來源",
            "status": "active", "created_at": "2026-11-15", "last_confirmed_at": "2026-11-16",
            "evidence": ["codex:original"],
        ]
        if let topic { data["topic"] = ["id": topic, "label": topic] }
        return try JSONDecoder().decode(Memory.self, from: JSONSerialization.data(withJSONObject: data))
    }

    func testOlderCLIStillDecodesAndAppearsUnderGeneralRules() throws {
        let old = try memory("old")
        XCTAssertNil(old.topic)
        let grouped = MemoryOrganization.groups([old])
        XCTAssertEqual(grouped.map(\.id), ["general"])
        XCTAssertEqual(grouped[0].memories[0].evidence, ["codex:original"])
    }

    func testGroupingKeepsIdsProjectsAndOrderWithinATopic() throws {
        let entries = try [memory("a", topic: "audio"), memory("b", project: "/work/two", topic: "audio"),
                           memory("c", topic: "visual"), memory("d")]
        let grouped = MemoryOrganization.groups(entries)
        XCTAssertEqual(Set(grouped.flatMap(\.memories).map(\.id)), Set(entries.map(\.id)))
        let audio = try XCTUnwrap(grouped.first { $0.id == "audio" })
        XCTAssertEqual(audio.memories.map(\.id), ["a", "b"])
        XCTAssertEqual(audio.memories.map(\.project), ["/work/one", "/work/two"])
        XCTAssertEqual(grouped.last?.id, "general")
    }

    func testKindAndTopicFiltersIntersectWithoutChangingTimeOrder() throws {
        let entries = try [memory("a", topic: "audio"), memory("b", topic: "visual"),
                           memory("c", topic: "audio", kind: "procedure")]
        XCTAssertEqual(MemoryOrganization.filter(entries, kind: "", topic: "").map(\.id), ["a", "b", "c"])
        XCTAssertEqual(MemoryOrganization.filter(entries, kind: "constraint", topic: "audio").map(\.id), ["a"])
        XCTAssertEqual(MemoryOrganization.filter(entries, kind: "", topic: "audio").map(\.id), ["a", "c"])
        XCTAssertTrue(MemoryOrganization.filter(entries, kind: "fact", topic: "visual").isEmpty)
    }

    func testBothViewsGroupByWhatTheMemoryIsFiledUnder() throws {
        let entries = try [memory("a", topic: "t_albedo"), memory("b", topic: "t_tuji"),
                           memory("c", topic: "t_albedo"), memory("d")]
        for lens in MemoryLens.allCases {
            let grouped = MemoryOrganization.groups(entries, by: lens)
            XCTAssertEqual(grouped.map(\.id), ["t_albedo", "t_tuji", "general"])
            XCTAssertEqual(grouped[0].memories.map(\.id), ["a", "c"])
            XCTAssertEqual(
                MemoryOrganization.filter(entries, kind: "", topic: "t_tuji", by: lens).map(\.id),
                ["b"])
        }
        XCTAssertEqual(MemoryTopic.general.label, "待歸類")
    }

    func testEachViewListsOneScopeAndSearchesWithinIt() {
        XCTAssertEqual(MemoryLens.topics.scope, "coding")
        XCTAssertEqual(MemoryLens.agents.scope, "openclaw")
        XCTAssertEqual(
            Backend.searchArguments(query: "q", project: nil, status: "active", scope: "openclaw")
                .suffix(2), ["--scope", "openclaw"])
        XCTAssertFalse(
            Backend.searchArguments(query: "q", project: "/p", status: "active").contains("--scope"))
    }

    func testAnAgentIsShownWithItsTeamAndATopicByItsName() throws {
        func topic(_ scope: String, _ about: String?) throws -> Topic {
            var data: [String: Any] = ["id": "t_1", "scope": scope, "name": "albedo", "active": 3]
            if let about { data["description"] = about }
            return try JSONDecoder().decode(
                Topic.self, from: JSONSerialization.data(withJSONObject: data))
        }
        XCTAssertEqual(try topic("openclaw", "video-studio").label, "albedo · video-studio")
        XCTAssertEqual(try topic("openclaw", nil).label, "albedo")
        XCTAssertEqual(try topic("coding", "收什麼").label, "albedo")
        XCTAssertEqual(
            Backend.topicArguments(["topic-edit", "--topic", "t_1"], name: "新名", description: ""),
            ["topic-edit", "--topic", "t_1", "--name", "新名", "--description", ""])
    }
}
