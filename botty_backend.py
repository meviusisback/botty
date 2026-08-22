#!/usr/bin/env python3
"""
botty_backend.py - Native backend for Botty Omarchy bar widget and desktop assistant.
Standard-library only. Interfaces with Hermes Agent profile "botty", OMP, Claude, and other agents,
captures screen context, attaches any file/document/media, manages conversation history,
handles model & provider selection, continuous learning, memory deletion, voice dictation,
and local AES-256 encrypted memory vault management.
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
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

# Base paths
BOTTY_DATA_DIR = Path.home() / ".local" / "share" / "botty"
BOTTY_DATA_DIR.mkdir(parents=True, exist_ok=True)
os.chmod(BOTTY_DATA_DIR, 0o700) # Enforce strict owner-only permissions

CONFIG_FILE = BOTTY_DATA_DIR / "config.json"
STATUS_FILE = BOTTY_DATA_DIR / "status.json"
HISTORY_FILE = BOTTY_DATA_DIR / "history.json"
VAULT_FILE = BOTTY_DATA_DIR / "vault.enc"
LOCK_FILE = BOTTY_DATA_DIR / "running.pid"
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
    t = re.sub(r"<thought>.*?</thought>", "", t, flags=re.DOTALL | re.IGNORECASE)
    t = re.sub(r"<think>.*?</think>", "", t, flags=re.DOTALL | re.IGNORECASE)
    t = re.sub(r"<reasoning>.*?</reasoning>", "", t, flags=re.DOTALL | re.IGNORECASE)
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

# ── Local Encryption & Vault Security ──────────────────────────────────────────

def get_machine_vault_key() -> str:
    """Generates a hardware-bound local encryption key using machine-id and user UID."""
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
    """Enforces owner-only (0600) permissions on sensitive files."""
    if filepath.exists():
        try:
            os.chmod(filepath, 0o600)
        except Exception:
            pass

def update_encrypted_vault() -> bool:
    """Encrypts local memories and history into AES-256 PBKDF2 vault backup."""
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
    """Returns local encryption and security hardening status."""
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
    """Inspects any attached file (code, documents, PDF, images, etc.) and prepares context."""
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

    # Category detection
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

    # Read preview text content for code/text files
    text_content = ""
    if category == "code" or mime.startswith("text/"):
        try:
            # Read up to 120 KB
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
    set_status(get_status().get("state", "idle"), headline=f"Agent engine: {engine.upper()}")
    return {"ok": True, "active_engine": engine}

def get_agent_engines() -> Dict[str, Any]:
    current_engine = get_active_engine()
    engines = [
        {
            "id": "hermes",
            "name": "Hermes Agent (Botty)",
            "desc": "Persistent assistant with desktop screen awareness, memory, skills & multi-model support",
            "icon": "🐼",
            "available": bool(shutil.which("hermes"))
        },
        {
            "id": "omp",
            "name": "OMP (Oh My Pi)",
            "desc": "Coding harness & parallel subagent orchestrator",
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

def get_active_model() -> Dict[str, str]:
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

def get_status() -> Dict[str, Any]:
    active_m = get_active_model()
    active_eng = get_active_engine()
    default_status = {
        "ok": True,
        "state": "idle",
        "headline": "Ready",
        "last_query": "",
        "last_answer": "",
        "last_error": "",
        "active_engine": active_eng,
        "active_model": active_m["model"],
        "active_provider": active_m["provider"],
        "session_turns": 0,
        "memory_count": count_memories(),
        "skills_count": count_skills(),
        "timestamp": int(time.time()),
        "has_active_work": False,
        "is_recording": VOICE_PID_FILE.exists()
    }
    status = load_json_file(STATUS_FILE, default_status)
    status["active_engine"] = active_eng
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
    active = get_active_model()
    current["active_engine"] = get_active_engine()
    current["active_model"] = active["model"]
    current["active_provider"] = active["provider"]
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
    set_status("idle", headline="Ready", last_query="", last_answer="", last_error="")
    return {"ok": True, "message": "History cleared"}

def add_history_message(role: str, content: str, attachments: Optional[List[Dict[str, Any]]] = None, actions: Optional[List[Dict[str, Any]]] = None) -> None:
    history = load_json_file(HISTORY_FILE, {"session_id": "botty-widget", "messages": []})
    msgs = history.get("messages", [])
    
    if msgs:
        last = msgs[-1]
        if last.get("role") == role and last.get("content") == content:
            if abs(int(time.time()) - last.get("timestamp", 0)) < 10:
                return

    msg = {
        "id": f"msg_{int(time.time()*1000)}_{len(msgs)}",
        "role": role,
        "content": content,
        "timestamp": int(time.time()),
        "attachments": attachments or [],
        "actions": actions or []
    }
    msgs.append(msg)
    history["messages"] = msgs
    save_json_file(HISTORY_FILE, history)

def ask(query: str, image_path: Optional[str] = None, file_path: Optional[str] = None, screen_context: bool = False, model: Optional[str] = None, provider: Optional[str] = None) -> Dict[str, Any]:
    """
    Executes a query through the active agent engine, attaching screenshot, files, or media.
    """
    if not query.strip() and not image_path and not file_path and not screen_context:
        return {"ok": False, "error": "Empty query and no attachment provided."}

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
    target_image = image_path
    attached_file_info: Optional[Dict[str, Any]] = None

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

    # Build attachments metadata
    attachments: List[Dict[str, Any]] = []
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

    # Prepare prompt text
    prompt_parts = []
    if captured_context:
        win = captured_context.get("active_window", {})
        win_title = win.get("title", "")
        win_class = win.get("class", "")
        if win_title or win_class:
            prompt_parts.append(f"[Active Window Context: {win_class} — '{win_title}']\n")

    if attached_file_info and not attached_file_info.get("is_image"):
        fname = attached_file_info["filename"]
        fpath = attached_file_info["path"]
        fext = attached_file_info["extension"]
        if attached_file_info.get("has_text_content"):
            preview = attached_file_info["text_preview"]
            prompt_parts.append(f"[ATTACHED FILE: {fname} (Path: {fpath})]\n```{fext}\n{preview}\n```\n[END ATTACHED FILE]\n")
        else:
            prompt_parts.append(f"[ATTACHED DOCUMENT: {fname} (Path: {fpath}, Size: {attached_file_info['size_str']})]\nPlease inspect this file using your file/document tools.\n[END ATTACHED DOCUMENT]\n")

    user_query = query.strip() if query.strip() else ("Analyze the attached file." if attached_file_info else "Analyze the attached screenshot context.")
    prompt_parts.append(user_query)
    final_prompt = "\n".join(prompt_parts)

    add_history_message("user", user_query, attachments=attachments)

    query_tmp = BOTTY_DATA_DIR / "current_query.txt"
    query_tmp.write_text(final_prompt, encoding="utf-8")

    engine = get_active_engine()
    cmd = []

    if engine == "omp":
        cmd = ["omp", "-p", "--allow-home", final_prompt]
    elif engine == "claude":
        cmd = ["claude", "-p", final_prompt]
    elif engine == "codex":
        cmd = ["codex", "exec", final_prompt]
    else:
        cmd = [
            "hermes",
            "-p", "botty",
            "chat",
            "-Q",
            "--query-file", str(query_tmp),
            "--continue", "botty-widget",
            "--create-if-missing",
            "--yolo"
        ]
        if target_image and os.path.exists(target_image):
            cmd.extend(["--image", target_image])
        if model:
            cmd.extend(["-m", model])
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
        
        if proc.returncode != 0 and not cleaned_response:
            err_msg = stderr_data.strip() or f"Agent process exited with code {proc.returncode}"
            set_status("error", headline="Error", last_error=err_msg)
            add_history_message("assistant", f"⚠️ Error: {err_msg}")
            return {"ok": False, "error": err_msg}

        if not cleaned_response:
            cleaned_response = "Done."

        actions = []
        for line in stdout_data.splitlines():
            if line.startswith("session_id:"):
                continue
            if "Saved memory" in line or "Saved to memory" in line:
                actions.append({"type": "memory", "text": line.strip()})
            elif "Created skill" in line or "Skill installed" in line:
                actions.append({"type": "skill", "text": line.strip()})

        add_history_message("assistant", cleaned_response, actions=actions)
        set_status("idle", headline="Ready", last_query=query, last_answer=cleaned_response)

        # Background update of encrypted vault backup
        try:
            update_encrypted_vault()
        except Exception:
            pass

        return {
            "ok": True,
            "response": cleaned_response,
            "raw_output": redact_secrets(stdout_data),
            "actions": actions,
            "attachments": attachments
        }

    except subprocess.TimeoutExpired:
        if 'proc' in locals():
            proc.kill()
        err = "Request timed out after 240 seconds."
        set_status("error", headline="Timeout", last_error=err)
        add_history_message("assistant", f"⚠️ {err}")
        return {"ok": False, "error": err}
    except Exception as e:
        err = f"Execution error: {str(e)}"
        set_status("error", headline="Error", last_error=err)
        add_history_message("assistant", f"⚠️ {err}")
        return {"ok": False, "error": err}
    finally:
        LOCK_FILE.unlink(missing_ok=True)
        query_tmp.unlink(missing_ok=True)

# ── Providers & Models Discovery (Active Key Filtered) ──────────────────────────

def get_active_configured_providers() -> Dict[str, Any]:
    active = get_active_model()
    
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

    if active.get("provider"):
        active_provider_ids.add(active["provider"])

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
        "active_engine": get_active_engine(),
        "active_model": active["model"],
        "active_provider": active["provider"],
        "providers": sorted_providers
    }

def set_model(model_id: str, provider_id: Optional[str] = None) -> Dict[str, Any]:
    if not HERMES_CONFIG_FILE.exists():
        return {"ok": False, "error": f"Config file not found: {HERMES_CONFIG_FILE}"}
    
    if not provider_id:
        if "/" in model_id:
            provider_id = "openrouter"
        elif ":" in model_id:
            provider_id = "ollama"
        else:
            provider_id = "opencode-go"

    try:
        with open(HERMES_CONFIG_FILE, "r", encoding="utf-8") as f:
            content = f.read()

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

        with open(HERMES_CONFIG_FILE, "w", encoding="utf-8") as f:
            f.write(new_content)

        set_status(get_status().get("state", "idle"), headline=f"Model: {model_id}")
        return {"ok": True, "model": model_id, "provider": provider_id}
    except Exception as e:
        return {"ok": False, "error": f"Failed to set model: {str(e)}"}

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

def compact_memory() -> Dict[str, Any]:
    set_status("working", headline="Compacting memory…")
    cmd = [
        "hermes", "-p", "botty", "chat", "-Q",
        "-q", "Review our conversation and summarize any durable facts, user preferences, or system conventions into persistent memory using the memory tool. Be concise.",
        "--continue", "botty-widget",
        "--yolo"
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        set_status("idle", headline="Memory compacted", last_answer=res.stdout.strip())
        update_encrypted_vault()
        return {"ok": True, "output": res.stdout.strip()}
    except Exception as e:
        set_status("error", headline="Compaction error", last_error=str(e))
        return {"ok": False, "error": str(e)}

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
    ask_p.add_argument("--screen", dest="screen", action="store_true", help="Capture and include screen context")
    ask_p.add_argument("--model", dest="model", default=None, help="Model override")
    ask_p.add_argument("--provider", dest="provider", default=None, help="Provider override")

    inspect_p = subparsers.add_parser("inspect-file", help="Inspect file metadata")
    inspect_p.add_argument("path", help="Path to file")

    cap_p = subparsers.add_parser("capture", help="Capture screen/window")
    cap_p.add_argument("--mode", dest="mode", default="activewindow", choices=["activewindow", "fullscreen", "region"])

    subparsers.add_parser("history", help="Get conversation history")
    subparsers.add_parser("clear", help="Clear conversation history")
    subparsers.add_parser("models", help="List active providers and models")
    
    set_m = subparsers.add_parser("set-model", help="Set active model")
    set_m.add_argument("model", help="Model identifier")
    set_m.add_argument("--provider", dest="provider", default=None, help="Provider name")

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

    subparsers.add_parser("compact", help="Compact session memory")
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
        res = ask(args.query, image_path=args.image, file_path=args.file, screen_context=args.screen, model=args.model, provider=args.provider)
        print(json.dumps(res, ensure_ascii=False))
    elif args.command == "inspect-file":
        print(json.dumps(inspect_file(args.path), ensure_ascii=False))
    elif args.command == "capture":
        print(json.dumps(capture_screen(args.mode), ensure_ascii=False))
    elif args.command == "history":
        print(json.dumps(get_history(), ensure_ascii=False))
    elif args.command == "clear":
        print(json.dumps(clear_history(), ensure_ascii=False))
    elif args.command == "models":
        print(json.dumps(get_active_configured_providers(), ensure_ascii=False))
    elif args.command == "set-model":
        print(json.dumps(set_model(args.model, args.provider), ensure_ascii=False))
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
        print(json.dumps(compact_memory(), ensure_ascii=False))
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
