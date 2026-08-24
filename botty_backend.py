#!/usr/bin/env python3
"""
botty_backend.py - Native backend for Botty Omarchy bar widget and desktop assistant.
Standard-library only. Interfaces with Hermes Agent profile "botty", OMP, Claude, and other agents,
captures screen context, attaches any file/document/media, manages conversation history,
handles dynamic per-engine model & provider selection, continuous learning, memory deletion,
voice dictation, out-of-process floating file picker, and local AES-256 encrypted memory vault management.
"""

import sys
import os
import json
import re
import time
import glob
import subprocess
import shutil
import sqlite3
import argparse
import mimetypes
import hashlib
import socket
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

# Base paths
BOTTY_DATA_DIR = Path.home() / ".local" / "share" / "botty"
BOTTY_DATA_DIR.mkdir(parents=True, exist_ok=True)
os.chmod(BOTTY_DATA_DIR, 0o700)

CONFIG_FILE = BOTTY_DATA_DIR / "config.json"
STATUS_FILE = BOTTY_DATA_DIR / "status.json"
HISTORY_FILE = BOTTY_DATA_DIR / "history.json"
HISTORY_ARCHIVE_FILE = BOTTY_DATA_DIR / "history_archive.jsonl"
VAULT_FILE = BOTTY_DATA_DIR / "vault.enc"
LOCK_FILE = BOTTY_DATA_DIR / "running.pid"
BOTTY_LOG_FILE = BOTTY_DATA_DIR / "botty.log"
VOICE_PID_FILE = BOTTY_DATA_DIR / "voice_record.pid"
VOICE_WAV_FILE = Path("/tmp/botty_dictation.wav")
SCREENSHOT_DIR = BOTTY_DATA_DIR / "captures"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
os.chmod(SCREENSHOT_DIR, 0o700)

HERMES_DIR = Path.home() / ".hermes"
HERMES_BOTTY_DIR = HERMES_DIR / "profiles" / "botty"
HERMES_CONFIG_FILE = HERMES_BOTTY_DIR / "config.yaml"
HERMES_MEMORY_DIR = HERMES_BOTTY_DIR / "memories"
HERMES_SKILLS_DIR = HERMES_BOTTY_DIR / "skills"
HERMES_STATE_DB = HERMES_BOTTY_DIR / "state.db"

DEFAULT_AUTO_COMPACT_THRESHOLD = 14
DEFAULT_COMPACT_PRESERVE_TAIL = 4

def get_auto_compact_threshold() -> int:
    cfg = load_json_file(CONFIG_FILE, {})
    return int(cfg.get("auto_compaction_threshold", DEFAULT_AUTO_COMPACT_THRESHOLD))

def get_compact_preserve_tail() -> int:
    cfg = load_json_file(CONFIG_FILE, {})
    return int(cfg.get("compact_preserve_tail", DEFAULT_COMPACT_PRESERVE_TAIL))

OMP_DIR = Path.home() / ".omp"
OMP_AGENT_DIR = OMP_DIR / "agent"
OMP_CONFIG_FILE = OMP_AGENT_DIR / "config.yml"
OMP_MODELS_DB = OMP_AGENT_DIR / "models.db"

# Redaction patterns for security
REDACTION_PATTERNS = [
    (re.compile(r"\b(sk-[a-zA-Z0-9_-]{8})[a-zA-Z0-9_-]{12,}\b"), r"\1…[REDACTED]"),
    (re.compile(r"\b(ghp_[a-zA-Z0-9]{4})[a-zA-Z0-9]{16,}\b"), r"\1…[REDACTED]"),
    (re.compile(r"\b(xai-[a-zA-Z0-9_-]{8})[a-zA-Z0-9_-]{12,}\b"), r"\1…[REDACTED]"),
    (re.compile(r"(Bearer\s+)[a-zA-Z0-9._~+/-]{16,}", re.IGNORECASE), r"\1[REDACTED]"),
    (re.compile(r"(api[_-]?key\s*[:=]\s*['\"]?)[a-zA-Z0-9._~+/-]{16,}", re.IGNORECASE), r"\1[REDACTED]"),
]

def redact_secrets(text: str) -> str:
    if not text:
        return ""
    t = str(text)
    for pattern, repl in REDACTION_PATTERNS:
        t = pattern.sub(repl, t)
    return t

def strip_reasoning(text: str) -> str:
    """Removes thinking / chain-of-thought blocks to return only concise, actionable answers."""
    if not text:
        return ""
    t = str(text)
    # Strip ANSI escape sequences
    t = re.sub(r"\x1b\[[0-9;]*[a-zA-Z]", "", t)
    t = re.sub(r"<thought>.*?(?:</thought>|$)", "", t, flags=re.DOTALL | re.IGNORECASE)
    t = re.sub(r"<think>.*?(?:</think>|$)", "", t, flags=re.DOTALL | re.IGNORECASE)
    t = re.sub(r"<reasoning>.*?(?:</reasoning>|$)", "", t, flags=re.DOTALL | re.IGNORECASE)
    t = re.sub(r"<antThinking>.*?(?:</antThinking>|$)", "", t, flags=re.DOTALL | re.IGNORECASE)
    t = re.sub(r"<scratchpad>.*?(?:</scratchpad>|$)", "", t, flags=re.DOTALL | re.IGNORECASE)
    t = re.sub(r"<reflection>.*?(?:</reflection>|$)", "", t, flags=re.DOTALL | re.IGNORECASE)
    t = re.sub(r"^(?:Thinking Process|Thought|Reasoning):\s*.*?(?=\n\n|\n[A-Z]|$)", "", t, flags=re.DOTALL | re.IGNORECASE)
    return t.strip()

