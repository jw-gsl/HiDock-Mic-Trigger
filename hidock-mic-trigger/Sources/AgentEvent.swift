import Foundation

/// One normalized agent event, decoded from the pipeline's event stream.
///
/// The Python layer (`shared/agent_events.py`) emits these as NDJSON lines on
/// stderr, each prefixed with the Unit-Separator byte (0x1f). This is the Swift
/// twin of that schema — keep the two in sync.
enum AgentEvent: Equatable {
    case stage(label: String)
    case text(delta: String)
    case tool(id: String, name: String, inputSummary: String?)
    case toolResult(id: String, ok: Bool, preview: String?)
    case usage(inputTokens: Int?, outputTokens: Int?, costUSD: Double?)
    case meta(engine: String?, sessionId: String?, model: String?)
    case error(message: String)
    case done(ok: Bool, summaryPath: String?)

    /// The byte every event line starts with (ASCII Unit Separator, 0x1f).
    /// Must match `EVENT_PREFIX` in `shared/agent_events.py`.
    static let prefix = "\u{1f}"

    /// Decode one stderr line into an event, or nil if it isn't an event line.
    /// Tolerates the prefix being present or absent (so callers can pass raw
    /// lines straight through).
    static func parse(line: String) -> AgentEvent? {
        var s = line
        if s.hasPrefix(prefix) { s.removeFirst() }
        s = s.trimmingCharacters(in: .whitespacesAndNewlines)
        guard s.hasPrefix("{"),
              let data = s.data(using: .utf8),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let t = obj["t"] as? String
        else { return nil }

        switch t {
        case "stage":
            return .stage(label: obj["label"] as? String ?? "")
        case "text":
            guard let delta = obj["delta"] as? String, !delta.isEmpty else { return nil }
            return .text(delta: delta)
        case "tool":
            return .tool(
                id: obj["id"] as? String ?? "",
                name: obj["name"] as? String ?? "tool",
                inputSummary: summariseToolInput(obj["input"])
            )
        case "tool_result":
            return .toolResult(
                id: obj["id"] as? String ?? "",
                ok: obj["ok"] as? Bool ?? true,
                preview: obj["preview"] as? String
            )
        case "usage":
            return .usage(
                inputTokens: obj["input_tokens"] as? Int,
                outputTokens: obj["output_tokens"] as? Int,
                costUSD: obj["cost_usd"] as? Double
            )
        case "meta":
            return .meta(
                engine: obj["engine"] as? String,
                sessionId: obj["session_id"] as? String,
                model: obj["model"] as? String
            )
        case "error":
            return .error(message: obj["message"] as? String ?? "Unknown error")
        case "done":
            return .done(ok: obj["ok"] as? Bool ?? true,
                         summaryPath: obj["summary_path"] as? String)
        default:
            return nil
        }
    }

    /// Quick check used by callers that line-read mixed stderr (events + logs).
    static func isEventLine(_ line: String) -> Bool {
        line.hasPrefix(prefix)
    }

    /// Produce a short human label for a tool's input dict, e.g.
    /// `transcript.md`, `grep "foo"`, or a truncated JSON blob.
    private static func summariseToolInput(_ input: Any?) -> String? {
        guard let dict = input as? [String: Any] else {
            if let s = input as? String, !s.isEmpty { return s }
            return nil
        }
        if let path = dict["file_path"] as? String ?? dict["path"] as? String ?? dict["notebook_path"] as? String {
            return (path as NSString).lastPathComponent
        }
        if let cmd = dict["command"] as? String { return cmd }
        if let pattern = dict["pattern"] as? String { return pattern }
        if let url = dict["url"] as? String { return url }
        if let query = dict["query"] as? String { return query }
        if let prompt = dict["prompt"] as? String { return String(prompt.prefix(60)) }
        // Fall back to a compact JSON rendering, truncated.
        if let data = try? JSONSerialization.data(withJSONObject: dict),
           let s = String(data: data, encoding: .utf8) {
            return s.count > 80 ? String(s.prefix(80)) + "…" : s
        }
        return nil
    }
}
