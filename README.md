# safe-guidance-demo

**An LLM that drives a real UX flow but cannot hallucinate user-visible text.**

The model is constrained to two outputs per turn:

1. an ordered list of phrase IDs from a fixed approved library, and
2. one structured command from a small whitelist (`navigate`, `update_preference`, `no_action`).

The host app assembles the reply from approved phrases and executes the command only if it matches the whitelist. Anything the model might *want* to say off-script is unreachable.

---

## What this proves

**A general-purpose LLM can run a UX flow without ever speaking in its own words.** Every word shown to the user came from a human-curated phrase file. Every state mutation went through a whitelist.

**The same prompt safely carries non-text actions.** `navigate` and `update_preference` are validated against allowed argument values before the host touches state.

**Context-awareness without free text.** The agent receives `user_state` each turn, so it can choose phrases like "you're already on that screen" — but only by selecting a pre-written phrase, never by inventing one.

**Multi-turn refinement comes free.** The Claude CLI's persistent session keeps the phrase library + schema in cache; subsequent turns only send `{user_state, user_message}`.

---

## Example session (real output)

This is verbatim from a scripted run of `chat_app.py`. Each turn shows the model's chosen phrase IDs, the validated command, the state effect, and the assembled user-facing message.

```
╔══════════════════════════════════════════════════════════════╗
║  TURN 1                                                      ║
╚══════════════════════════════════════════════════════════════╝
┌─ you ─────────────────────────────────────────────────────────
│ hi what can I do
┌─ agent JSON ──────────────────────────────────────────────────
│ phrase_ids (2): ack_sorry_unsupported, filler_period
│ command:    no_action
┌─ bot ─────────────────────────────────────────────────────────
│ Sorry, I can only update cabinet color and wall color, or
│ move you between approved screens..

╔══════════════════════════════════════════════════════════════╗
║  TURN 2                                                      ║
╚══════════════════════════════════════════════════════════════╝
┌─ you ─────────────────────────────────────────────────────────
│ change my cabinet color to green
┌─ agent JSON ──────────────────────────────────────────────────
│ phrase_ids (6): confirm_will_update, noun_cabinet, noun_color, filler_to, color_green, filler_period
│ command:    update_preference(key=cabinet_color, value=green)
┌─ status ──────────────────────────────────────────────────────
│ ✓ preferences.cabinet_color: oak -> green
┌─ bot ─────────────────────────────────────────────────────────
│ Great! I'll update your cabinet color to green.

╔══════════════════════════════════════════════════════════════╗
║  TURN 3                                                      ║
╚══════════════════════════════════════════════════════════════╝
┌─ you ─────────────────────────────────────────────────────────
│ now make the walls sage
┌─ agent JSON ──────────────────────────────────────────────────
│ phrase_ids (6): confirm_will_update, noun_wall, noun_color, filler_to, color_sage, filler_period
│ command:    update_preference(key=wall_color, value=sage)
┌─ status ──────────────────────────────────────────────────────
│ ✓ preferences.wall_color: beige -> sage
┌─ bot ─────────────────────────────────────────────────────────
│ Great! I'll update your wall color to sage.

╔══════════════════════════════════════════════════════════════╗
║  TURN 4                                                      ║
╚══════════════════════════════════════════════════════════════╝
┌─ you ─────────────────────────────────────────────────────────
│ take me to the design page
┌─ agent JSON ──────────────────────────────────────────────────
│ phrase_ids (3): confirm_navigating_to, screen_design, filler_period
│ command:    navigate(screen=design)
┌─ status ──────────────────────────────────────────────────────
│ ✓ current_screen: home -> design
┌─ bot ─────────────────────────────────────────────────────────
│ Sure, taking you to the design page.

╔══════════════════════════════════════════════════════════════╗
║  TURN 5                                                      ║
╚══════════════════════════════════════════════════════════════╝
┌─ you ─────────────────────────────────────────────────────────
│ paint my walls neon pink
┌─ agent JSON ──────────────────────────────────────────────────
│ phrase_ids (2): ack_color_unsupported, filler_period
│ command:    no_action
┌─ bot ─────────────────────────────────────────────────────────
│ Sorry, that color isn't in the approved palette..
```

**Turn 5 is the headline.** A free LLM would happily paint the walls neon pink. This one literally cannot — `neon pink` is not in `data/approved_actions.json`, and the model has no phrase for it either. Both layers refuse independently.

---

## How it works

```
         ┌──────────────────────────────────────────────────┐
user ──► │  chat_app.py                                     │
         │                                                  │
         │   build prompt = {phrase_library, actions,       │
         │                   schema, user_state, message}   │
         │                            │                     │
         │                            ▼                     │
         │   claude -p  ───►  {"phrase_ids": [...],         │
         │                     "command": {...}}            │
         │                            │                     │
         │           ┌────────────────┴───────────────┐     │
         │           ▼                                ▼     │
         │   assemble text from              validate cmd   │
         │   approved phrase IDs             against        │
         │           │                       whitelist      │
         │           │                                │     │
         │           ▼                                ▼     │
         │     user-facing reply           mutate user.json │
         └──────────────────────────────────────────────────┘
```

The model never produces a string that reaches the user. It produces *identifiers* that the host resolves against a curated dictionary.

---

## Running it

### Prerequisites

- **Claude CLI** on PATH (`which claude`)
- **OAuth'd via `claude login`** — same auth as Claude Code; no API key required
- **Python 3.10+** — standard library only

### Run

```
python3 chat_app.py
```

Type at the `you>` prompt. `quit` or Ctrl+D to exit. Per-turn verbose detail (full prompt, raw model JSON, validation results) goes to `~/.claude/logs/<session-id>.jsonl`. The console shows the clean boxed summary you see above.

### Files

```
data/phrases.json           approved phrase library (greetings, nouns, colors, screens, etc.)
data/approved_actions.json  action whitelist with allowed arg values
data/response_schema.json   required JSON shape for every model reply
data/user.json              persistent user state (current_screen + preferences)
system_prompt.txt           hard rules sent as --system-prompt
chat_app.py                 the host app
```

---

## Why this pattern is interesting

The general idea: **let the LLM choose from a menu, not write the message.** The model still does the hard part — interpreting messy natural-language input — while the host retains absolute control over what the user sees and what mutations happen.

The same shape generalizes to:

- **IVR-style customer support** — canned responses + bounded actions (create ticket, transfer to billing)
- **B2B configurators** — pick SKUs from a catalog; never invent prices
- **Healthcare / legal triage** — route to approved next steps; no free-form medical or legal advice
- **Voice assistants** on top of a constrained device API

---

## Porting to a different LLM

The Claude-specific surface lives in one function: `call_claude()` in `chat_app.py`. Everything else (phrase assembly, command validation, state mutation, prompt construction) is LLM-agnostic.

**Replace `call_claude()`** with a function of the same signature that returns the raw model text. The rest of the app does not care which model produced it.

**Replicate the persistent session.** This demo uses `claude -p --session-id` for cross-turn memory. If your SDK lacks built-in sessions, maintain a `messages` list and re-send each turn (OpenAI, Anthropic Messages API, Gemini, Bedrock, etc.). On turn 1 send the full library + schema; on subsequent turns send only `{user_state, user_message}` — `build_turn_user_message()` already does this.

**Use JSON-mode if available.** It eliminates `parse_agent_json()`'s defensive code-fence stripping. Local change.

**Swap auth.** Replace OAuth with whatever your target uses (API key env var, service account, etc.).

The phrase library, action whitelist, response schema, validator, assembler, and state file are all provider-neutral and stay as-is.
