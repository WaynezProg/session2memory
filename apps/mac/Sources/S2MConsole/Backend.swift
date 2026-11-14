import Foundation

struct BackendError: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

/// Keys and defaults of the settings, kept in UserDefaults.
enum SettingKey {
    static let s2mDirectory = "s2mDirectory"
    static let s2mCommand = "s2mCommand"
    static let storeDirectory = "storeDirectory"
    static let hksDirectory = "hksDirectory"
    static let hksCommand = "hksCommand"
    static let ksRoot = "ksRoot"

    static var defaults: [String: Any] {
        let s2m = RepositoryFinder.session2memory()
        return [
            s2mDirectory: s2m ?? "",
            s2mCommand: "uv run session2memory",
            storeDirectory: "",
            hksDirectory: s2m.flatMap(RepositoryFinder.hks(beside:)) ?? "",
            hksCommand: "uv run ks",
            ksRoot: "",
        ]
    }
}

/// Finds the two repositories for the first start, so no personal path is built in.
/// Settings override whatever is found here.
enum RepositoryFinder {
    /// The session2memory checkout this app was built in or started from: the nearest
    /// folder above the executable or the working directory that holds its sources.
    static func session2memory(
        from starts: [String] = [
            Bundle.main.bundlePath, FileManager.default.currentDirectoryPath,
        ]
    ) -> String? {
        for start in starts {
            var url = URL(fileURLWithPath: start).standardizedFileURL
            while url.path != "/" {
                if isDirectory(url.appendingPathComponent("src/session2memory")) {
                    return url.path
                }
                url.deleteLastPathComponent()
            }
        }
        return nil
    }

    /// An HKS checkout next to the session2memory one, under its repository name.
    static func hks(beside s2m: String) -> String? {
        let candidate = URL(fileURLWithPath: s2m)
            .deletingLastPathComponent()
            .appendingPathComponent("Hybrid-Knowledge-System")
        return isDirectory(candidate.appendingPathComponent("src/hks")) ? candidate.path : nil
    }

    private static func isDirectory(_ url: URL) -> Bool {
        var directory: ObjCBool = false
        return FileManager.default.fileExists(atPath: url.path, isDirectory: &directory)
            && directory.boolValue
    }
}

private final class DataBox: @unchecked Sendable {
    var data = Data()
}

struct ShellResult {
    let status: Int32
    let output: String
    let errors: String
}

enum Shell {
    /// Single-quote a value for zsh.
    static func quote(_ value: String) -> String {
        "'" + value.replacingOccurrences(of: "'", with: "'\\''") + "'"
    }

    /// Run a command line in a login shell, so PATH is the one the terminal has (uv, mise).
    static func run(_ command: String, directory: String) async throws -> ShellResult {
        try await withCheckedThrowingContinuation { continuation in
            DispatchQueue.global(qos: .userInitiated).async {
                let process = Process()
                process.executableURL = URL(fileURLWithPath: "/bin/zsh")
                process.arguments = ["-lc", command]
                let expanded = (directory as NSString).expandingTildeInPath
                if !expanded.isEmpty {
                    process.currentDirectoryURL = URL(fileURLWithPath: expanded)
                }
                let outputPipe = Pipe()
                let errorPipe = Pipe()
                process.standardOutput = outputPipe
                process.standardError = errorPipe
                do {
                    try process.run()
                } catch {
                    continuation.resume(
                        throwing: BackendError(
                            message: "Could not start the command in \(expanded): "
                                + error.localizedDescription))
                    return
                }
                // Both pipes are drained while the process runs; a full pipe would block it.
                let errorBox = DataBox()
                let group = DispatchGroup()
                group.enter()
                DispatchQueue.global(qos: .utility).async {
                    errorBox.data = errorPipe.fileHandleForReading.readDataToEndOfFile()
                    group.leave()
                }
                let outputData = outputPipe.fileHandleForReading.readDataToEndOfFile()
                group.wait()
                process.waitUntilExit()
                continuation.resume(
                    returning: ShellResult(
                        status: process.terminationStatus,
                        output: String(data: outputData, encoding: .utf8) ?? "",
                        errors: String(data: errorBox.data, encoding: .utf8) ?? ""))
            }
        }
    }
}

/// Talks to the two command lines. It holds no state; settings are read at each call.
struct Backend {
    let s2mDirectory: String
    let s2mCommand: String
    let storeDirectory: String
    let hksDirectory: String
    let hksCommand: String
    let ksRoot: String

