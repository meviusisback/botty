// Model.js - Helper functions and data parsers for Botty Agent QML UI.

/**
 * Returns color corresponding to the agent state.
 */
function statusColor(state, fg, accent, urgent) {
  var s = String(state || "").toLowerCase()
  if (s === "working" || s === "busy" || s === "running" || s === "thinking") {
    return accent || "#38BDF8"
  }
  if (s === "waiting" || s === "prompt" || s === "input") {
    return "#F59E0B"
  }
  if (s === "error" || s === "failed") {
    return urgent || "#EF4444"
  }
  if (s === "done" || s === "completed") {
    return "#10B981"
  }
  return fg || "#E2E8F0"
}

/**
 * Returns Nerd Font icon glyph for the bar and headers.
 */
function statusIcon(state) {
  var s = String(state || "").toLowerCase()
  if (s === "working" || s === "busy" || s === "running" || s === "thinking") {
    return "󰑐" // Spinner / sync
  }
  if (s === "waiting" || s === "prompt" || s === "input") {
    return "󰌵" // Lightbulb / prompt
  }
  if (s === "error" || s === "failed") {
    return "󰅚" // Alert / error
  }
  return "󰚩" // Botty robot glyph
}

/**
 * Returns human-readable status badge string.
 */
function statusBadgeText(state) {
  var s = String(state || "").toLowerCase()
  if (s === "working" || s === "busy" || s === "thinking") return "THINKING…"
  if (s === "waiting") return "PROMPT"
  if (s === "error") return "ERROR"
  if (s === "done") return "DONE"
  return "ONLINE"
}

/**
 * Strips reasoning / internal monologue tags.
 */
function stripReasoning(text) {
  if (!text) return ""
  var t = String(text)
  t = t.replace(/<thought>[\s\S]*?<\/thought>/gi, "")
  t = t.replace(/<think>[\s\S]*?<\/think>/gi, "")
  t = t.replace(/<reasoning>[\s\S]*?<\/reasoning>/gi, "")
  t = t.replace(/^(?:Thinking Process|Thought|Reasoning):\s*[\s\S]*?(?=\n\n|\n[A-Z]|$)/gi, "")
  return t.trim()
}

/**
 * Parses message text into structured visual blocks (markdown text, code blocks, actions).
 */
function parseBlocks(content) {
  if (!content) return []
  var clean = stripReasoning(content)
  var blocks = []
  
  // Split on code fences ```lang ... ```
  var codeBlockRegex = /```([a-zA-Z0-9_-]*)\n([\s\S]*?)```/g
  var lastIndex = 0
  var match

  while ((match = codeBlockRegex.exec(clean)) !== null) {
    // Text before the code block
    var preText = clean.substring(lastIndex, match.index).trim()
    if (preText.length > 0) {
      blocks.push({
        type: "text",
        text: preText
      })
    }

    var lang = (match[1] || "").trim() || "text"
    var code = match[2] || ""
    blocks.push({
      type: "code",
      language: lang,
      code: code.trimEnd()
    })

    lastIndex = match.index + match[0].length
  }

  // Trailing text
  var remaining = clean.substring(lastIndex).trim()
  if (remaining.length > 0) {
    blocks.push({
      type: "text",
      text: remaining
    })
  }

  // If no code blocks matched, return single text block
  if (blocks.length === 0 && clean.length > 0) {
    blocks.push({
      type: "text",
      text: clean
    })
  }

  return blocks
}

/**
 * Formats epoch timestamp (seconds) into local HH:MM.
 */
function formatTime(timestamp) {
  if (!timestamp) return ""
  var d = new Date(Number(timestamp) * 1000)
  var h = ("0" + d.getHours()).slice(-2)
  var m = ("0" + d.getMinutes()).slice(-2)
  return h + ":" + m
}

/**
 * Truncates text with ellipsis if length exceeds maxLen.
 */
function truncateText(text, maxLen) {
  if (!text) return ""
  var str = String(text)
  var limit = Number(maxLen) || 40
  if (str.length <= limit) return str
  return str.substring(0, limit - 1) + "…"
}

/**
 * Formats status headline for the top bar widget.
 */
function getBarHeadline(status, maxLen) {
  if (!status) return "Botty"
  if (status.state === "working") return "Botty thinking…"
  if (status.state === "error") return "Botty: Error"
  if (status.last_query && status.last_query.length > 0) {
    return truncateText(status.last_query, maxLen || 30)
  }
  return "Botty Ready"
}

/**
 * Generates bar tooltip text.
 */
function getTooltipText(status) {
  if (!status) return "Botty Agent Assistant"
  var lines = ["Botty - Desktop Agent Assistant (Hermes)"]
  lines.push("Status: " + statusBadgeText(status.state))
  if (status.active_model) lines.push("Model: " + status.active_model)
  if (status.memory_count !== undefined) lines.push("Memories: " + status.memory_count + " | Skills: " + (status.skills_count || 0))
  if (status.last_query) lines.push("Last: " + truncateText(status.last_query, 50))
  lines.push("Left-click to open | Right-click to capture screen")
  return lines.join("\n")
}
