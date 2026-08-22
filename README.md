# Botty — Omarchy Desktop Agent Assistant Bar Widget

**Botty** is an interactive, native Omarchy bar widget and desktop assistant powered by the Hermes Agent profile `botty`. It integrates directly into your top bar to provide instant screen awareness, multimodal media understanding, persistent memory, continuous learning, model switching, and desktop automation actions.

![Botty Preview](preview.png)

---

## ✨ Features

- 󰚩 **Native Omarchy Bar Widget**: Status-aware bar icon that indicates live state:
  - **Thinking / Processing**: Animated pulsing accent spinner (`󰑐`).
  - **Waiting / Prompt**: Amber indicator (`󰌵`).
  - **Error / Alert**: Urgent red glyph (`󰅚`).
  - **Ready / Done**: Theme foreground robot glyph (`󰚩`).
- 󰹑 **Live Screen Awareness**: Automatically captures active Hyprland windows, extracts OCR text, and injects visual context into queries with one click.
- 󰋩 **Multimodal Media Support**: Attach screenshots, images, or clipboard visuals (`wl-paste`) for visual reasoning and troubleshooting.
- 󰭹 **Graphical Response Rendering**:
  - Concise answers without chain-of-thought bloat.
  - Native Markdown text formatting (bold, headers, bullet lists).
  - Monospaced code blocks with syntax badges and one-click **Copy** button.
  - Computer action execution receipts and memory badges.
- 󰒓 **Dynamic Model Switching**: Switch the underlying LLM directly from the widget (OpenCode Go, Claude 3.7 Sonnet, GPT-4o / 5.5, Gemini 2.0 / 3.7 Flash, DeepSeek V3, or local Ollama).
- 󰋚 **Continuous Learning & Persistent Memory**:
  - Automatically compacts and distills conversation learnings into long-term memory (`MEMORY.md` / `USER.md`).
  - Create and inspect custom Hermes skills.
- 󰘦 **Desktop Actions**: Executes terminal commands, file modifications, and Hyprland workspace / window operations directly on your Omarchy system.

---

## 🚀 Installation

### 1. Link Plugin to Omarchy

```bash
mkdir -p ~/.config/omarchy/plugins
ln -s ~/repo/botty ~/.config/omarchy/plugins/meviusisback.botty
```

### 2. Validate Plugin

```bash
omarchy plugin validate ~/.config/omarchy/plugins/meviusisback.botty
```

### 3. Restart Omarchy Shell

```bash
omarchy restart shell
```

---

## ⌨️ Global Keybindings

Add to your `~/.config/hypr/bindings.lua`:

```lua
-- Toggle Botty Assistant
o.bind("SUPER + B", "Botty Assistant", "omarchy shell meviusisback.botty toggle")

-- Quick Ask with Screen Context
o.bind("SUPER + SHIFT + B", "Botty Screen Query", "omarchy shell meviusisback.botty capture_and_ask")
```

---

## 🛠️ CLI & IPC Commands

You can interact with Botty directly via CLI or IPC:

```bash
# Toggle popup panel
omarchy shell meviusisback.botty toggle

# Ask a direct question
omarchy shell meviusisback.botty ask "How do I configure Hyprland animations?"

# Read screen and ask
omarchy shell meviusisback.botty captureAndAsk "What is shown on my active window?"

# Clear chat history
omarchy shell meviusisback.botty clear
```

---

## 🔒 Security & Privacy

- **Secret Redaction**: API keys (OpenAI, Anthropic, OpenRouter, GitHub tokens) and Bearer headers are masked prior to UI rendering.
- **Controlled Approvals**: Inherits Hermes profile `botty` smart approval policies and dangerous command deny-lists.
- **Local Storage**: All history, screenshots, and memories are stored locally in `~/.local/share/botty/` and `~/.hermes/profiles/botty/`.

---

## 📄 License

MIT License © 2026 meviusisback
