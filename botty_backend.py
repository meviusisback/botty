#!/usr/bin/env python3
"""
botty_backend.py - Native backend for Botty Omarchy bar widget and desktop assistant.
Standard-library only. Interfaces with Hermes Agent profile "botty", captures screen context,
manages conversation history, handles model selection, continuous learning, memory, and voice dictation.
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
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

# Base paths
BOTTY_DATA_DIR = Path.home() / ".local" / "share" / "botty"
BOTTY_DATA_DIR.mkdir(parents=True, exist_ok=True)

STATUS_FILE = BOTTY_DATA_DIR / "status.json"
HISTORY_FILE = BOTTY_DATA_DIR / "history.json"
LOCK_FILE = BOTTY_DATA_DIR / "running.pid"
VOICE_PID_FILE = BOTTY_DATA_DIR / "voice_record.pid"
VOICE_WAV_FILE = Path("/tmp/botty_dictation.wav")
SCREENSHOT_DIR = BOTTY_DATA_DIR / "captures"
SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)

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
        tmp_path.replace(path)
    except Exception as e:
        if tmp_path.exists():
            tmp_path.unlink()
        raise e

def get_active_model() -> Dict[str, str]:
    """Reads the active model and provider from botty profile config.yaml."""
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
    """Returns the current state for bar widget polling."""
    default_status = {
        "ok": True,
        "state": "idle",
        "headline": "Ready",
        "last_query": "",
        "last_answer": "",
        "last_error": "",
        "active_model": get_active_model()["model"],
        "active_provider": get_active_model()["provider"],
        "session_turns": 0,
        "memory_count": count_memories(),
        "skills_count": count_skills(),
        "timestamp": int(time.time()),
        "has_active_work": False,
        "is_recording": VOICE_PID_FILE.exists()
    }
    status = load_json_file(STATUS_FILE, default_status)
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
    """
    Captures a clean screenshot of the screen/active window using grim + hyprctl.
    Does NOT extract OCR text; passes image directly to multimodal model.
    """
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
    msg = {
        "id": f"msg_{int(time.time()*1000)}_{len(history.get('messages', []))}",
        "role": role,
        "content": content,
        "timestamp": int(time.time()),
        "attachments": attachments or [],
        "actions": actions or []
    }
    history["messages"].append(msg)
    save_json_file(HISTORY_FILE, history)

def ask(query: str, image_path: Optional[str] = None, screen_context: bool = False, model: Optional[str] = None, provider: Optional[str] = None) -> Dict[str, Any]:
    """
    Executes a query through Hermes profile 'botty', attaching screenshot or media.
    """
    if not query.strip() and not image_path and not screen_context:
        return {"ok": False, "error": "Empty query and no attachment provided."}

    set_status("working", headline="Botty is thinking…", last_query=query)
    LOCK_FILE.write_text(str(os.getpid()))

    captured_context: Optional[Dict[str, Any]] = None
    target_image = image_path

    if screen_context:
        cap = capture_screen(mode="activewindow")
        if cap.get("ok"):
            captured_context = cap
            if not target_image:
                target_image = cap.get("image_path")

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

    # Prepare prompt text (clean ambient context, NO raw OCR dump)
    prompt_parts = []
    if captured_context:
        win = captured_context.get("active_window", {})
        win_title = win.get("title", "")
        win_class = win.get("class", "")
        if win_title or win_class:
            prompt_parts.append(f"[Active Window Context: {win_class} — '{win_title}']\n")

    user_query = query.strip() if query.strip() else "Analyze the attached screenshot context."
    prompt_parts.append(user_query)
    final_prompt = "\n".join(prompt_parts)

    # Save clean user message to history
    add_history_message("user", user_query, attachments=attachments)

    query_tmp = BOTTY_DATA_DIR / "current_query.txt"
    query_tmp.write_text(final_prompt, encoding="utf-8")

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
            err_msg = stderr_data.strip() or f"Hermes process exited with code {proc.returncode}"
            set_status("error", headline="Error", last_error=err_msg)
            add_history_message("assistant", f"⚠️ Error executing request: {err_msg}")
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

def get_providers_and_models() -> Dict[str, Any]:
    """Returns the full catalog of providers and all selectable models from Hermes."""
    active = get_active_model()
    providers_dict: Dict[str, Dict[str, Any]] = {}

    # 1. Read provider_models_cache.json
    p_cache_file = HERMES_DIR / "provider_models_cache.json"
    if p_cache_file.exists():
        try:
            with open(p_cache_file, "r", encoding="utf-8") as f:
                p_data = json.load(f)
            for p_id, info in p_data.items():
                models_list = info.get("models", [])
                if p_id not in providers_dict:
                    providers_dict[p_id] = {
                        "id": p_id,
                        "name": p_id.replace("-", " ").title(),
                        "models": []
                    }
                for m in models_list:
                    name = m.split("/")[-1].replace("-", " ").title() if "/" in m else m
                    providers_dict[p_id]["models"].append({"id": m, "name": name})
        except Exception:
            pass

    # 2. Read models_dev_cache.json
    dev_cache_file = HERMES_DIR / "models_dev_cache.json"
    if dev_cache_file.exists():
        try:
            with open(dev_cache_file, "r", encoding="utf-8") as f:
                dev_data = json.load(f)
            for p_id, p_info in dev_data.items():
                if isinstance(p_info, dict):
                    p_name = p_info.get("name", p_id.replace("-", " ").title())
                    models_dict = p_info.get("models", {})
                    if p_id not in providers_dict:
                        providers_dict[p_id] = {
                            "id": p_id,
                            "name": p_name,
                            "models": []
                        }
                    existing_ids = {m["id"] for m in providers_dict[p_id]["models"]}
                    if isinstance(models_dict, dict):
                        for m_id, m_data in models_dict.items():
                            if m_id not in existing_ids:
                                m_name = m_data.get("name", m_id) if isinstance(m_data, dict) else m_id
                                providers_dict[p_id]["models"].append({"id": m_id, "name": m_name})
                    elif isinstance(models_dict, list):
                        for m in models_dict:
                            m_id = m if isinstance(m, str) else m.get("id", str(m))
                            if m_id not in existing_ids:
                                providers_dict[p_id]["models"].append({"id": m_id, "name": m_id})
        except Exception:
            pass

    # Ensure Ollama local is present
    if "ollama" not in providers_dict:
        providers_dict["ollama"] = {
            "id": "ollama",
            "name": "Ollama (Local)",
            "models": [
                {"id": "ornith:latest", "name": "Ornith / Llama (Local)"},
                {"id": "ornith:9b", "name": "Ornith 9B"}
            ]
        }

    # Sort and prioritize primary providers
    priority = ["opencode-go", "openrouter", "ollama", "google", "anthropic", "openai", "copilot", "kimi", "z.ai", "minimax", "groq", "novita"]
    sorted_providers = []
    for p in priority:
        if p in providers_dict and providers_dict[p]["models"]:
            sorted_providers.append(providers_dict.pop(p))
    for p in sorted(providers_dict.keys()):
        if providers_dict[p]["models"]:
            sorted_providers.append(providers_dict[p])

    return {
        "ok": True,
        "active_model": active["model"],
        "active_provider": active["provider"],
        "providers": sorted_providers
    }

def set_model(model_id: str, provider_id: Optional[str] = None) -> Dict[str, Any]:
    """Updates the default model and provider in ~/.hermes/profiles/botty/config.yaml."""
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

        set_status(get_status().get("state", "idle"), headline="Model updated")
        return {"ok": True, "model": model_id, "provider": provider_id}
    except Exception as e:
        return {"ok": False, "error": f"Failed to set model: {str(e)}"}

# ── Voice Dictation ────────────────────────────────────────────────────────────

def dictate_start() -> Dict[str, Any]:
    """Starts voice recording from microphone to WAV file."""
    if VOICE_PID_FILE.exists():
        dictate_stop() # clear stale

    if VOICE_WAV_FILE.exists():
        VOICE_WAV_FILE.unlink()

    try:
        # Record with ffmpeg from default PulseAudio / PipeWire capture
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
    """Stops voice recording and runs local transcription via voxtype."""
    if not VOICE_PID_FILE.exists():
        return {"ok": False, "error": "No active recording."}

    try:
        pid = int(VOICE_PID_FILE.read_text().strip())
        # Terminate ffmpeg gracefully
        os.kill(pid, 15) # SIGTERM
        time.sleep(0.3)
    except Exception:
        pass
    finally:
        VOICE_PID_FILE.unlink(missing_ok=True)

    if not VOICE_WAV_FILE.exists() or VOICE_WAV_FILE.stat().st_size < 1000:
        return {"ok": False, "error": "Audio capture was empty."}

    # Transcribe via voxtype
    try:
        res = subprocess.run(["voxtype", "transcribe", str(VOICE_WAV_FILE)], capture_output=True, text=True, timeout=15)
        # Parse transcribed text
        output_lines = [l.strip() for l in res.stdout.splitlines() if l.strip()]
        # Filter out voxtype INFO/log lines
        text_lines = [l for l in output_lines if not l.startswith("Loading audio file") and not l.startswith("Audio format") and not l.startswith("Processing") and not re.search(r"^\d{4}-\d{2}-\d{2}T", l) and not l.startswith("whisper_")]
        transcribed = " ".join(text_lines).strip()
        
        return {"ok": True, "text": transcribed}
    except Exception as e:
        return {"ok": False, "error": f"Transcription failed: {str(e)}"}

def dictate_status() -> Dict[str, Any]:
    return {"ok": True, "recording": VOICE_PID_FILE.exists()}

# ── Memory & Skills ────────────────────────────────────────────────────────────

def get_memories() -> Dict[str, Any]:
    memories = []
    mem_file = HERMES_MEMORY_DIR / "MEMORY.md"
    if mem_file.exists():
        try:
            content = mem_file.read_text(encoding="utf-8")
            entries = [e.strip() for e in content.split("§") if e.strip()]
            for idx, entry in enumerate(entries):
                memories.append({
                    "id": f"mem_{idx}",
                    "type": "system",
                    "text": redact_secrets(entry),
                    "source": "MEMORY.md"
                })
        except Exception:
            pass

    user_file = HERMES_MEMORY_DIR / "USER.md"
    if user_file.exists():
        try:
            content = user_file.read_text(encoding="utf-8")
            entries = [e.strip() for e in content.split("§") if e.strip()]
            for idx, entry in enumerate(entries):
                memories.append({
                    "id": f"user_{idx}",
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
    
    try:
        content = ""
        if target_file.exists():
            content = target_file.read_text(encoding="utf-8").strip()
        
        if content:
            new_content = f"{content}\n§\n{text.strip()}\n"
        else:
            new_content = f"{text.strip()}\n"
            
        target_file.write_text(new_content, encoding="utf-8")
        set_status(get_status().get("state", "idle"), headline="Memory saved")
        return {"ok": True, "message": "Memory saved successfully."}
    except Exception as e:
        return {"ok": False, "error": f"Failed to save memory: {str(e)}"}

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
    ask_p.add_argument("--screen", dest="screen", action="store_true", help="Capture and include screen context")
    ask_p.add_argument("--model", dest="model", default=None, help="Model override")
    ask_p.add_argument("--provider", dest="provider", default=None, help="Provider override")

    cap_p = subparsers.add_parser("capture", help="Capture screen/window")
    cap_p.add_argument("--mode", dest="mode", default="activewindow", choices=["activewindow", "fullscreen", "region"])

    subparsers.add_parser("history", help="Get conversation history")
    subparsers.add_parser("clear", help="Clear conversation history")
    subparsers.add_parser("models", help="List available providers and models")
    
    set_m = subparsers.add_parser("set-model", help="Set active model")
    set_m.add_argument("model", help="Model identifier")
    set_m.add_argument("--provider", dest="provider", default=None, help="Provider name")

    # Dictation commands
    subparsers.add_parser("dictate-start", help="Start voice recording")
    subparsers.add_parser("dictate-stop", help="Stop voice recording & transcribe")
    subparsers.add_parser("dictate-status", help="Check voice recording status")

    subparsers.add_parser("memories", help="List memories")
    
    add_m = subparsers.add_parser("add-memory", help="Add persistent memory")
    add_m.add_argument("text", help="Memory content")
    add_m.add_argument("--user", dest="user", action="store_true", help="Store as user fact in USER.md")

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
        res = ask(args.query, image_path=args.image, screen_context=args.screen, model=args.model, provider=args.provider)
        print(json.dumps(res, ensure_ascii=False))
    elif args.command == "capture":
        print(json.dumps(capture_screen(args.mode), ensure_ascii=False))
    elif args.command == "history":
        print(json.dumps(get_history(), ensure_ascii=False))
    elif args.command == "clear":
        print(json.dumps(clear_history(), ensure_ascii=False))
    elif args.command == "models":
        print(json.dumps(get_providers_and_models(), ensure_ascii=False))
    elif args.command == "set-model":
        print(json.dumps(set_model(args.model, args.provider), ensure_ascii=False))
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