    static func current() -> Backend {
        let defaults = UserDefaults.standard
        func value(_ key: String) -> String {
            (defaults.string(forKey: key) ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        }
        return Backend(
            s2mDirectory: value(SettingKey.s2mDirectory),
            s2mCommand: value(SettingKey.s2mCommand),
            storeDirectory: value(SettingKey.storeDirectory),
            hksDirectory: value(SettingKey.hksDirectory),
            hksCommand: value(SettingKey.hksCommand),
            ksRoot: value(SettingKey.ksRoot))
    }

    // MARK: session2memory

    private func console<T: Decodable>(_ arguments: [String]) async throws -> T {
        var parts = [s2mCommand, "auto", "console"] + arguments.map(Shell.quote)
        if !storeDirectory.isEmpty {
            parts += ["--store", Shell.quote((storeDirectory as NSString).expandingTildeInPath)]
        }
        let result = try await Shell.run(parts.joined(separator: " "), directory: s2mDirectory)
        guard let data = lastJSONLine(result.output) else {
            throw BackendError(message: failureText(result, what: "session2memory"))
        }
        let envelope: Envelope<T>
        do {
            envelope = try JSONDecoder().decode(Envelope<T>.self, from: data)
        } catch {
            throw BackendError(
                message: "Unexpected answer from session2memory: \(error.localizedDescription)")
        }
        if envelope.ok, let payload = envelope.data {
            return payload
        }
        throw BackendError(message: envelope.error ?? "session2memory reported a failure")
    }

    func overview() async throws -> Overview { try await console(["overview"]) }

    func projects() async throws -> [Project] { try await console(["projects"]) }

    func memories(project: String, status: String) async throws -> [Memory] {
        try await console(["memories", "--project", project, "--status", status])
    }

    func memory(id: String) async throws -> MemoryDetail {
        try await console(["memory", "--id", id])
    }

    /// Memory whose text holds every word of `query`, in one workspace or (nil) in all.
    func search(query: String, project: String?, status: String) async throws -> [Memory] {
        try await console(Self.searchArguments(query: query, project: project, status: status))
    }

    /// How many search results are asked for; as many shown means there may be more.
    static let searchLimit = 500

    static func searchArguments(query: String, project: String?, status: String) -> [String] {
        var arguments = [
            "search", "--query", query, "--status", status, "--limit", String(searchLimit),
        ]
        if let project, !project.isEmpty { arguments += ["--project", project] }
        return arguments
    }

    func sessions(
        project: String?, tool: String?, scope: String = "", query: String = "",
        pendingOnly: Bool = false, limit: Int
    ) async throws -> [SessionSummary] {
        try await console(
            Self.sessionsArguments(
                project: project, tool: tool, scope: scope, query: query,
                pendingOnly: pendingOnly, limit: limit))
    }

    static func sessionsArguments(
        project: String?, tool: String?, scope: String = "", query: String, pendingOnly: Bool,
        limit: Int
    ) -> [String] {
        var arguments = ["sessions", "--limit", String(limit)]
        if let project, !project.isEmpty { arguments += ["--project", project] }
        if let tool, !tool.isEmpty { arguments += ["--tool", tool] }
        if !scope.isEmpty { arguments += ["--scope", scope] }
        let words = query.trimmingCharacters(in: .whitespacesAndNewlines)
        if !words.isEmpty { arguments += ["--query", words] }
        if pendingOnly { arguments.append("--pending") }
        return arguments
    }

    func session(key: String) async throws -> SessionDetail {
        try await console(["session", "--key", key])
    }

    func runs(limit: Int) async throws -> [RunRecord] {
        try await console(["runs", "--limit", String(limit)])
    }

    func retire(id: String, reason: String) async throws -> MemoryDetail {
        try await console(["retire", "--id", id, "--reason", reason])
    }

    func restore(id: String, reason: String) async throws -> MemoryDetail {
        try await console(["restore", "--id", id, "--reason", reason])
    }

    /// Reword an active memory; the answer is the new entry, the old one is superseded.
    func edit(id: String, text: String, reason: String) async throws -> MemoryDetail {
        try await console(["edit", "--id", id, "--text", text, "--reason", reason])
    }

    func add(project: String, kind: String, text: String, reason: String) async throws
        -> MemoryDetail
    {
        try await console(
            ["add", "--project", project, "--kind", kind, "--text", text, "--reason", reason])
    }

    /// Have the next run curate the session again.
    func requeue(key: String) async throws -> SessionDetail {
        try await console(["requeue", "--key", key])
    }

    /// Take the session out of the curation queue.
    func skip(key: String) async throws -> SessionDetail {
        try await console(["skip", "--key", key])
    }

    // MARK: HKS

    func query(_ question: String) async throws -> QueryResponse {
        var command = ""
        if !ksRoot.isEmpty {
            command += "KS_ROOT=" + Shell.quote((ksRoot as NSString).expandingTildeInPath) + " "
        }
        command += hksCommand + " query " + Shell.quote(question) + " --writeback=no"
        let result = try await Shell.run(command, directory: hksDirectory)
        guard let start = result.output.firstIndex(of: "{"),
            let data = String(result.output[start...]).data(using: .utf8)
        else {
            throw BackendError(message: failureText(result, what: "ks"))
        }
        do {
            return try JSONDecoder().decode(QueryResponse.self, from: data)
        } catch {
            throw BackendError(message: "Unexpected answer from ks: \(error.localizedDescription)")
        }
    }

    // MARK: helpers

    /// The console prints exactly one JSON object; anything before it is tool chatter.
    private func lastJSONLine(_ output: String) -> Data? {
        let lines = output.split(separator: "\n").map(String.init)
        guard let line = lines.last(where: { $0.hasPrefix("{") }) else { return nil }
        return line.data(using: .utf8)
    }

    private func failureText(_ result: ShellResult, what: String) -> String {
        let detail = result.errors.trimmingCharacters(in: .whitespacesAndNewlines)
        let tail = String(detail.suffix(600))
        if tail.isEmpty {
            return "\(what) gave no answer (exit \(result.status)). Check the folder and command in Settings."
        }
        return "\(what) failed (exit \(result.status)): \(tail)"
    }
}
