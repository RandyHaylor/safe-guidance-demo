#!/usr/bin/env python3
"""
safe-guidance-demo chat app.

Talks to a persistent `claude -p` session. The agent is constrained to reply
ONLY with a JSON object of the form:

    { "phrase_ids": [...], "command": {"action": "...", "args": {...}} }

The host app:
  - assembles user-facing text from the approved phrase library,
  - validates the command against the approved action whitelist,
  - executes navigate / update_preference against user.json,
  - logs every input and output verbosely.

Usage:
    python3 chat_app.py
"""

import json
import os
import subprocess
import sys
import textwrap
import uuid
from datetime import datetime
from pathlib import Path


REPO_DIR              = Path(__file__).resolve().parent
DATA_DIR              = REPO_DIR / "data"
PHRASES_FILE          = DATA_DIR / "phrases.json"
APPROVED_ACTIONS_FILE = DATA_DIR / "approved_actions.json"
RESPONSE_SCHEMA_FILE  = DATA_DIR / "response_schema.json"
USER_STATE_FILE       = DATA_DIR / "user.json"
SYSTEM_PROMPT_FILE    = REPO_DIR / "system_prompt.txt"
LOG_DIR               = Path.home() / ".claude" / "logs"


def log_section(title: str) -> None:
    bar = "=" * 70
    print(f"\n{bar}\n  {title}\n{bar}", flush=True)


def log_kv(label: str, value) -> None:
    """Verbose-style line to STDOUT. Used only for startup/config and errors."""
    if isinstance(value, (dict, list)):
        rendered = json.dumps(value, indent=2)
    else:
        rendered = str(value)
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {label}:\n{textwrap.indent(rendered, '    ')}", flush=True)


def record_to_logfile(log_path: Path, label: str, value) -> None:
    """Append a labeled record to the per-session log file ONLY (no stdout)."""
    if isinstance(value, (dict, list)):
        rendered = json.dumps(value, indent=2)
    else:
        rendered = str(value)
    with log_path.open("a") as fh:
        fh.write(f"[{datetime.now().isoformat(timespec='seconds')}] {label}:\n")
        fh.write(textwrap.indent(rendered, "    "))
        fh.write("\n")


def render_turn_summary(turn_index: int,
                        user_input: str,
                        phrase_ids: list,
                        command: dict,
                        validation_ok: bool,
                        validation_reason: str,
                        effect_message: str,
                        assembled_text: str,
                        user_state: dict) -> str:
    """Build a clean, human-readable end-of-turn block for the console."""
    width = 64
    rule  = "─" * width

    def section(name: str) -> str:
        return f"\n┌─ {name} {'─' * (width - len(name) - 4)}"

    lines = []
    lines.append("")
    lines.append("╔" + "═" * (width - 2) + "╗")
    lines.append(f"║  TURN {turn_index}".ljust(width - 1) + "║")
    lines.append("╚" + "═" * (width - 2) + "╝")

    lines.append(section("you"))
    lines.append(f"│ {user_input}")

    lines.append(section("agent JSON"))
    lines.append(f"│ phrase_ids ({len(phrase_ids)}): {', '.join(phrase_ids) if phrase_ids else '(none)'}")
    action = command.get("action", "?")
    args   = command.get("args", {})
    if args:
        args_str = ", ".join(f"{k}={v}" for k, v in args.items())
        lines.append(f"│ command:    {action}({args_str})")
    else:
        lines.append(f"│ command:    {action}")

    lines.append(section("status"))
    if not validation_ok:
        lines.append(f"│ ⚠ command rejected: {validation_reason}")
    elif action == "update_preference":
        lines.append(f"│ ✓ {effect_message}")
    elif action == "navigate":
        lines.append(f"│ ✓ {effect_message}")
    else:
        lines.append("│ (no side effect)")

    lines.append(section("bot"))
    for chunk in textwrap.wrap(assembled_text, width=width - 4) or [""]:
        lines.append(f"│ {chunk}")

    lines.append(section("state after"))
    lines.append(f"│ screen={user_state.get('current_screen')}   "
                 f"cabinet={user_state['preferences'].get('cabinet_color')}   "
                 f"wall={user_state['preferences'].get('wall_color')}")

    lines.append("└" + rule[1:])
    return "\n".join(lines)


