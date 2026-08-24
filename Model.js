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
 * Returns Panda icon for the bar and headers.
 */
function statusIcon(state) {
  var s = String(state || "").toLowerCase()
  if (s === "working" || s === "busy" || s === "running" || s === "thinking") {
    return "󰑐" // Spinner / thinking
  }
  if (s === "waiting" || s === "prompt" || s === "input") {
    return "󰌵" // Lightbulb / prompt
  }
  if (s === "error" || s === "failed") {
    return "󰅚" // Alert / error
  }
  return "🐼" // Panda Emoji (guaranteed Panda face on all fonts)
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
 * Strips reasoning / internal monologue tags (handles both closed and unclosed tags).
 */
function stripReasoning(text) {
  if (!text) return ""
  var t = String(text)
  t = t.replace(/<think>[\s\S]*?(?:<\/think>|$)/gi, "")
  t = t.replace(/<thought>[\s\S]*?(?:<\/thought>|$)/gi, "")
  t = t.replace(/<reasoning>[\s\S]*?(?:<\/reasoning>|$)/gi, "")
  t = t.replace(/<antThinking>[\s\S]*?(?:<\/antThinking>|$)/gi, "")
  t = t.replace(/<scratchpad>[\s\S]*?(?:<\/scratchpad>|$)/gi, "")
  t = t.replace(/<reflection>[\s\S]*?(?:<\/reflection>|$)/gi, "")
  t = t.replace(/^(?:Thinking Process|Thought|Reasoning):\s*[\s\S]*?(?=\n\n|\n[A-Z]|$)/gi, "")
  return t.trim()
}

/**
 * Sanitizes unescaped XML/HTML tags in text so Qt Quick Markdown parser doesn't drop/swallow text.
 */
function sanitizeTextForMarkdown(text) {
  if (!text) return ""
  var t = String(text)

  // Protect inline code backticks `...`
  var inlineCodes = []
  t = t.replace(/`[^`\n]+`/g, function(m) {
    inlineCodes.push(m)
    return "__INLINE_CODE_" + (inlineCodes.length - 1) + "__"
  })

  // Escape XML/HTML-like tags that are NOT standard markdown/HTML tags or URLs
  var allowedTags = /^(?:a|b|i|em|strong|code|pre|p|br|hr|ul|ol|li|blockquote|h[1-6]|img)(?:\s+[^>]*)?$/i
  t = t.replace(/<([^>\n]+)>/g, function(match, tagContent) {
    var trimmed = tagContent.trim()
    if (/^https?:\/\/|^mailto:/i.test(trimmed)) {
      return match
    }
    var tagName = trimmed.replace(/^\//, "").split(/\s+/)[0]
    if (allowedTags.test(tagName)) {
      return match
    }
    return "&lt;" + tagContent + "&gt;"
  })

  // Restore inline codes
  t = t.replace(/__INLINE_CODE_(\d+)__/g, function(_, idx) {
    return inlineCodes[Number(idx)]
  })

  return t
}

/**
 * Parses message text into structured visual blocks (markdown text, code blocks).
 */
function parseBlocks(content) {
  if (!content) return []
  var raw = String(content)
  var clean = stripReasoning(raw)

  // If stripping reasoning left nothing, fallback to raw content or "✓ Done."
  if (!clean.length) {
    clean = raw.trim().length > 0 ? raw.trim() : "✓ Done."
  }

  var blocks = []
  var codeBlockRegex = /```([a-zA-Z0-9_-]*)\n([\s\S]*?)```/g
  var lastIndex = 0
  var match

  while ((match = codeBlockRegex.exec(clean)) !== null) {
    var preText = clean.substring(lastIndex, match.index).trim()
    if (preText.length > 0) {
      blocks.push({
        type: "text",
        text: sanitizeTextForMarkdown(preText)
      })
    }

    var lang = (match[1] || "").trim() || "bash"
    var code = match[2] || ""
    blocks.push({
      type: "code",
      language: lang,
      code: String(code || "").replace(/\s+$/, "")
    })

    lastIndex = match.index + match[0].length
  }

  var remaining = clean.substring(lastIndex).trim()
  if (remaining.length > 0) {
    blocks.push({
      type: "text",
      text: sanitizeTextForMarkdown(remaining)
    })
  }

  if (blocks.length === 0 && clean.length > 0) {
    blocks.push({
      type: "text",
      text: sanitizeTextForMarkdown(clean)
    })
  }

  return blocks
}

/**
 * Formats epoch timestamp (seconds) into local date and time string (e.g., 'Today, 14:30', '23 Aug, 11:29').
 */
function formatTimestamp(timestamp) {
  if (!timestamp) return ""
  var d = new Date(Number(timestamp) * 1000)
  if (isNaN(d.getTime())) return ""

  var now = new Date()
  var isToday = (d.toDateString() === now.toDateString())

  var yesterday = new Date()
  yesterday.setDate(yesterday.getDate() - 1)
  var isYesterday = (d.toDateString() === yesterday.toDateString())

  var h = ("0" + d.getHours()).slice(-2)
  var m = ("0" + d.getMinutes()).slice(-2)
  var timeStr = h + ":" + m

  if (isToday) {
    return "Today, " + timeStr
  } else if (isYesterday) {
    return "Yesterday, " + timeStr
  } else {
    var months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    var day = d.getDate()
    var month = months[d.getMonth()]
    return day + " " + month + ", " + timeStr
  }
}

/**
 * Legacy alias for formatTimestamp.
 */
function formatTime(timestamp) {
  return formatTimestamp(timestamp)
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
    return truncateText(status.last_query, maxLen || 24)
  }
  return "Botty"
}

/**
 * Generates bar tooltip text.
 */
function getTooltipText(status) {
  if (!status) return "Botty 🐼 - Desktop Agent Assistant"
  var lines = ["Botty 🐼 - Desktop Agent Assistant"]
  lines.push("Status: " + statusBadgeText(status.state))
  if (status.active_model) lines.push("Model: " + status.active_model)
  if (status.memory_count !== undefined) lines.push("Memories: " + status.memory_count + " | Skills: " + (status.skills_count || 0))
  if (status.last_query) lines.push("Last: " + truncateText(status.last_query, 45))
  lines.push("Left-click: Open (Super+A) | Right-click: Screen Context (Super+Shift+A)")
  return lines.join("\n")
}

/**
 * Resolves the logo SVG, fallback icon, soft brand color, and display name for an agent engine.
 */
function agentLogoInfo(engineId) {
  var eng = String(engineId || "").toLowerCase()

  if (eng === "hermes") {
    return {
      svg: "assets/icons/models/hermes.svg",
      fallbackIcon: "☤",
      color: "#FBBF24", // Soft warm amber
      name: "Hermes"
    }
  }
  if (eng === "omp" || eng === "opencode") {
    return {
      svg: "assets/icons/models/opencode.svg",
      fallbackIcon: "⬡",
      color: "#2DD4BF", // Soft teal
      name: "OpenCode"
    }
  }
  if (eng === "claude") {
    return {
      svg: "assets/icons/models/claude.svg",
      fallbackIcon: "✳",
      color: "#E07A5F", // Soft terracotta
      name: "Claude"
    }
  }
  if (eng === "codex") {
    return {
      svg: "assets/icons/models/openai.svg",
      fallbackIcon: "✹",
      color: "#34D399", // Soft mint
      name: "Codex"
    }
  }
  return {
    svg: "assets/icons/panda.svg",
    fallbackIcon: "🐼",
    color: "#93C5FD", // Soft pastel blue
    name: eng ? (eng.charAt(0).toUpperCase() + eng.slice(1)) : "Botty"
  }
}

/**
 * Resolves the logo SVG, fallback icon, soft brand color, and display name for a model.
 */
function modelLogoInfo(modelId, engineId) {
  var s = String(modelId || "").toLowerCase()
  var eng = String(engineId || "").toLowerCase()

  if (s.includes("claude") || s.includes("anthropic") || eng === "claude") {
    return {
      svg: "assets/icons/models/claude.svg",
      fallbackIcon: "󰚩",
      color: "#E07A5F", // Soft warm terracotta
      name: "Claude",
      provider: "Anthropic"
    }
  }
  if (s.includes("gpt") || s.includes("openai") || s.includes("o1") || s.includes("o3") || s.includes("codex") || eng === "codex") {
    return {
      svg: "assets/icons/models/openai.svg",
      fallbackIcon: "󰘚",
      color: "#34D399", // Soft mint
      name: "OpenAI",
      provider: "OpenAI"
    }
  }
  if (s.includes("gemini") || s.includes("google") || s.includes("antigravity")) {
    return {
      svg: "assets/icons/models/gemini.svg",
      fallbackIcon: "✦",
      color: "#818CF8", // Soft periwinkle / lavender
      name: "Gemini",
      provider: "Google"
    }
  }
  if (s.includes("deepseek") || s.includes("r1")) {
    return {
      svg: "assets/icons/models/deepseek.svg",
      fallbackIcon: "🐋",
      color: "#38BDF8", // Soft sky cyan
      name: "DeepSeek",
      provider: "DeepSeek"
    }
  }
  if (s.includes("mistral") || s.includes("codestral") || s.includes("mixtral")) {
    return {
      svg: "assets/icons/models/mistral.svg",
      fallbackIcon: "󱢺",
      color: "#FB923C", // Soft peach
      name: "Mistral",
      provider: "Mistral"
    }
  }
  if (s.includes("llama") || s.includes("meta")) {
    return {
      svg: "assets/icons/models/meta.svg",
      fallbackIcon: "󰙯",
      color: "#60A5FA", // Soft pastel blue
      name: "Meta",
      provider: "Meta"
    }
  }
  if (s.includes("qwen") || s.includes("qwq") || s.includes("alibaba")) {
    return {
      svg: "assets/icons/models/qwen.svg",
      fallbackIcon: "󰚩",
      color: "#A78BFA", // Soft violet
      name: "Qwen",
      provider: "Qwen"
    }
  }
  if (s.includes("opencode") || eng === "omp") {
    return {
      svg: "assets/icons/models/opencode.svg",
      fallbackIcon: "⬡",
      color: "#2DD4BF", // Soft teal
      name: "OpenCode",
      provider: "OpenCode"
    }
  }
  if (s.includes("hermes") || s.includes("ox-") || s.includes("nous") || eng === "hermes") {
    return {
      svg: "assets/icons/models/hermes.svg",
      fallbackIcon: "☤",
      color: "#FBBF24", // Soft amber
      name: "Hermes",
      provider: "Hermes"
    }
  }
  return {
    svg: "assets/icons/models/hermes.svg",
    fallbackIcon: "󰚩",
    color: "#93C5FD", // Soft pastel blue
    name: "Model",
    provider: "AI"
  }
}

/**
 * Cleans up raw model IDs into human-readable badge labels.
 */
function cleanModelName(modelId) {
  if (!modelId) return ""
  var m = String(modelId).trim()
  m = m.replace(/^[^/]+\//, "")
  return m
}

