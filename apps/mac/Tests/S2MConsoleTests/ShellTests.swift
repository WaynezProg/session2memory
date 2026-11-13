import XCTest

@testable import S2MConsole

final class ShellTests: XCTestCase {
    func testQuoteWrapsInSingleQuotes() {
        XCTAssertEqual(Shell.quote("plain"), "'plain'")
        XCTAssertEqual(Shell.quote(""), "''")
        XCTAssertEqual(Shell.quote("two words $HOME `id`"), "'two words $HOME `id`'")
    }

    func testQuoteEscapesSingleQuote() {
        XCTAssertEqual(Shell.quote("it's"), "'it'\\''s'")
    }

    /// What zsh hands to the command is the value itself, whatever it holds.
    func testQuotedValueSurvivesTheShell() async throws {
        let value = "a 'b' \"c\" $HOME; echo x \\ 中文"
        let result = try await Shell.run("printf %s " + Shell.quote(value), directory: "")
        XCTAssertEqual(result.status, 0)
        XCTAssertEqual(result.output, value)
    }
}

final class RepositoryFinderTests: XCTestCase {
    func testFindsTheCheckoutAboveAStartingFolder() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("s2m-finder-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: root) }
        let s2m = root.appendingPathComponent("checkout")
        let deep = s2m.appendingPathComponent("apps/mac/.build/debug")
        let hks = root.appendingPathComponent("Hybrid-Knowledge-System")
        for folder in [
            s2m.appendingPathComponent("src/session2memory"), deep,
            hks.appendingPathComponent("src/hks"),
        ] {
            try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        }

        let found = try XCTUnwrap(RepositoryFinder.session2memory(from: [deep.path]))
        XCTAssertEqual(
            URL(fileURLWithPath: found).resolvingSymlinksInPath(), s2m.resolvingSymlinksInPath())
        XCTAssertEqual(
            RepositoryFinder.hks(beside: found).map {
                URL(fileURLWithPath: $0).resolvingSymlinksInPath()
            }, hks.resolvingSymlinksInPath())
        XCTAssertNil(RepositoryFinder.session2memory(from: [root.path]))
        XCTAssertNil(RepositoryFinder.hks(beside: root.appendingPathComponent("alone/checkout").path))
    }
}
