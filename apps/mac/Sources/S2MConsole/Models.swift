import Foundation

// Shapes of `session2memory auto console <view>` (src/session2memory/auto/console.py).
// Fields the console can do without are optional, so an older or newer command line still
// decodes.

struct Envelope<T: Decodable>: Decodable {
    let ok: Bool
    let data: T?
    let error: String?
}

struct Overview: Decodable {
    let store: String
    let counts: [String: Int]
    let lastRun: RunSummary?

    enum CodingKeys: String, CodingKey {
        case store, counts
        case lastRun = "last_run"
    }
}

struct RunSummary: Decodable {
    let runId: String
    let finishedAt: String?

    enum CodingKeys: String, CodingKey {
        case runId = "run_id"
        case finishedAt = "finished_at"
    }
}

struct Project: Decodable, Identifiable, Hashable {
    let project: String
    let name: String
    let active: Int
    let superseded: Int
    let retired: Int
    let stale: Int
    let sessions: Int
    let sessionsPending: Int
    let lastActivity: String?

    var id: String { project }

    enum CodingKeys: String, CodingKey {
        case project, name, active, superseded, retired, stale, sessions
        case sessionsPending = "sessions_pending"
        case lastActivity = "last_activity"
    }
}

struct Memory: Decodable, Identifiable, Hashable {
    let id: String
    let project: String
    let scope: String
    let kind: String
    let text: String
    let status: String
    let createdAt: String
    let lastConfirmedAt: String
    let supersedesId: String?
    let evidence: [String]

    enum CodingKeys: String, CodingKey {
        case id, project, scope, kind, text, status, evidence
        case createdAt = "created_at"
        case lastConfirmedAt = "last_confirmed_at"
        case supersedesId = "supersedes_id"
    }
}

struct MemoryEvent: Decodable, Hashable {
    let op: String
    let at: String
    let detail: JSONValue
}

struct MemoryDetail: Decodable, Identifiable {
    let id: String
    let project: String
    let scope: String
    let kind: String
    let text: String
    let status: String
    let createdAt: String
    let lastConfirmedAt: String
    let supersedesId: String?
    let evidence: [String]
    let replacedBy: [String]
    let restorable: Bool
    let events: [MemoryEvent]
    let evidenceSessions: [SessionSummary]

    enum CodingKeys: String, CodingKey {
        case id, project, scope, kind, text, status, evidence, restorable, events
        case createdAt = "created_at"
        case lastConfirmedAt = "last_confirmed_at"
        case supersedesId = "supersedes_id"
        case replacedBy = "replaced_by"
        case evidenceSessions = "evidence_sessions"
    }
}

struct SessionSummary: Decodable, Identifiable, Hashable {
    let key: String
    let tool: String?
    let sessionId: String?
    let agent: String?
    let scope: String?
    let project: String?
    let title: String?
    let summary: String?
    let summaryOrigin: String?
    let lastActivity: String?
    let pending: Bool?

    var id: String { key }

    enum CodingKeys: String, CodingKey {
        case key, tool, agent, scope, project, title, summary, pending
        case sessionId = "session_id"
        case summaryOrigin = "summary_origin"
        case lastActivity = "last_activity"
    }
}

/// Whether a run would read a session that waits for curation.
struct Curation: Decodable, Equatable {
    /// What makes every run pass over the session.
    let blocked: [String]
    /// It waits, but a run with the default age limit drops it unread.
    let expires: Bool?
    /// A person sent it back: the run reads it whatever its age.
    let byHand: Bool?
    /// What depends on how the run is started.
    let notes: [String]?

    enum CodingKeys: String, CodingKey {
        case blocked, expires, notes
        case byHand = "by_hand"
    }
}

struct SessionDetail: Decodable {
    let key: String
    let tool: String?
    let sessionId: String?
    let agent: String?
    let scope: String?
    let project: String?
    let title: String?
    let summary: String?
    let summaryOrigin: String?
    let lastActivity: String?
    let pending: Bool?
    let digest: String?
    let memories: [Memory]
    let curation: Curation?

    enum CodingKeys: String, CodingKey {
        case key, tool, agent, scope, project, title, summary, pending, digest, memories
        case curation
        case sessionId = "session_id"
        case summaryOrigin = "summary_origin"
        case lastActivity = "last_activity"
    }
}

struct RunRecord: Decodable, Identifiable, Hashable {
    let runId: String
    let startedAt: String
    let finishedAt: String?
    let report: JSONValue

    var id: String { runId }

    enum CodingKeys: String, CodingKey {
        case report
        case runId = "run_id"
        case startedAt = "started_at"
        case finishedAt = "finished_at"
    }
}

// Shape of `ks query` (HKS). Only what the console shows is decoded strictly.

struct QueryEvidence: Decodable, Hashable {
    let sourceRelpath: String?
    let route: String?
    let quote: String?
    let sectionPath: String?

    enum CodingKeys: String, CodingKey {
        case route, quote
        case sourceRelpath = "source_relpath"
        case sectionPath = "section_path"
    }
}

struct TraceStep: Decodable, Hashable {
    let kind: String
    let detail: JSONValue
}

struct QueryTrace: Decodable {
    let route: String
    let steps: [TraceStep]
}

struct QueryResponse: Decodable {
    let answer: String
    let source: [String]
    let confidence: Double
    let evidence: [QueryEvidence]?
    let trace: QueryTrace
}

/// "2026-11-13T09:28:18+00:00" -> "2026-11-13 09:28".
func shortTime(_ value: String?) -> String {
    guard let value, !value.isEmpty else { return "–" }
    return String(value.replacingOccurrences(of: "T", with: " ").prefix(16))
}