def render_session_summary(session_id: str,
                           log_path: Path,
                           user_state: dict,
                           total_turns: int) -> str:
    width = 64
    lines = []
    lines.append("")
    lines.append("═" * width)
    lines.append("  SESSION ENDED")
    lines.append("═" * width)
    lines.append(f"  turns:          {total_turns}")
    lines.append(f"  session_id:     {session_id}")
    lines.append(f"  transcript log: {log_path}")
    lines.append("")
    lines.append("  final user state:")
    lines.append(f"    current_screen : {user_state.get('current_screen')}")
    lines.append(f"    cabinet_color  : {user_state['preferences'].get('cabinet_color')}")
    lines.append(f"    wall_color     : {user_state['preferences'].get('wall_color')}")
    lines.append("═" * width)
    return "\n".join(lines)


def load_json_file(path: Path) -> dict:
    with path.open("r") as fh:
        return json.load(fh)


def save_user_state(state: dict) -> None:
    with USER_STATE_FILE.open("w") as fh:
        json.dump(state, fh, indent=2)
        fh.write("\n")


def flatten_phrase_library(phrase_library: dict) -> dict:
    """Return {phrase_id: text} across all categories, dropping _comment keys."""
    flat = {}
    for category, entries in phrase_library.items():
        if category.startswith("_"):
            continue
        if not isinstance(entries, dict):
            continue
        for phrase_id, text in entries.items():
            flat[phrase_id] = text
    return flat


def assemble_response_text(phrase_ids: list, flat_phrases: dict) -> str:
    """Join approved phrases. Punctuation phrases attach without leading space."""
    parts = []
    for pid in phrase_ids:
        if pid not in flat_phrases:
            parts.append(f"[UNKNOWN_PHRASE:{pid}]")
            continue
        text = flat_phrases[pid]
        if pid.startswith("filler_") and text in (".", "?", "!", ","):
            if parts:
                parts[-1] = parts[-1] + text
            else:
                parts.append(text)
        else:
            parts.append(text)
    return " ".join(parts)


def validate_command(command: dict, approved_actions: dict) -> tuple[bool, str]:
    if not isinstance(command, dict):
        return False, "command is not an object"
    action = command.get("action")
    args   = command.get("args", {})
    if action not in approved_actions or action.startswith("_"):
        return False, f"action '{action}' is not in approved_actions.json"
    spec = approved_actions[action]
    allowed_args = spec.get("args", {})
    for key, val in args.items():
        if key not in allowed_args:
            return False, f"arg '{key}' not allowed for action '{action}'"
        if val not in allowed_args[key]:
            return False, f"arg value '{val}' not allowed for {action}.{key}"
    return True, "ok"


def execute_command(command: dict, user_state: dict) -> str:
    action = command.get("action")
    args   = command.get("args", {})
    if action == "no_action":
        return "no side effect"
    if action == "navigate":
        target = args["screen"]
        prev   = user_state["current_screen"]
        user_state["current_screen"] = target
        save_user_state(user_state)
        return f"current_screen: {prev} -> {target}"
    if action == "update_preference":
        key, value = args["key"], args["value"]
        prev = user_state["preferences"].get(key)
        user_state["preferences"][key] = value
        save_user_state(user_state)
        return f"preferences.{key}: {prev} -> {value}"
    return f"unhandled action: {action}"


def build_turn_user_message(user_text: str,
                            phrase_library: dict,
                            approved_actions: dict,
                            response_schema: dict,
                            user_state: dict,
                            is_first_turn: bool) -> str:
    """
    On the first turn we send the full library + schema so the agent has it
    in cache. On subsequent turns we only need the user's message + state,
    since the session keeps the prior context.
    """
    if is_first_turn:
        bundle = {
            "instructions":      "Respond ONLY with the JSON object described in response_schema. No other text.",
            "phrase_library":    phrase_library,
            "approved_actions":  approved_actions,
            "response_schema":   response_schema,
            "user_state":        user_state,
            "user_message":      user_text,
        }
    else:
        bundle = {
            "user_state":   user_state,
            "user_message": user_text,
        }
    return json.dumps(bundle, indent=2)