def load_json_file(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def save_json_file(path: Path, data: Any) -> None:
    tmp_path = path.with_suffix(".tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.chmod(tmp_path, 0o600)
        tmp_path.replace(path)
    except Exception as e:
        if tmp_path.exists():
            tmp_path.unlink()
        raise e

# ── Out-of-Process File Picker ─────────────────────────────────────────────────

def pick_file_dialog() -> Dict[str, Any]:
    try:
        import gi
        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk
        
        dialog = Gtk.FileChooserDialog(
            title="Attach File to Botty",
            parent=None,
            action=Gtk.FileChooserAction.OPEN
        )
        dialog.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OPEN, Gtk.ResponseType.OK
        )
        dialog.set_default_size(740, 500)
        dialog.set_current_folder(str(Path.home()))
        
        all_filter = Gtk.FileFilter()
        all_filter.set_name("All Files & Documents")
        all_filter.add_pattern("*")
        dialog.add_filter(all_filter)
        
        res = dialog.run()
        selected_path = ""
        if res == Gtk.ResponseType.OK:
            selected_path = dialog.get_filename() or ""
        dialog.destroy()
        while Gtk.events_pending():
            Gtk.main_iteration()
            
        if selected_path:
            return {"ok": True, "path": selected_path}
        return {"ok": False, "cancelled": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}

# ── Local Encryption & Vault Security ──────────────────────────────────────────

def get_machine_vault_key() -> str:
    machine_id = ""
    for mid_path in ["/etc/machine-id", "/var/lib/dbus/machine-id"]:
        if os.path.exists(mid_path):
            try:
                machine_id = Path(mid_path).read_text().strip()
                break
            except Exception:
                pass
    user_seed = f"{os.getuid()}:{os.environ.get('USER', 'user')}:{machine_id}"
    return hashlib.sha256(user_seed.encode("utf-8")).hexdigest()

def secure_file_permissions(filepath: Path) -> None:
    if filepath.exists():
        try:
            os.chmod(filepath, 0o600)
        except Exception:
            pass

def update_encrypted_vault() -> bool:
    payload = {
        "timestamp": int(time.time()),
        "memories": get_memories().get("memories", []),
        "history": load_json_file(HISTORY_FILE, {})
    }
    raw_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    key = get_machine_vault_key()
    
    try:
        enc_cmd = ["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-iter", "10000", "-pass", f"pass:{key}"]
        proc = subprocess.run(enc_cmd, input=raw_bytes, capture_output=True)
        if proc.returncode == 0 and proc.stdout:
            VAULT_FILE.write_bytes(proc.stdout)
            secure_file_permissions(VAULT_FILE)
            return True
    except Exception:
        pass
    return False

def get_vault_security_info() -> Dict[str, Any]:
    has_vault = VAULT_FILE.exists()
    vault_size = VAULT_FILE.stat().st_size if has_vault else 0
    return {
        "ok": True,
        "encryption_enabled": True,
        "cipher": "AES-256-CBC (PBKDF2 10,000 iter)",
        "key_derivation": "Hardware-Bound (Machine-ID + User UID)",
        "file_permissions": "POSIX 0600 / 0700 (Owner Only)",
        "vault_path": str(VAULT_FILE),
        "vault_size_bytes": vault_size,
        "last_encrypted": int(VAULT_FILE.stat().st_mtime) if has_vault else int(time.time())
    }

# ── File Attachment & Context Inspection ───────────────────────────────────────

def inspect_file(filepath: str) -> Dict[str, Any]:
    p = Path(filepath).expanduser().resolve()
    if not p.exists():
        return {"ok": False, "error": f"File not found: {filepath}"}

    size_bytes = p.stat().st_size
    size_str = f"{size_bytes} B"
    if size_bytes > 1024 * 1024:
        size_str = f"{size_bytes / (1024 * 1024):.1f} MB"
    elif size_bytes > 1024:
        size_str = f"{size_bytes / 1024:.1f} KB"

    ext = p.suffix.lower().lstrip(".")
    mime, _ = mimetypes.guess_type(str(p))
    mime = mime or "application/octet-stream"

    image_exts = {"png", "jpg", "jpeg", "webp", "gif", "bmp", "svg", "ico"}
    code_exts = {
        "py", "js", "ts", "jsx", "tsx", "rs", "c", "cpp", "h", "hpp", "go",
        "java", "sh", "bash", "zsh", "lua", "toml", "yaml", "yml", "json",
        "md", "txt", "csv", "html", "css", "xml", "sql", "qml", "ini", "conf"
    }
    doc_exts = {"pdf", "docx", "doc", "odt", "rtf", "xlsx", "pptx"}

    if ext in image_exts:
        category = "image"
        icon = "󰋩"
    elif ext in code_exts or mime.startswith("text/"):
        category = "code"
        icon = "󰈙"
    elif ext in doc_exts or "pdf" in mime:
        category = "document"
        icon = "󰈦"
    else:
        category = "file"
        icon = "󰈔"

    text_content = ""
    if category == "code" or mime.startswith("text/"):
        try:
            text_content = p.read_text(encoding="utf-8", errors="replace")[:120000]
        except Exception:
            pass

    return {
        "ok": True,
        "path": str(p),
        "filename": p.name,
        "extension": ext,
        "size_bytes": size_bytes,
        "size_str": size_str,
        "category": category,
        "icon": icon,
        "is_image": category == "image",
        "has_text_content": bool(text_content),
        "text_preview": text_content
    }

# ── Agent Engines & Models ────────────────────────────────────────────────────

def get_active_engine() -> str:
    cfg = load_json_file(CONFIG_FILE, {"agent_engine": "hermes"})
    return cfg.get("agent_engine", "hermes")

def set_active_engine(engine: str) -> Dict[str, Any]:
    valid = ["hermes", "omp", "claude", "codex"]
    if engine not in valid:
        return {"ok": False, "error": f"Invalid engine '{engine}'. Valid options: {valid}"}
    cfg = load_json_file(CONFIG_FILE, {"agent_engine": "hermes"})
    cfg["agent_engine"] = engine
    save_json_file(CONFIG_FILE, cfg)
    
    current_model = get_active_model_for_engine(engine)
    set_status(get_status().get("state", "idle"), headline=f"Agent: {engine.upper()} ({current_model.get('model', '')})")
    return {"ok": True, "active_engine": engine, "active_model": current_model.get("model", ""), "active_provider": current_model.get("provider", "")}

def get_agent_engines() -> Dict[str, Any]:
    current_engine = get_active_engine()
    engines = [
        {
            "id": "hermes",
            "name": "Hermes (Botty)",
            "desc": "Persistent desktop assistant with screen awareness, memory, skills & multi-model support",
            "icon": "🐼",
            "available": bool(shutil.which("hermes"))
        },
        {
            "id": "omp",
            "name": "OMP (Oh My Pi)",
            "desc": "High-speed parallel coding harness and orchestrator",
            "icon": "󰘦",
            "available": bool(shutil.which("omp"))
        },
        {
            "id": "claude",
            "name": "Claude Code",
            "desc": "Anthropic Claude coding and terminal assistant",
            "icon": "󰚩",
            "available": bool(shutil.which("claude"))
        },
        {
            "id": "codex",
            "name": "OpenAI Codex",
            "desc": "OpenAI coding and shell automation assistant",
            "icon": "󰚩",
            "available": bool(shutil.which("codex"))
        }
    ]
    return {
        "ok": True,
        "active_engine": current_engine,
        "engines": engines
    }

def get_active_model_for_engine(engine: Optional[str] = None) -> Dict[str, str]:
    eng = engine or get_active_engine()
    cfg = load_json_file(CONFIG_FILE, {})
    engine_models = cfg.get("engine_models", {})

    if eng == "hermes":
        if not HERMES_CONFIG_FILE.exists():
            return {"model": "ox-alpha-free", "provider": "opencode-go"}
        try:
            with open(HERMES_CONFIG_FILE, "r", encoding="utf-8") as f:
                content = f.read()
            model_match = re.search(r"model:\s*\n\s*default:\s*([^\n]+)", content)
            provider_match = re.search(r"model:\s*\n(?:[^\n]+\n)*?\s*provider:\s*([^\n]+)", content)
            model = model_match.group(1).strip() if model_match else "ox-alpha-free"
            provider = provider_match.group(1).strip() if provider_match else "opencode-go"
            model = model.strip("'\"")
            provider = provider.strip("'\"")
            return {"model": model, "provider": provider}
        except Exception:
            return {"model": "ox-alpha-free", "provider": "opencode-go"}
    elif eng == "omp":
        # Check ~/.omp/agent/config.yml directly for ground truth
        if OMP_CONFIG_FILE.exists():
            try:
                text = OMP_CONFIG_FILE.read_text(encoding="utf-8")
                m = re.search(r"default:\s*([^\n]+)", text)
                if m:
                    active_m = m.group(1).strip().strip("'\"")
                    prov = active_m.split("/")[0] if "/" in active_m else "google-antigravity"
                    return {"model": active_m, "provider": prov}
            except Exception:
                pass
        return engine_models.get("omp", {"model": "google-antigravity/gemini-3.7-flash", "provider": "google-antigravity"})
    elif eng == "claude":
        return engine_models.get("claude", {"model": "claude-3-7-sonnet", "provider": "anthropic"})
    elif eng == "codex":
        return engine_models.get("codex", {"model": "gpt-4o", "provider": "openai"})
    
    return {"model": "default", "provider": "auto"}

def get_status() -> Dict[str, Any]:
    active_eng = get_active_engine()
    active_m = get_active_model_for_engine(active_eng)
    default_status = {
        "ok": True,
        "state": "idle",
        "headline": "Ready",
        "last_query": "",
        "last_answer": "",
        "last_error": "",
        "active_engine": active_eng,
        "active_model": active_m.get("model", "ox-alpha-free"),
        "active_provider": active_m.get("provider", "opencode-go"),
        "session_turns": 0,
        "memory_count": count_memories(),
        "skills_count": count_skills(),
        "timestamp": int(time.time()),
        "has_active_work": False,
        "is_recording": VOICE_PID_FILE.exists()
    }
    status = load_json_file(STATUS_FILE, default_status)
    status["active_engine"] = active_eng
    status["active_model"] = active_m.get("model", "ox-alpha-free")
    status["active_provider"] = active_m.get("provider", "opencode-go")
    status["is_recording"] = VOICE_PID_FILE.exists()
    
    if LOCK_FILE.exists():
        try:
            pid = int(LOCK_FILE.read_text().strip())
            os.kill(pid, 0)
            status["state"] = "working"
            status["has_active_work"] = True
            status["headline"] = "Thinking…"
        except (ValueError, OSError):
            LOCK_FILE.unlink(missing_ok=True)
            if status.get("state") == "working":
                status["state"] = "idle"
                status["has_active_work"] = False
                status["headline"] = "Ready"
                save_json_file(STATUS_FILE, status)
    return status

def set_status(state: str, headline: str = "", last_query: str = "", last_answer: str = "", last_error: str = "") -> None:
    current = get_status()
    current["state"] = state
    current["has_active_work"] = (state == "working")
    if headline:
        current["headline"] = headline
    if last_query:
        current["last_query"] = last_query
    if last_answer:
        current["last_answer"] = last_answer
    if last_error:
        current["last_error"] = last_error
    current["timestamp"] = int(time.time())
    active_eng = get_active_engine()
    active_m = get_active_model_for_engine(active_eng)
    current["active_engine"] = active_eng
    current["active_model"] = active_m.get("model", "")
    current["active_provider"] = active_m.get("provider", "")
    current["memory_count"] = count_memories()
    current["skills_count"] = count_skills()
    current["is_recording"] = VOICE_PID_FILE.exists()
    save_json_file(STATUS_FILE, current)

def count_memories() -> int:
    mem_file = HERMES_MEMORY_DIR / "MEMORY.md"
    user_file = HERMES_MEMORY_DIR / "USER.md"
    count = 0
    for f in [mem_file, user_file]:
        if f.exists():
            secure_file_permissions(f)
            try:
                content = f.read_text(encoding="utf-8")
                entries = [e.strip() for e in content.split("§") if e.strip()]
                count += len(entries)
            except Exception:
                pass
    return count

def count_skills() -> int:
    count = 0
    if HERMES_SKILLS_DIR.exists():
        count += len([d for d in HERMES_SKILLS_DIR.iterdir() if d.is_dir() and (d / "SKILL.md").exists()])
    return count

def append_botty_log(entry: str) -> None:
    try:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(BOTTY_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {entry}\n")
        secure_file_permissions(BOTTY_LOG_FILE)
    except Exception:
        pass

def get_botty_logs(max_lines: int = 250) -> Dict[str, Any]:
    raw_lines = []
    if BOTTY_LOG_FILE.exists():
        try:
            text = BOTTY_LOG_FILE.read_text(encoding="utf-8", errors="replace")
            lines = [l for l in text.splitlines() if l.strip()]
            raw_lines = lines[-max_lines:]
        except Exception:
            pass

    history = load_json_file(HISTORY_FILE, {"messages": []})
    messages = history.get("messages", [])
    last_assistant_raw = ""
    last_assistant_model = ""
    last_assistant_engine = ""
    for m in reversed(messages):
        if m.get("role") == "assistant":
            last_assistant_raw = m.get("raw_output") or m.get("content", "")
            last_assistant_model = m.get("model", "")
            last_assistant_engine = m.get("engine", "")
            break

    return {
        "ok": True,
        "logs": "\n".join(raw_lines),
        "log_count": len(raw_lines),
        "last_assistant_raw": last_assistant_raw,
        "last_assistant_model": last_assistant_model,
        "last_assistant_engine": last_assistant_engine,
        "log_path": str(BOTTY_LOG_FILE)
    }

def clear_botty_logs() -> Dict[str, Any]:
    try:
        if BOTTY_LOG_FILE.exists():
            BOTTY_LOG_FILE.unlink()
        return {"ok": True, "message": "Logs cleared"}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def hypr_ipc_query(cmd: str) -> Any:
    """Fast direct Unix domain socket query to Hyprland IPC socket with CLI fallback."""
    xdg_runtime = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    his = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE", "")
    sock_path = f"{xdg_runtime}/hypr/{his}/.socket.sock" if his else ""
    
    if sock_path and os.path.exists(sock_path):
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.settimeout(1.0)
            s.connect(sock_path)
            s.sendall(cmd.encode("utf-8"))
            buf = []
            while True:
                chunk = s.recv(8192)
                if not chunk:
                    break
                buf.append(chunk)
            s.close()
            raw = b"".join(buf).decode("utf-8", errors="replace")
            return json.loads(raw)
        except Exception:
            pass

    # Fallback to hyprctl CLI
    try:
        cli_cmd = cmd.lstrip("j/").split()
        res = subprocess.run(["hyprctl", *cli_cmd, "-j"], capture_output=True, text=True, timeout=2.0)
        if res.returncode == 0 and res.stdout.strip():
            return json.loads(res.stdout)
    except Exception:
        pass
    return {}

def get_targeted_situation_context() -> Dict[str, Any]:
    """Derives visible tools & environment on the active workspace in <10ms without taking a screenshot."""
    start_t = time.perf_counter()
    active_win = hypr_ipc_query("j/activewindow") or {}
    active_ws = hypr_ipc_query("j/activeworkspace") or {}
    clients = hypr_ipc_query("j/clients") or []

    ws_id = active_ws.get("id")
    active_addr = active_win.get("address", "")
    
    visible = []
    if isinstance(clients, list):
        for c in clients:
            if not isinstance(c, dict):
                continue
            c_ws = c.get("workspace", {})
            c_ws_id = c_ws.get("id") if isinstance(c_ws, dict) else c_ws
            if (c_ws_id == ws_id or ws_id is None) and c.get("mapped") and not c.get("hidden"):
                if c.get("class") != "meviusisback.botty":
                    visible.append(c)

    terminal_classes = {"foot", "kitty", "alacritty", "ghostty", "xterm", "gnome-terminal", "wezterm", "urxvt", "st", "terminator", "konsole"}
    editor_classes = {"code", "code-oss", "vscodium", "cursor", "zed", "sublime_text", "neovim", "helix", "emacs"}
    
    tools_list = []
    primary_cwd = ""
    primary_git: Dict[str, Any] = {}
    primary_app = active_win.get("class", "") or "Desktop"
    primary_title = active_win.get("title", "")

    for c in visible:
        cls = c.get("class", "")
        title = c.get("title", "")
        pid = c.get("pid")
        is_active = (c.get("address") == active_addr) or (pid and pid == active_win.get("pid"))
        
        tool_info: Dict[str, Any] = {
            "class": cls,
            "title": title,
            "pid": pid,
            "is_active": is_active,
            "category": "app",
            "cwd": "",
            "cmd": "",
            "git": {}
        }
        
        if cls.lower() in terminal_classes and pid:
            tool_info["category"] = "terminal"
            cwd = ""
            cmd = ""
            if os.path.exists(f"/proc/{pid}"):
                try:
                    cur_pid = pid
                    for _ in range(5):
                        try:
                            children_raw = Path(f"/proc/{cur_pid}/task/{cur_pid}/children").read_text().strip()
                            if children_raw:
                                cur_pid = int(children_raw.split()[-1])
                            else:
                                break
                        except Exception:
                            break
                    cwd = os.readlink(f"/proc/{cur_pid}/cwd")
                    cmd = Path(f"/proc/{cur_pid}/cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()
                except Exception:
                    pass
            
            tool_info["cwd"] = cwd
            tool_info["cmd"] = cmd or title
            if is_active and cwd:
                primary_cwd = cwd

            # Check Git
            if cwd and (Path(cwd) / ".git").exists():
                try:
                    r_br = subprocess.run(["git", "-C", cwd, "branch", "--show-current"], capture_output=True, text=True, timeout=0.5)
                    r_st = subprocess.run(["git", "-C", cwd, "status", "--short"], capture_output=True, text=True, timeout=0.5)
                    br = r_br.stdout.strip() or "detached"
                    st_lines = [l for l in r_st.stdout.strip().splitlines() if l.strip()]
                    git_data = {
                        "branch": br,
                        "changed_count": len(st_lines),
                        "status_summary": ", ".join(st_lines[:5]) + ("..." if len(st_lines) > 5 else "")
                    }
                    tool_info["git"] = git_data
                    if is_active:
                        primary_git = git_data
                except Exception:
                    pass
                    
        elif cls.lower() in editor_classes and pid:
            tool_info["category"] = "editor"
            cwd = ""
            if os.path.exists(f"/proc/{pid}/cwd"):
                try:
                    cwd = os.readlink(f"/proc/{pid}/cwd")
                except Exception:
                    pass
            tool_info["cwd"] = cwd
            if is_active and cwd:
                primary_cwd = cwd
        elif "browser" in cls.lower() or cls.lower() in {"chromium", "google-chrome", "firefox", "zen", "brave-browser"}:
            tool_info["category"] = "browser"
            
        tools_list.append(tool_info)

    # Active selection (mouse highlight)
    selection = ""
    try:
        r_sel = subprocess.run(["wl-paste", "--primary"], capture_output=True, text=True, timeout=0.3)
        if r_sel.returncode == 0 and r_sel.stdout.strip():
            sel_text = r_sel.stdout.strip()
            if len(sel_text) > 400:
                sel_text = sel_text[:400] + "… [truncated]"
            selection = sel_text
    except Exception:
        pass

    elapsed_ms = int((time.perf_counter() - start_t) * 1000)

    return {
        "ok": True,
        "active_workspace": ws_id,
        "active_app": primary_app,
        "active_title": primary_title,
        "primary_cwd": primary_cwd,
        "primary_git": primary_git,
        "visible_tools": tools_list,
        "selection": selection,
        "elapsed_ms": elapsed_ms,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
    }

def format_situation_prompt(ctx: Dict[str, Any]) -> str:
    if not ctx or not ctx.get("ok"):
        return ""
    
    lines = ["[SCREEN TOOLS & SITUATION CONTEXT]"]
    tools = ctx.get("visible_tools", [])
    if tools:
        for t in tools:
            prefix = "[ACTIVE] " if t.get("is_active") else ""
            cat = t.get("category", "app").capitalize()
            cls = t.get("class", "App")
            title = t.get("title", "")
            cwd = t.get("cwd", "")
            if cwd:
                short_cwd = cwd.replace(str(Path.home()), "~")
            else:
                short_cwd = ""
            
            git = t.get("git", {})
            git_str = ""
            if git:
                br = git.get("branch", "")
                cc = git.get("changed_count", 0)
                git_str = f" (git: {br}" + (f", {cc} changed" if cc else ", clean") + ")"
                
            cmd = t.get("cmd", "")
            
            if cat == "Terminal":
                lines.append(f"- {prefix}Terminal ({cls}): cwd=\"{short_cwd}\"{git_str} | cmd=\"{cmd or title}\"")
            elif cat == "Editor":
                loc = f" in \"{short_cwd}\"" if short_cwd else ""
                lines.append(f"- {prefix}Editor ({cls}): \"{title}\"{loc}")
            elif cat == "Browser":
                lines.append(f"- {prefix}Browser ({cls}): \"{title}\"")
            else:
                lines.append(f"- {prefix}{cls}: \"{title}\"")
    else:
        app = ctx.get("active_app", "Desktop")
        tit = ctx.get("active_title", "")
        lines.append(f"- Active Window: {app} — \"{tit}\"")

    selection = ctx.get("selection", "")
    if selection:
        lines.append(f"- Active Mouse Highlighted Text: \"{selection}\"")
        
    lines.append("[END CONTEXT]")
    return "\n".join(lines)

def capture_screen(mode: str = "activewindow") -> Dict[str, Any]:
    timestamp = int(time.time() * 1000)
    capture_path = SCREENSHOT_DIR / f"capture_{timestamp}.png"
    
    active_win_info: Dict[str, Any] = {}
    geometry = None

    try:
        res = subprocess.run(["hyprctl", "activewindow", "-j"], capture_output=True, text=True, timeout=3)
        if res.returncode == 0 and res.stdout.strip():
            active_win_info = json.loads(res.stdout)
    except Exception:
        pass

    if mode == "activewindow" and active_win_info and "at" in active_win_info and "size" in active_win_info:
        at = active_win_info["at"]
        size = active_win_info["size"]
        if size[0] > 0 and size[1] > 0:
            geometry = f"{at[0]},{at[1]} {size[0]}x{size[1]}"

    try:
        cmd = ["grim"]
        if geometry:
            cmd.extend(["-g", geometry])
        cmd.append(str(capture_path))
        capture_res = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if capture_res.returncode != 0 or not capture_path.exists():
            subprocess.run(["grim", str(capture_path)], capture_output=True, text=True, timeout=5)
    except Exception as e:
        return {"ok": False, "error": f"Grim capture failed: {str(e)}"}

    if not capture_path.exists():
        return {"ok": False, "error": "Screenshot file was not generated."}

    secure_file_permissions(capture_path)

    return {
        "ok": True,
        "image_path": str(capture_path),
        "filename": capture_path.name,
        "mode": mode,
        "active_window": {
            "title": active_win_info.get("title", ""),
            "class": active_win_info.get("class", ""),
            "initialTitle": active_win_info.get("initialTitle", ""),
            "pid": active_win_info.get("pid", 0),
            "geometry": geometry or "fullscreen"
        }
    }

def reset_hermes_session(session_name: str = "botty-widget") -> Dict[str, Any]:
    """
    Safely reset/archive the Hermes profile SQLite session for session_name.
    Renames the session title to archived-botty-widget-<timestamp> so that
    Hermes will create a fresh, clean session on the next --continue botty-widget --create-if-missing,
    avoiding 100k+ token context bloat while keeping SQLite historical records intact.
    """
    if not HERMES_STATE_DB.exists():
        return {"ok": True, "reset": False, "reason": "No state.db"}
    try:
        import sqlite3
        con = sqlite3.connect(str(HERMES_STATE_DB), timeout=5.0)
        cur = con.cursor()
        now_ts = int(time.time())
        cur.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='sessions'")
        if cur.fetchone()[0] > 0:
            cur.execute("SELECT id FROM sessions WHERE title = ? OR id = ?", (session_name, session_name))
            rows = cur.fetchall()
            if rows:
                new_title = f"archived-{session_name}-{now_ts}"
                cur.execute("UPDATE sessions SET title = ? WHERE title = ? OR id = ?", (new_title, session_name, session_name))
                con.commit()
                con.close()
                return {"ok": True, "reset": True, "archived_sessions": [r[0] for r in rows], "new_title": new_title}
        con.close()
        return {"ok": True, "reset": False, "reason": "No active session matching title"}
    except Exception as e:
        return {"ok": False, "error": f"Failed to reset Hermes session: {str(e)}"}

def get_history() -> Dict[str, Any]:
    default_history = {
        "session_id": "botty-widget",
        "messages": []
    }
    history = load_json_file(HISTORY_FILE, default_history)
    return {"ok": True, "history": history}

def clear_history() -> Dict[str, Any]:
    history = {
        "session_id": "botty-widget",
        "messages": []
    }
    save_json_file(HISTORY_FILE, history)
    reset_hermes_session("botty-widget")
    set_status("idle", headline="Ready", last_query="", last_answer="", last_error="")
    return {"ok": True, "message": "History cleared and session reset"}

def add_history_message(role: str, content: str, attachments: Optional[List[Dict[str, Any]]] = None, actions: Optional[List[Dict[str, Any]]] = None, model: Optional[str] = None, engine: Optional[str] = None, raw_output: Optional[str] = None) -> None:
    history = load_json_file(HISTORY_FILE, {"session_id": "botty-widget", "messages": []})
    msgs = history.get("messages", [])

    safe_content = (content or "").strip()
    if not safe_content and not attachments and not actions:
        safe_content = "✓ Done." if role == "assistant" else "(empty)"
    elif not safe_content and (attachments or actions):
        safe_content = ""

    if msgs:
        last = msgs[-1]
        if last.get("role") == role and last.get("content") == safe_content:
            if abs(int(time.time()) - last.get("timestamp", 0)) < 10:
                return

    msg = {
        "id": f"msg_{int(time.time()*1000)}_{len(msgs)}",
        "role": role,
        "content": safe_content,
        "timestamp": int(time.time()),
        "attachments": attachments or [],
        "actions": actions or [],
        "model": model or "",
        "engine": engine or "",
        "raw_output": raw_output or ""
    }
    msgs.append(msg)
    history["messages"] = msgs
    save_json_file(HISTORY_FILE, history)

def ask(query: str, image_path: Optional[str] = None, file_path: Optional[str] = None, screen_context: bool = False, situation_context: bool = False, model: Optional[str] = None, provider: Optional[str] = None) -> Dict[str, Any]:
    if not query.strip() and not image_path and not file_path and not screen_context and not situation_context:
        return {"ok": False, "error": "Empty query and no attachment or context provided."}

    if LOCK_FILE.exists():
        try:
            pid = int(LOCK_FILE.read_text().strip())
            os.kill(pid, 0)
            return {"ok": False, "error": "Botty is already processing a request."}
        except (ValueError, OSError):
            LOCK_FILE.unlink(missing_ok=True)

    set_status("working", headline="Botty is thinking…", last_query=query)
    LOCK_FILE.write_text(str(os.getpid()))

    captured_context: Optional[Dict[str, Any]] = None
    situation_data: Optional[Dict[str, Any]] = None
    target_image = image_path
    attached_file_info: Optional[Dict[str, Any]] = None

    if situation_context:
        sit = get_targeted_situation_context()
        if sit.get("ok"):
            situation_data = sit

    if screen_context:
        cap = capture_screen(mode="activewindow")
        if cap.get("ok"):
            captured_context = cap
            if not target_image:
                target_image = cap.get("image_path")

    if file_path and os.path.exists(file_path):
        finfo = inspect_file(file_path)
        if finfo.get("ok"):
            attached_file_info = finfo
            if finfo.get("is_image") and not target_image:
                target_image = finfo["path"]

    attachments: List[Dict[str, Any]] = []

    if situation_data:
        attachments.append({
            "type": "situation",
            "app_name": situation_data.get("active_app", "Desktop"),
            "window_title": situation_data.get("active_title", ""),
            "cwd": situation_data.get("primary_cwd", ""),
            "git_branch": situation_data.get("primary_git", {}).get("branch", ""),
            "git_changed": situation_data.get("primary_git", {}).get("changed_count", 0),
            "visible_tools_count": len(situation_data.get("visible_tools", [])),
            "is_screen_capture": False
        })

    if target_image and os.path.exists(target_image):
        win_title = ""
        win_class = ""
        if captured_context:
            win = captured_context.get("active_window", {})
            win_title = win.get("title", "")
            win_class = win.get("class", "")
        attachments.append({
            "type": "image",
            "path": target_image,
            "filename": os.path.basename(target_image),
            "is_screen_capture": bool(screen_context),
            "app_name": win_class or "Active Window",
            "window_title": win_title
        })

    if attached_file_info and not attached_file_info.get("is_image"):
        attachments.append({
            "type": attached_file_info.get("category", "file"),
            "path": attached_file_info["path"],
            "filename": attached_file_info["filename"],
            "size_str": attached_file_info["size_str"],
            "icon": attached_file_info["icon"],
            "is_screen_capture": False
        })

    BOTTY_AGENT_DIRECTIVE = (
        "You are acting as Botty, the friendly and capable AI desktop agent on this Omarchy Linux workstation. "
        "You have full local access to the machine environment: terminal execution, local files, system CLI tools, native IPC APIs, and installed skills. "
        "When (optional) screen context, situational tools, or window information is provided, use it to understand the user's workspace state and what needs to be done. "
        "You are fully capable of executing tasks directly on this machine to assist the user. "
        "Do NOT attempt X11 GUI clicks, xdotool, or synthetic mouse automation. "
        "Instead, operate natively in the terminal using your CLI tools, shell commands, native IPC APIs (such as herdr for driving agent TUIs, hyprctl for window/workspace management, or system utilities), and installed skills to execute tasks directly on this machine.\n\n"
        "COMMUNICATION & ANSWER GUIDELINES (CRITICAL):\n"
        "- When you apply changes to the computer or execute actions, and in general for all answers: your response must be non-technical, human-friendly, and concise.\n"
        "- Clearly describe exactly what you did and what changes were made in simple, plain human language (e.g. 'I updated the volume settings and restarted the audio service.').\n"
        "- Avoid technical jargon, raw command dumps, internal monologue, or unnecessary technical minutiae unless the user specifically asks for technical details or code.\n"
        "- Execute actions cleanly and confirm what was accomplished."
    )

    prompt_parts = []

    if situation_data:
        sit_prompt = format_situation_prompt(situation_data)
        if sit_prompt:
            prompt_parts.append(sit_prompt + "\n")

    if captured_context or (screen_context and target_image):
        win = captured_context.get("active_window", {}) if captured_context else {}
        win_title = win.get("title", "")
        win_class = win.get("class", "")
        win_info = f"{win_class} — '{win_title}'" if (win_class or win_title) else "Active Workspace / Screen"
        img_info = f" (Screenshot image: {target_image})" if target_image else ""
        prompt_parts.append(
            f"[SCREEN CONTEXT ATTACHED: {win_info}{img_info}]\n"
            f"Note for Agent: You have access to this screen context and full local capabilities to perform tasks on this machine. "
            f"Use the visual/screen state to understand what is on screen and what needs to be done.\n"
            f"[END SCREEN CONTEXT]\n"
        )

    if attached_file_info and not attached_file_info.get("is_image"):
        fname = attached_file_info["filename"]
        fpath = attached_file_info["path"]
        fext = attached_file_info["extension"]
        if attached_file_info.get("has_text_content"):
            preview = attached_file_info["text_preview"]
            prompt_parts.append(f"[ATTACHED FILE: {fname} (Path: {fpath})]\n```{fext}\n{preview}\n```\n[END ATTACHED FILE]\n")
        else:
            prompt_parts.append(f"[ATTACHED DOCUMENT: {fname} (Path: {fpath}, Size: {attached_file_info['size_str']})]\nPlease inspect and work with this file using your terminal and file tools.\n[END ATTACHED DOCUMENT]\n")

    user_query = query.strip() if query.strip() else ("Analyze the attached file." if attached_file_info else ("Analyze the current desktop situation and active tools." if situation_data else "Analyze the attached screenshot context."))
    prompt_parts.append(user_query)
    final_prompt = "\n".join(prompt_parts)

    add_history_message("user", user_query, attachments=attachments)

    engine = get_active_engine()
    active_m = get_active_model_for_engine(engine)
    selected_model = model or active_m.get("model", "")

    query_tmp = BOTTY_DATA_DIR / "current_query.txt"
    hermes_prompt = f"[SYSTEM DIRECTIVE]\n{BOTTY_AGENT_DIRECTIVE}\n[END SYSTEM DIRECTIVE]\n\n{final_prompt}"
    query_tmp.write_text(hermes_prompt, encoding="utf-8")

    t_start = time.perf_counter()
    append_botty_log(f"QUERY [{engine}/{selected_model}]: {user_query}")

    cmd = []

    if engine == "omp":
        cmd = ["omp", "-p", "--allow-home", f"--append-system-prompt={BOTTY_AGENT_DIRECTIVE}"]
        if selected_model:
            cmd.extend(["--model", selected_model])
        cmd.append(final_prompt)
    elif engine == "claude":
        cmd = ["claude", "-p", "--append-system-prompt", BOTTY_AGENT_DIRECTIVE]
        if selected_model:
            cmd.extend(["--model", selected_model])
        cmd.append(final_prompt)
    elif engine == "codex":
        cmd = ["codex", "exec"]
        if selected_model:
            cmd.extend(["--model", selected_model])
        codex_prompt = f"[SYSTEM DIRECTIVE]\n{BOTTY_AGENT_DIRECTIVE}\n[END SYSTEM DIRECTIVE]\n\n{final_prompt}"
        cmd.append(codex_prompt)
    else:
        cmd = [
            "hermes",
            "-p", "botty",
            "chat",
            "-Q",
            "--query-file", str(query_tmp),
            "--continue", "botty-widget",
            "--create-if-missing",
            "--max-turns", "15",
            "--run-budget", "200",
            "--yolo"
        ]
        if target_image and os.path.exists(target_image):
            cmd.extend(["--image", target_image])
        if selected_model:
            cmd.extend(["-m", selected_model])
        if provider:
            cmd.extend(["--provider", provider])

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1
        )
        
        stdout_data, stderr_data = proc.communicate(timeout=240)
        cleaned_response = strip_reasoning(stdout_data)
        t_duration = time.perf_counter() - t_start

        if proc.returncode != 0 and not cleaned_response:
            err_msg = stderr_data.strip() or f"Agent process exited with code {proc.returncode}"
            set_status("error", headline="Error", last_error=err_msg)
            append_botty_log(f"ERROR [{engine}/{selected_model}] (Code {proc.returncode}): {err_msg}")
            add_history_message("assistant", f"⚠️ Error: {err_msg}", model=selected_model, engine=engine, raw_output=redact_secrets(stderr_data or stdout_data))
            return {"ok": False, "error": err_msg}

        if not cleaned_response:
            raw_stripped = stdout_data.strip()
            cleaned_response = raw_stripped if raw_stripped else "✓ Done."
        actions = []
        for line in stdout_data.splitlines():
            if line.startswith("session_id:"):
                continue
            if "Saved memory" in line or "Saved to memory" in line:
                actions.append({"type": "memory", "text": line.strip()})
            elif "Created skill" in line or "Skill installed" in line:
                actions.append({"type": "skill", "text": line.strip()})

        raw_output_saved = redact_secrets(stdout_data)
        append_botty_log(f"RESPONSE [{engine}/{selected_model}] ({t_duration:.1f}s): {cleaned_response[:140]}...")
        add_history_message("assistant", cleaned_response, actions=actions, model=selected_model, engine=engine, raw_output=raw_output_saved)
        set_status("idle", headline="Ready", last_query=query, last_answer=cleaned_response)

        try:
            update_encrypted_vault()
        except Exception:
            pass

        # Trigger auto-compaction if context reaches threshold
        try:
            curr_h = load_json_file(HISTORY_FILE, {"messages": []})
            if len(curr_h.get("messages", [])) >= get_auto_compact_threshold():
                distill_and_compact_session()
        except Exception as e:
            append_botty_log(f"Auto-compaction trigger error: {str(e)}")

        return {
            "ok": True,
            "response": cleaned_response,
            "raw_output": raw_output_saved,
            "actions": actions,
            "attachments": attachments
        }

    except subprocess.TimeoutExpired:
        if 'proc' in locals():
            proc.kill()
        err = "Request timed out after 240 seconds."
        set_status("error", headline="Timeout", last_error=err)
        append_botty_log(f"TIMEOUT [{engine}/{selected_model}]: {err}")
        add_history_message("assistant", f"⚠️ {err}", model=selected_model, engine=engine, raw_output=err)
        return {"ok": False, "error": err}
    except Exception as e:
        err = f"Execution error: {str(e)}"
        set_status("error", headline="Error", last_error=err)
        append_botty_log(f"EXCEPTION [{engine}/{selected_model}]: {err}")
        add_history_message("assistant", f"⚠️ {err}", model=selected_model, engine=engine, raw_output=err)
        return {"ok": False, "error": err}
    finally:
        LOCK_FILE.unlink(missing_ok=True)
        query_tmp.unlink(missing_ok=True)

# ── Dynamic Per-Engine Models & Providers Discovery ────────────────────────────

def get_dynamic_engine_models(engine_name: Optional[str] = None) -> Dict[str, Any]:
    """Returns dynamic provider and model choices checked strictly against each agent's real config."""
    engine = engine_name or get_active_engine()
    active_m = get_active_model_for_engine(engine)

    if engine == "hermes":
        # Check Hermes .env and config.yaml
        env_files = [HERMES_BOTTY_DIR / ".env", HERMES_DIR / ".env"]
        env_vars: Dict[str, str] = {}
        for ef in env_files:
            if ef.exists():
                for line in ef.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        v = v.strip().strip("\"'")
                        if len(v) > 3 and not v.startswith("REDACTED"):
                            env_vars[k.strip()] = v

        active_provider_ids = set()
        if "OPENCODE_GO_API_KEY" in env_vars:
            active_provider_ids.add("opencode-go")
            active_provider_ids.add("opencode-free")
        if "OPENCODE_ZEN_API_KEY" in env_vars:
            active_provider_ids.add("opencode-zen")
        if "OPENROUTER_API_KEY" in env_vars:
            active_provider_ids.add("openrouter")
        if "ANTHROPIC_API_KEY" in env_vars:
            active_provider_ids.add("anthropic")
        if "OPENAI_API_KEY" in env_vars:
            active_provider_ids.add("openai")
        if "GEMINI_API_KEY" in env_vars or "GOOGLE_API_KEY" in env_vars:
            active_provider_ids.add("google")
        if "GLM_API_KEY" in env_vars:
            active_provider_ids.add("z.ai")
        if "KIMI_API_KEY" in env_vars:
            active_provider_ids.add("kimi")
        if "MINIMAX_API_KEY" in env_vars:
            active_provider_ids.add("minimax")
        if "GROQ_API_KEY" in env_vars:
            active_provider_ids.add("groq")
        if "NOVITA_API_KEY" in env_vars:
            active_provider_ids.add("novita")
        if "FIREWORKS_API_KEY" in env_vars:
            active_provider_ids.add("fireworks")
        if "DEEPINFRA_API_KEY" in env_vars:
            active_provider_ids.add("deepinfra")

        if HERMES_CONFIG_FILE.exists():
            try:
                cfg_text = HERMES_CONFIG_FILE.read_text(encoding="utf-8")
                if "ollama:" in cfg_text:
                    active_provider_ids.add("ollama")
            except Exception:
                pass

        p_cache_file = HERMES_DIR / "provider_models_cache.json"
        p_data: Dict[str, Any] = {}
        if p_cache_file.exists():
            try:
                with open(p_cache_file, "r", encoding="utf-8") as f:
                    p_data = json.load(f)
            except Exception:
                pass

        if active_m.get("provider"):
            active_provider_ids.add(active_m["provider"])

        providers_dict: Dict[str, Dict[str, Any]] = {}

        for p_id in active_provider_ids:
            p_name = p_id.replace("-", " ").title()
            models_list = []

            if p_id in p_data:
                for m in p_data[p_id].get("models", []):
                    name = m.split("/")[-1].replace("-", " ").title() if "/" in m else m
                    models_list.append({"id": m, "name": name})

            if p_id == "opencode-go" and not models_list:
                models_list = [
                    {"id": "ox-alpha-free", "name": "OpenCode Alpha Free"},
                    {"id": "kimi-k2.5", "name": "Kimi K2.5"},
                    {"id": "glm-5", "name": "GLM-5"},
                    {"id": "minimax-m2.5", "name": "MiniMax M2.5"}
                ]
            elif p_id == "ollama" and not models_list:
                models_list = [
                    {"id": "ornith:latest", "name": "Ornith / Llama (Local)"},
                    {"id": "ornith:9b", "name": "Ornith 9B"}
                ]

            if models_list or p_id in active_provider_ids:
                providers_dict[p_id] = {
                    "id": p_id,
                    "name": p_name,
                    "models": models_list
                }

        sorted_providers = []
        priority = ["opencode-go", "openrouter", "opencode-free", "ollama", "google", "anthropic", "openai"]
        for p in priority:
            if p in providers_dict:
                sorted_providers.append(providers_dict.pop(p))
        for p in sorted(providers_dict.keys()):
            sorted_providers.append(providers_dict[p])

        return {
            "ok": True,
            "engine": "hermes",
            "active_engine": "hermes",
            "active_model": active_m.get("model", "ox-alpha-free"),
            "active_provider": active_m.get("provider", "opencode-go"),
            "providers": sorted_providers
        }

    elif engine == "omp":
        # Check ~/.omp/agent/config.yml and ~/.omp/agent/models.db directly
        omp_active_model = "google-antigravity/gemini-3.7-flash"
        omp_active_provider = "google-antigravity"

        if OMP_CONFIG_FILE.exists():
            try:
                text = OMP_CONFIG_FILE.read_text(encoding="utf-8")
                m = re.search(r"default:\s*([^\n]+)", text)
                if m:
                    omp_active_model = m.group(1).strip().strip("'\"")
                    if "/" in omp_active_model:
                        omp_active_provider = omp_active_model.split("/")[0]
            except Exception:
                pass

        omp_providers = []
        if OMP_MODELS_DB.exists():
            try:
                conn = sqlite3.connect(str(OMP_MODELS_DB))
                c = conn.cursor()
                c.execute("SELECT * FROM model_cache;")
                for r in c.fetchall():
                    p_id = r[0]
                    clean_pid = p_id.split(":")[0]
                    m_json = r[-1]
                    try:
                        m_data = json.loads(m_json)
                        models = []
                        for item in m_data:
                            m_id = item.get("id")
                            m_name = item.get("name", m_id)
                            if m_id:
                                full_id = f"{clean_pid}/{m_id}" if ("/" not in m_id and clean_pid not in ["ollama", "google-antigravity"]) else m_id
                                models.append({"id": full_id, "name": m_name})
                        if models:
                            p_name = clean_pid.replace("-", " ").title()
                            omp_providers.append({"id": clean_pid, "name": p_name, "models": models})
                    except Exception:
                        pass
            except Exception:
                pass

        if not omp_providers:
            omp_providers = [
                {
                    "id": "google-antigravity",
                    "name": "Google Antigravity",
                    "models": [
                        {"id": "google-antigravity/gemini-3.7-flash", "name": "Gemini 3.7 Flash"},
                        {"id": "claude-sonnet-4-5", "name": "Claude Sonnet 4.5"}
                    ]
                }
            ]

        return {
            "ok": True,
            "engine": "omp",
            "active_engine": "omp",
            "active_model": omp_active_model,
            "active_provider": omp_active_provider,
            "providers": omp_providers
        }

    elif engine == "claude":
        claude_providers = [
            {
                "id": "anthropic",
                "name": "Anthropic",
                "models": [
                    {"id": "claude-3-7-sonnet", "name": "Claude 3.7 Sonnet (Default)"},
                    {"id": "claude-3-5-sonnet", "name": "Claude 3.5 Sonnet"},
                    {"id": "claude-3-5-haiku", "name": "Claude 3.5 Haiku"},
                    {"id": "claude-3-opus", "name": "Claude 3 Opus"}
                ]
            }
        ]
        return {
            "ok": True,
            "engine": "claude",
            "active_engine": "claude",
            "active_model": active_m.get("model", "claude-3-7-sonnet"),
            "active_provider": active_m.get("provider", "anthropic"),
            "providers": claude_providers
        }

    elif engine == "codex":
        codex_providers = [
            {
                "id": "openai",
                "name": "OpenAI",
                "models": [
                    {"id": "gpt-4o", "name": "GPT-4o (Default)"},
                    {"id": "gpt-4o-mini", "name": "GPT-4o Mini"},
                    {"id": "o3-mini", "name": "o3-mini (Reasoning)"},
                    {"id": "o1", "name": "o1 (Reasoning)"},
                    {"id": "gpt-4-turbo", "name": "GPT-4 Turbo"}
                ]
            }
        ]
        return {
            "ok": True,
            "engine": "codex",
            "active_engine": "codex",
            "active_model": active_m.get("model", "gpt-4o"),
            "active_provider": active_m.get("provider", "openai"),
            "providers": codex_providers
        }

    return {"ok": False, "error": f"Unknown engine {engine}"}

def set_model(model_id: str, provider_id: Optional[str] = None, engine_name: Optional[str] = None) -> Dict[str, Any]:
    engine = engine_name or get_active_engine()
    cfg = load_json_file(CONFIG_FILE, {"agent_engine": engine, "engine_models": {}})
    if "engine_models" not in cfg:
        cfg["engine_models"] = {}

    if not provider_id:
        if "/" in model_id:
            provider_id = model_id.split("/")[0]
        else:
            provider_id = "default"

    cfg["engine_models"][engine] = {"model": model_id, "provider": provider_id}
    save_json_file(CONFIG_FILE, cfg)

    if engine == "hermes":
        if HERMES_CONFIG_FILE.exists():
            try:
                content = HERMES_CONFIG_FILE.read_text(encoding="utf-8")
                new_content = re.sub(
                    r"(model:\s*\n\s*default:\s*)[^\n]+",
                    rf"\g<1>{model_id}",
                    content
                )
                new_content = re.sub(
                    r"(model:\s*\n(?:[^\n]+\n)*?\s*provider:\s*)[^\n]+",
                    rf"\g<1>{provider_id}",
                    new_content
                )
                HERMES_CONFIG_FILE.write_text(new_content, encoding="utf-8")
            except Exception as e:
                return {"ok": False, "error": f"Failed to set Hermes model: {str(e)}"}
    elif engine == "omp":
        if OMP_CONFIG_FILE.exists():
            try:
                content = OMP_CONFIG_FILE.read_text(encoding="utf-8")
                new_content = re.sub(r"(default:\s*)[^\n]+", rf"\g<1>{model_id}", content)
                OMP_CONFIG_FILE.write_text(new_content, encoding="utf-8")
            except Exception as e:
                return {"ok": False, "error": f"Failed to set OMP model: {str(e)}"}

    set_status(get_status().get("state", "idle"), headline=f"{engine.upper()}: {model_id}")
    return {"ok": True, "engine": engine, "model": model_id, "provider": provider_id}

# ── Voice Dictation ────────────────────────────────────────────────────────────

def dictate_start() -> Dict[str, Any]:
    if VOICE_PID_FILE.exists():
        dictate_stop()

    if VOICE_WAV_FILE.exists():
        VOICE_WAV_FILE.unlink()

    try:
        proc = subprocess.Popen(
            ["ffmpeg", "-y", "-f", "pulse", "-i", "default", "-ac", "1", "-ar", "16000", str(VOICE_WAV_FILE)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        VOICE_PID_FILE.write_text(str(proc.pid))
        return {"ok": True, "recording": True, "pid": proc.pid}
    except Exception as e:
        return {"ok": False, "error": f"Failed to start recording: {str(e)}"}

def dictate_stop() -> Dict[str, Any]:
    if not VOICE_PID_FILE.exists():
        return {"ok": False, "error": "No active recording."}

    try:
        pid = int(VOICE_PID_FILE.read_text().strip())
        os.kill(pid, 15)
        time.sleep(0.3)
    except Exception:
        pass
    finally:
        VOICE_PID_FILE.unlink(missing_ok=True)

    if not VOICE_WAV_FILE.exists() or VOICE_WAV_FILE.stat().st_size < 1000:
        return {"ok": False, "error": "Audio capture was empty."}

    try:
        res = subprocess.run(["voxtype", "transcribe", str(VOICE_WAV_FILE)], capture_output=True, text=True, timeout=15)
        output_lines = [l.strip() for l in res.stdout.splitlines() if l.strip()]
        text_lines = [l for l in output_lines if not l.startswith("Loading audio file") and not l.startswith("Audio format") and not l.startswith("Processing") and not re.search(r"^\d{4}-\d{2}-\d{2}T", l) and not l.startswith("whisper_")]
        transcribed = " ".join(text_lines).strip()
        return {"ok": True, "text": transcribed}
    except Exception as e:
        return {"ok": False, "error": f"Transcription failed: {str(e)}"}

def dictate_status() -> Dict[str, Any]:
    return {"ok": True, "recording": VOICE_PID_FILE.exists()}

# ── Memory Management (Add & Delete) ──────────────────────────────────────────

def get_memories() -> Dict[str, Any]:
    memories = []
    mem_file = HERMES_MEMORY_DIR / "MEMORY.md"
    if mem_file.exists():
        secure_file_permissions(mem_file)
        try:
            content = mem_file.read_text(encoding="utf-8")
            entries = [e.strip() for e in content.split("§") if e.strip()]
            for idx, entry in enumerate(entries):
                memories.append({
                    "id": f"mem_{idx}",
                    "index": idx,
                    "type": "system",
                    "text": redact_secrets(entry),
                    "source": "MEMORY.md"
                })
        except Exception:
            pass

    user_file = HERMES_MEMORY_DIR / "USER.md"
    if user_file.exists():
        secure_file_permissions(user_file)
        try:
            content = user_file.read_text(encoding="utf-8")
            entries = [e.strip() for e in content.split("§") if e.strip()]
            for idx, entry in enumerate(entries):
                memories.append({
                    "id": f"user_{idx}",
                    "index": idx,
                    "type": "user",
                    "text": redact_secrets(entry),
                    "source": "USER.md"
                })
        except Exception:
            pass

    return {"ok": True, "memories": memories, "count": len(memories)}

def add_memory(text: str, is_user_fact: bool = False) -> Dict[str, Any]:
    if not text.strip():
        return {"ok": False, "error": "Empty memory text."}
    
    target_file = (HERMES_MEMORY_DIR / "USER.md") if is_user_fact else (HERMES_MEMORY_DIR / "MEMORY.md")
    target_file.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(target_file.parent, 0o700)
    
    try:
        content = ""
        if target_file.exists():
            content = target_file.read_text(encoding="utf-8").strip()
        
        if content:
            new_content = f"{content}\n§\n{text.strip()}\n"
        else:
            new_content = f"{text.strip()}\n"
            
        target_file.write_text(new_content, encoding="utf-8")
        secure_file_permissions(target_file)
        set_status(get_status().get("state", "idle"), headline="Memory saved")
        update_encrypted_vault()
        return {"ok": True, "message": "Memory saved successfully."}
    except Exception as e:
        return {"ok": False, "error": f"Failed to save memory: {str(e)}"}

def delete_memory(memory_id: str, is_user_fact: bool = False) -> Dict[str, Any]:
    target_file = (HERMES_MEMORY_DIR / "USER.md") if is_user_fact else (HERMES_MEMORY_DIR / "MEMORY.md")
    if not target_file.exists():
        return {"ok": False, "error": "Memory file not found."}

    try:
        content = target_file.read_text(encoding="utf-8")
        entries = [e.strip() for e in content.split("§") if e.strip()]
        
        idx = int(str(memory_id).split("_")[-1])
        if 0 <= idx < len(entries):
            removed = entries.pop(idx)
            new_content = ("\n§\n".join(entries) + "\n") if entries else ""
            target_file.write_text(new_content, encoding="utf-8")
            secure_file_permissions(target_file)
            set_status(get_status().get("state", "idle"), headline="Memory deleted")
            update_encrypted_vault()
            return {"ok": True, "message": "Memory deleted.", "removed": removed}
        else:
            return {"ok": False, "error": "Memory index out of range."}
    except Exception as e:
        return {"ok": False, "error": f"Failed to delete memory: {str(e)}"}


def get_skills() -> Dict[str, Any]:
    skills = []
    if HERMES_SKILLS_DIR.exists():
        for d in HERMES_SKILLS_DIR.iterdir():
            if d.is_dir():
                skill_file = d / "SKILL.md"
                desc = ""
                if skill_file.exists():
                    try:
                        first_lines = skill_file.read_text(encoding="utf-8").splitlines()[:5]
                        for l in first_lines:
                            if l.startswith("description:") or l.startswith("Description:") or l.startswith("#"):
                                desc = l.lstrip("#: ").strip()
                                break
                    except Exception:
                        pass
                skills.append({
                    "name": d.name,
                    "description": desc or "Local skill for Botty",
                    "path": str(d),
                    "is_custom": True
                })
    return {"ok": True, "skills": skills, "count": len(skills)}

def create_skill(name: str, description: str, instructions: str) -> Dict[str, Any]:
    clean_name = re.sub(r"[^a-zA-Z0-9_-]", "-", name.strip().lower())
    if not clean_name:
        return {"ok": False, "error": "Invalid skill name."}
    
    skill_dir = HERMES_SKILLS_DIR / clean_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    
    skill_file = skill_dir / "SKILL.md"
    body = f"""---
name: {clean_name}
description: "{description.strip()}"
---

# {clean_name.replace('-', ' ').title()}

{instructions.strip()}
"""
    try:
        skill_file.write_text(body, encoding="utf-8")
        set_status(get_status().get("state", "idle"), headline=f"Created skill {clean_name}")
        return {"ok": True, "name": clean_name, "path": str(skill_dir)}
    except Exception as e:
        return {"ok": False, "error": f"Failed to create skill: {str(e)}"}

def distill_and_compact_session(force: bool = False, preserve_tail: Optional[int] = None) -> Dict[str, Any]:
    """
    Distills durable user facts into USER.md, system facts into MEMORY.md, procedural
    workflows into skills/, archives full conversation turns to history_archive.jsonl,
    and replaces pruned history in history.json with a concise context summary + protected tail.
    Also resets the Hermes SQLite session so the next turn starts with 0 stale context tokens.
    """
    history = load_json_file(HISTORY_FILE, {"session_id": "botty-widget", "messages": []})
    msgs = history.get("messages", [])
    
    threshold = get_auto_compact_threshold()
    tail_count = preserve_tail if preserve_tail is not None else get_compact_preserve_tail()
    
    if len(msgs) < threshold and not force:
        return {"ok": True, "compacted": False, "reason": f"Message count ({len(msgs)}) below threshold ({threshold})"}
    
    if len(msgs) <= tail_count:
        return {"ok": True, "compacted": False, "reason": f"Message count ({len(msgs)}) <= preserve tail ({tail_count})"}
    
    to_compact = msgs[:-tail_count]
    tail_messages = msgs[-tail_count:]
    
    # Format readable conversation text for distillation
    conv_lines = []
    for m in to_compact:
        r = m.get("role", "user").capitalize()
        c = m.get("content", "").strip()
        if m.get("is_summary"):
            conv_lines.append(f"[Previous Summary]: {c}")
            continue
        if c:
            conv_lines.append(f"{r}: {c}")
        if m.get("attachments"):
            for att in m.get("attachments", []):
                fn = att.get("filename") or att.get("path", "")
                conv_lines.append(f"[{r} Attachment: {fn}]")
        if m.get("actions"):
            for act in m.get("actions", []):
                conv_lines.append(f"[{r} Action: {act.get('text', '')}]")
    
    conv_text = "\n".join(conv_lines)
    if not conv_text.strip():
        return {"ok": True, "compacted": False, "reason": "No compactable text"}

    set_status("working", headline="Distilling memory & compacting…")

    distillation_prompt = (
        "You are an expert knowledge distillation engine for Botty desktop assistant on Linux.\n"
        "Analyze the following conversation history.\n"
        "Extract:\n"
        "1. 'user_memories': A JSON array of strings containing durable user facts, preferences, specific tool requests, project styles, or persistent instructions (for USER.md).\n"
        "2. 'system_memories': A JSON array of strings containing durable workstation environment facts, tool installations, socket/service paths, display manager details, or system fixes (for MEMORY.md).\n"
        "3. 'skills': A JSON array of objects representing reusable procedural recipes, workflows, or troubleshooting procedures discovered. Each object MUST have: 'name' (kebab-case), 'description' (one concise sentence), and 'instructions' (clean markdown steps for SKILL.md). Only produce a skill if a clear reusable procedure or multi-step workflow was discovered.\n"
        "4. 'context_summary': A concise 2-3 sentence overview of the conversation topics, current machine status, and any active pending task.\n\n"
        "CRITICAL: Respond ONLY with a valid JSON object matching this schema:\n"
        "{\n"
        '  "user_memories": ["..."],\n'
        '  "system_memories": ["..."],\n'
        '  "skills": [{"name": "...", "description": "...", "instructions": "..."}],\n'
        '  "context_summary": "..."\n'
        "}\n\n"
        "CONVERSATION HISTORY TO DISTILL:\n"
        f"{conv_text}"
    )

    engine = get_active_engine()
    distilled_data = None
    raw_llm_out = ""

    # Attempt LLM distillation
    try:
        if engine == "omp":
            cmd = ["omp", "-p", "--allow-home", distillation_prompt]
        elif engine == "claude":
            cmd = ["claude", "-p", distillation_prompt]
        elif engine == "codex":
            cmd = ["codex", "exec", distillation_prompt]
        else:
            # Hermes one-shot mode -z
            cmd = ["hermes", "-p", "botty", "-z", distillation_prompt, "--run-budget", "60"]
            active_m = get_active_model_for_engine("hermes")
            m_name = active_m.get("model")
            p_name = active_m.get("provider")
            if m_name:
                cmd.extend(["-m", m_name])
            if p_name:
                cmd.extend(["--provider", p_name])

        res = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        raw_llm_out = res.stdout.strip()
        if res.returncode == 0 and raw_llm_out:
            json_match = re.search(r"\{[\s\S]*\}", raw_llm_out)
            if json_match:
                distilled_data = json.loads(json_match.group(0))
    except Exception as e:
        append_botty_log(f"Distillation LLM error: {str(e)}")

    if not isinstance(distilled_data, dict):
        distilled_data = {
            "user_memories": [],
            "system_memories": [],
            "skills": [],
            "context_summary": f"Conversation covering {len(to_compact)} previous messages. Core context archived."
        }

    user_mems = distilled_data.get("user_memories", [])
    sys_mems = distilled_data.get("system_memories", [])
    new_skills = distilled_data.get("skills", [])
    summary_text = str(distilled_data.get("context_summary", "")).strip() or "Prior conversation compacted into persistent memory."

    # 1. Save memories
    saved_user_count = 0
    existing_user_mems = {m.get("text", "").strip().lower() for m in get_memories().get("memories", []) if m.get("type") == "user"}
    for um in user_mems:
        um_str = str(um).strip()
        if um_str and um_str.lower() not in existing_user_mems:
            add_memory(um_str, is_user_fact=True)
            existing_user_mems.add(um_str.lower())
            saved_user_count += 1

    saved_sys_count = 0
    existing_sys_mems = {m.get("text", "").strip().lower() for m in get_memories().get("memories", []) if m.get("type") == "system"}
    for sm in sys_mems:
        sm_str = str(sm).strip()
        if sm_str and sm_str.lower() not in existing_sys_mems:
            add_memory(sm_str, is_user_fact=False)
            existing_sys_mems.add(sm_str.lower())
            saved_sys_count += 1

    # 2. Save skills
    saved_skill_count = 0
    for sk in new_skills:
        if isinstance(sk, dict) and sk.get("name") and sk.get("instructions"):
            sk_res = create_skill(sk.get("name", ""), sk.get("description", ""), sk.get("instructions", ""))
            if sk_res.get("ok"):
                saved_skill_count += 1

    # 3. Archive compacted messages to history_archive.jsonl
    try:
        with open(HISTORY_ARCHIVE_FILE, "a", encoding="utf-8") as f:
            for m in to_compact:
                archive_entry = {
                    "archived_at": int(time.time()),
                    "session_id": history.get("session_id", "botty-widget"),
                    "message": m
                }
                f.write(json.dumps(archive_entry, ensure_ascii=False) + "\n")
        secure_file_permissions(HISTORY_ARCHIVE_FILE)
    except Exception as e:
        append_botty_log(f"Archive write error: {str(e)}")

    # 4. Construct synthetic summary message and prune history.json
    summary_msg = {
        "id": f"msg_summary_{int(time.time()*1000)}",
        "role": "system",
        "content": f"📌 [Context Compacted]: {summary_text}",
        "timestamp": int(time.time()),
        "attachments": [],
        "actions": [],
        "model": "",
        "engine": "",
        "is_summary": True
    }
    history["messages"] = [summary_msg] + tail_messages
    save_json_file(HISTORY_FILE, history)

    # 5. Reset Hermes session context in SQLite state.db
    reset_hermes_session("botty-widget")

    # 6. Synchronize encrypted memory vault
    try:
        update_encrypted_vault()
    except Exception:
        pass

    set_status("idle", headline="Ready", last_query="", last_answer=f"Memory consolidated: {saved_user_count + saved_sys_count} memories, {saved_skill_count} skills saved. Context pruned.")
    append_botty_log(f"COMPACTION COMPLETED: Compacted {len(to_compact)} msgs -> {len(history['messages'])} msgs left. Added {saved_user_count} user mems, {saved_sys_count} sys mems, {saved_skill_count} skills.")

    return {
        "ok": True,
        "compacted": True,
        "compacted_count": len(to_compact),
        "remaining_count": len(history["messages"]),
        "memories_added": saved_user_count + saved_sys_count,
        "skills_added": saved_skill_count,
        "summary": summary_text
    }

def compact_memory(force: bool = True, preserve_tail: Optional[int] = None) -> Dict[str, Any]:
    return distill_and_compact_session(force=force, preserve_tail=preserve_tail)

def copy_to_clipboard(text: str) -> Dict[str, Any]:
    try:
        proc = subprocess.run(["wl-copy"], input=text, text=True, capture_output=True, timeout=3)
        return {"ok": proc.returncode == 0}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def get_clipboard_image() -> Dict[str, Any]:
    timestamp = int(time.time() * 1000)
    clip_path = SCREENSHOT_DIR / f"clip_{timestamp}.png"
    try:
        proc = subprocess.run(["wl-paste", "--type", "image/png"], stdout=open(clip_path, "wb"), stderr=subprocess.PIPE, timeout=3)
        if proc.returncode == 0 and clip_path.exists() and clip_path.stat().st_size > 0:
            secure_file_permissions(clip_path)
            return {"ok": True, "image_path": str(clip_path), "filename": clip_path.name}
        else:
            if clip_path.exists():
                clip_path.unlink()
            return {"ok": False, "error": "No image found in clipboard."}
    except Exception as e:
        if clip_path.exists():
            clip_path.unlink()
        return {"ok": False, "error": str(e)}

def main():
    parser = argparse.ArgumentParser(description="Botty Omarchy Agent Backend")
    subparsers = parser.add_subparsers(dest="command", help="Backend command")

    subparsers.add_parser("status", help="Get live bot status")
    
    ask_p = subparsers.add_parser("ask", help="Send a query to Botty")
    ask_p.add_argument("query", nargs="?", default="", help="Query prompt text")
    ask_p.add_argument("--image", dest="image", default=None, help="Path to image/media file")
    ask_p.add_argument("--file", dest="file", default=None, help="Path to any file/code/document")
    ask_p.add_argument("--screen", dest="screen", action="store_true", help="Capture and include screen screenshot context")
    ask_p.add_argument("--situation", dest="situation", action="store_true", help="Include targeted desktop & tool situation context")
    ask_p.add_argument("--model", dest="model", default=None, help="Model override")
    ask_p.add_argument("--provider", dest="provider", default=None, help="Provider override")

    inspect_p = subparsers.add_parser("inspect-file", help="Inspect file metadata")
    inspect_p.add_argument("path", help="Path to file")

    subparsers.add_parser("pick-file", help="Open native floating GTK3 file chooser dialog")

    cap_p = subparsers.add_parser("capture", help="Capture screen/window")
    cap_p.add_argument("--mode", dest="mode", default="activewindow", choices=["activewindow", "fullscreen", "region"])

    subparsers.add_parser("situation-context", help="Extract visible workspace tools and desktop situation context")
    subparsers.add_parser("logs", help="Get execution logs and raw agent outputs")
    subparsers.add_parser("clear-logs", help="Clear execution logs")

    subparsers.add_parser("history", help="Get conversation history")
    subparsers.add_parser("clear", help="Clear conversation history")
    
    models_p = subparsers.add_parser("models", help="List active providers and models for an engine")
    models_p.add_argument("--engine", dest="engine", default=None, help="Agent engine name (hermes, omp, claude, codex)")
    
    set_m = subparsers.add_parser("set-model", help="Set active model for engine")
    set_m.add_argument("model", help="Model identifier")
    set_m.add_argument("--provider", dest="provider", default=None, help="Provider name")
    set_m.add_argument("--engine", dest="engine", default=None, help="Engine name")

    subparsers.add_parser("engines", help="List available agent engines")
    set_e = subparsers.add_parser("set-engine", help="Set active agent engine (hermes, omp, claude, codex)")
    set_e.add_argument("engine", help="Engine name")

    subparsers.add_parser("vault-status", help="Get local AES-256 vault status")
    subparsers.add_parser("vault-backup", help="Create encrypted vault snapshot")

    subparsers.add_parser("dictate-start", help="Start voice recording")
    subparsers.add_parser("dictate-stop", help="Stop voice recording & transcribe")
    subparsers.add_parser("dictate-status", help="Check voice recording status")

    subparsers.add_parser("memories", help="List memories")
    
    add_m = subparsers.add_parser("add-memory", help="Add persistent memory")
    add_m.add_argument("text", help="Memory content")
    add_m.add_argument("--user", dest="user", action="store_true", help="Store as user fact in USER.md")

    del_m = subparsers.add_parser("delete-memory", help="Delete a persistent memory")
    del_m.add_argument("id", help="Memory ID or index")
    del_m.add_argument("--user", dest="user", action="store_true", help="Delete from USER.md")

    compact_p = subparsers.add_parser("compact", help="Compact session memory & prune old context")
    compact_p.add_argument("--force", dest="force", action="store_true", default=True, help="Force compaction even if below threshold")
    compact_p.add_argument("--tail", dest="tail", type=int, default=None, help="Number of recent messages to preserve as active tail")

    subparsers.add_parser("skills", help="List skills")

    create_s = subparsers.add_parser("create-skill", help="Create a new skill")
    create_s.add_argument("name", help="Skill name")
    create_s.add_argument("description", help="Short description")
    create_s.add_argument("instructions", help="Skill instructions/markdown")

    copy_p = subparsers.add_parser("copy", help="Copy text to clipboard")
    copy_p.add_argument("text", help="Text to copy")

    subparsers.add_parser("clip-image", help="Get clipboard image")

    args = parser.parse_args()

    if not args.command or args.command == "status":
        print(json.dumps(get_status(), ensure_ascii=False))
    elif args.command == "ask":
        res = ask(args.query, image_path=args.image, file_path=args.file, screen_context=args.screen, situation_context=args.situation, model=args.model, provider=args.provider)
        print(json.dumps(res, ensure_ascii=False))
    elif args.command == "inspect-file":
        print(json.dumps(inspect_file(args.path), ensure_ascii=False))
    elif args.command == "pick-file":
        print(json.dumps(pick_file_dialog(), ensure_ascii=False))
    elif args.command == "capture":
        print(json.dumps(capture_screen(args.mode), ensure_ascii=False))
    elif args.command == "situation-context":
        print(json.dumps(get_targeted_situation_context(), ensure_ascii=False))
    elif args.command == "logs":
        print(json.dumps(get_botty_logs(), ensure_ascii=False))
    elif args.command == "clear-logs":
        print(json.dumps(clear_botty_logs(), ensure_ascii=False))
    elif args.command == "history":
        print(json.dumps(get_history(), ensure_ascii=False))
    elif args.command == "clear":
        print(json.dumps(clear_history(), ensure_ascii=False))
    elif args.command == "models":
        print(json.dumps(get_dynamic_engine_models(args.engine), ensure_ascii=False))
    elif args.command == "set-model":
        print(json.dumps(set_model(args.model, args.provider, args.engine), ensure_ascii=False))
    elif args.command == "engines":
        print(json.dumps(get_agent_engines(), ensure_ascii=False))
    elif args.command == "set-engine":
        print(json.dumps(set_active_engine(args.engine), ensure_ascii=False))
    elif args.command == "vault-status":
        print(json.dumps(get_vault_security_info(), ensure_ascii=False))
    elif args.command == "vault-backup":
        print(json.dumps({"ok": update_encrypted_vault(), "vault": str(VAULT_FILE)}, ensure_ascii=False))
    elif args.command == "dictate-start":
        print(json.dumps(dictate_start(), ensure_ascii=False))
    elif args.command == "dictate-stop":
        print(json.dumps(dictate_stop(), ensure_ascii=False))
    elif args.command == "dictate-status":
        print(json.dumps(dictate_status(), ensure_ascii=False))
    elif args.command == "memories":
        print(json.dumps(get_memories(), ensure_ascii=False))
    elif args.command == "add-memory":
        print(json.dumps(add_memory(args.text, is_user_fact=args.user), ensure_ascii=False))
    elif args.command == "delete-memory":
        print(json.dumps(delete_memory(args.id, is_user_fact=args.user), ensure_ascii=False))
    elif args.command == "compact":
        print(json.dumps(compact_memory(force=args.force, preserve_tail=args.tail), ensure_ascii=False))
    elif args.command == "skills":
        print(json.dumps(get_skills(), ensure_ascii=False))
    elif args.command == "create-skill":
        print(json.dumps(create_skill(args.name, args.description, args.instructions), ensure_ascii=False))
    elif args.command == "copy":
        print(json.dumps(copy_to_clipboard(args.text), ensure_ascii=False))
    elif args.command == "clip-image":
        print(json.dumps(get_clipboard_image(), ensure_ascii=False))
    else:
        print(json.dumps({"ok": False, "error": f"Unknown command: {args.command}"}))

if __name__ == "__main__":
    main()
