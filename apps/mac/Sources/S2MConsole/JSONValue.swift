import Foundation

/// Any JSON value. Run reports and trace details have no fixed shape, so they are kept as
/// they came and shown as text.
enum JSONValue: Codable, Hashable {
    case null
    case bool(Bool)
    case number(Double)
    case string(String)
    case array([JSONValue])
    case object([String: JSONValue])

    init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if container.decodeNil() {
            self = .null
        } else if let value = try? container.decode(Bool.self) {
            self = .bool(value)
        } else if let value = try? container.decode(Double.self) {
            self = .number(value)
        } else if let value = try? container.decode(String.self) {
            self = .string(value)
        } else if let value = try? container.decode([JSONValue].self) {
            self = .array(value)
        } else if let value = try? container.decode([String: JSONValue].self) {
            self = .object(value)
        } else {
            throw DecodingError.dataCorruptedError(
                in: container, debugDescription: "Unsupported JSON value")
        }
    }

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .null: try container.encodeNil()
        case .bool(let value): try container.encode(value)
        case .number(let value): try container.encode(value)
        case .string(let value): try container.encode(value)
        case .array(let value): try container.encode(value)
        case .object(let value): try container.encode(value)
        }
    }

    subscript(key: String) -> JSONValue? {
        if case .object(let members) = self {
            return members[key]
        }
        return nil
    }

    var members: [(key: String, value: JSONValue)] {
        if case .object(let found) = self {
            return found.sorted { $0.key < $1.key }
        }
        return []
    }

    var stringValue: String? {
        if case .string(let value) = self {
            return value
        }
        return nil
    }

    var numberValue: Double? {
        if case .number(let value) = self {
            return value
        }
        return nil
    }

    /// A short single-line rendering for summaries.
    var brief: String {
        switch self {
        case .null: return "–"
        case .bool(let value): return value ? "yes" : "no"
        case .number(let value):
            if value == value.rounded() && abs(value) < 1e15 {
                return String(Int64(value))
            }
            return String(format: "%.4g", value)
        case .string(let value): return value
        case .array(let value): return "\(value.count) items"
        case .object(let value): return "\(value.count) fields"
        }
    }

    /// Indented JSON text.
    var pretty: String {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
        guard let data = try? encoder.encode(self),
            let text = String(data: data, encoding: .utf8)
        else {
            return brief
        }
        return text
    }
}