def call_claude(prompt_text: str,
                session_id: str,
                is_first_turn: bool,
                system_prompt_path: Path,
                log_path: Path) -> str:
    """
    Run claude -p in JSON output mode (buffered, simpler to parse than
    stream-json for a chat loop). Append-log everything to ~/.claude/logs/<sid>.jsonl.
    """
    cmd = [
        "claude", "-p", prompt_text,
        "--output-format", "json",
        "--system-prompt", system_prompt_path.read_text(),
        "--max-turns", "1",
        # Hard-disable every built-in tool. The CLI's own --help says:
        #   --tools <tools...>  Use "" to disable all tools, "default" to use all tools, ...
        # We verified this empties the session's tools array (init event reports tools: []),
        # so the model cannot Bash/Read/Edit/WebFetch/etc. — its only output channel
        # is the JSON reply we then interpret as our own custom tool protocol.
        "--tools", "",
        # Belt-and-suspenders: also ignore any ambient MCP servers (which add tools too).
        "--strict-mcp-config",
    ]
    if is_first_turn:
        cmd += ["--session-id", session_id]
    else:
        cmd += ["--resume", session_id]

    record_to_logfile(log_path, "CLAUDE CLI argv",
                      cmd[:3] + ["...truncated prompt..."] + cmd[4:])
    print("  …calling claude…", flush=True)

    proc = subprocess.run(cmd, capture_output=True, text=True)
    raw_stdout = proc.stdout
    raw_stderr = proc.stderr

    with log_path.open("a") as fh:
        fh.write(f"\n--- TURN @ {datetime.now().isoformat()} ---\n")
        fh.write("STDIN_PROMPT:\n" + prompt_text + "\n")
        fh.write("STDOUT:\n" + raw_stdout + "\n")
        if raw_stderr:
            fh.write("STDERR:\n" + raw_stderr + "\n")

    if proc.returncode != 0:
        record_to_logfile(log_path, "CLAUDE STDERR", raw_stderr)
        raise RuntimeError(f"claude exited {proc.returncode}: {raw_stderr.strip()[:200]}")

    envelope = json.loads(raw_stdout)
    # `--output-format json` returns a JSON ARRAY of events, not a single
    # object. The final result event has type="result" and a `result` field
    # containing the assistant's text. Be defensive: also accept a plain dict
    # in case the CLI behavior changes.
    if isinstance(envelope, list):
        record_to_logfile(log_path, "CLAUDE ENVELOPE (event count, types)",
                          [e.get("type") if isinstance(e, dict) else type(e).__name__
                           for e in envelope])
        result_event = next(
            (e for e in envelope
             if isinstance(e, dict) and e.get("type") == "result"),
            None,
        )
        if result_event is None:
            raise RuntimeError("no 'result' event found in claude envelope")
        return result_event.get("result", "")
    if isinstance(envelope, dict):
        record_to_logfile(log_path, "CLAUDE ENVELOPE (top-level keys)",
                          list(envelope.keys()))
        return envelope.get("result", "")
    raise RuntimeError(f"unexpected envelope type: {type(envelope).__name__}")


def parse_agent_json(result_text: str) -> dict:
    """Strip stray fences/whitespace, parse JSON."""
    s = result_text.strip()
    if s.startswith("```"):
        lines = [ln for ln in s.splitlines() if not ln.startswith("```")]
        s = "\n".join(lines).strip()
    return json.loads(s)


def main() -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    phrase_library   = load_json_file(PHRASES_FILE)
    approved_actions = load_json_file(APPROVED_ACTIONS_FILE)
    response_schema  = load_json_file(RESPONSE_SCHEMA_FILE)
    user_state       = load_json_file(USER_STATE_FILE)
    flat_phrases     = flatten_phrase_library(phrase_library)

    session_id = str(uuid.uuid4())
    log_path   = LOG_DIR / f"{session_id}.jsonl"

    width = 64
    print()
    print("═" * width)
    print("  safe-guidance-demo chat app")
    print("═" * width)
    print(f"  session_id    : {session_id}")
    print(f"  phrase library: {len(flat_phrases)} approved phrases")
    print(f"  initial state : screen={user_state.get('current_screen')}, "
          f"cabinet={user_state['preferences'].get('cabinet_color')}, "
          f"wall={user_state['preferences'].get('wall_color')}")
    print(f"  log file      : {log_path}")
    print("═" * width)

    is_first_turn = True
    turn_index    = 0
    print("\nType a message (or 'quit' to exit). Try things like:")
    print("  - hi what can I do")
    print("  - change my cabinet color to green")
    print("  - take me to the design page")
    print("  - paint my walls neon pink   (should be refused)")
    print(f"\n(verbose per-turn details written to {log_path})\n")

    while True:
        try:
            user_text = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not user_text:
            continue
        if user_text.lower() in ("quit", "exit"):
            break

        turn_index += 1

        # Verbose details go to the per-session log file ONLY (not stdout).
        record_to_logfile(log_path, f"TURN {turn_index} (first_turn={is_first_turn})", "")
        record_to_logfile(log_path, "user_input", user_text)

        prompt_text = build_turn_user_message(
            user_text, phrase_library, approved_actions,
            response_schema, user_state, is_first_turn,
        )
        record_to_logfile(log_path, "prompt_to_claude (full)", prompt_text)

        try:
            raw_reply = call_claude(prompt_text, session_id, is_first_turn,
                                    SYSTEM_PROMPT_FILE, log_path)
        except Exception as exc:
            print(f"\n⚠ ERROR calling claude: {exc}\n")
            record_to_logfile(log_path, "ERROR calling claude", str(exc))
            is_first_turn = False
            continue

        # Subprocess returned 0; the session file is on disk. Switch to
        # --resume for all subsequent turns regardless of what we parse next.
        is_first_turn = False
        record_to_logfile(log_path, "raw_agent_reply", raw_reply)

        try:
            parsed = parse_agent_json(raw_reply)
        except json.JSONDecodeError as exc:
            print(f"\n⚠ ERROR parsing agent JSON: {exc}\n")
            record_to_logfile(log_path, "ERROR parsing agent JSON", str(exc))
            continue

        record_to_logfile(log_path, "parsed_agent_json", parsed)

        phrase_ids = parsed.get("phrase_ids", [])
        command    = parsed.get("command", {"action": "no_action", "args": {}})

        unknown = [pid for pid in phrase_ids if pid not in flat_phrases]
        if unknown:
            record_to_logfile(log_path, "REJECTED unknown phrase_ids", unknown)

        ok, reason = validate_command(command, approved_actions)
        record_to_logfile(log_path, "command_validation",
                          {"ok": ok, "reason": reason, "command": command})

        assembled = assemble_response_text(phrase_ids, flat_phrases)
        record_to_logfile(log_path, "assembled_user_text", assembled)

        if ok:
            effect = execute_command(command, user_state)
        else:
            effect = "skipped (rejected)"
        record_to_logfile(log_path, "command_effect", effect)
        record_to_logfile(log_path, "user_state_after", user_state)

        # Clean human-readable summary block to stdout.
        print(render_turn_summary(
            turn_index    = turn_index,
            user_input    = user_text,
            phrase_ids    = phrase_ids,
            command       = command,
            validation_ok = ok,
            validation_reason = reason,
            effect_message    = effect,
            assembled_text    = assembled,
            user_state        = user_state,
        ))

    print(render_session_summary(session_id, log_path, user_state, turn_index))
    record_to_logfile(log_path, "session_ended_final_state", user_state)
    return 0


if __name__ == "__main__":
    sys.exit(main())
