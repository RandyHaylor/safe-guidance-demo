# AI Agent 'Safe Guidance' System Design

**A user-facing ai agent approach that safely responds and drives real application commands but cannot hallucinate user-visible text or perform any harmful/unapproved actions.**

Each reply has two parts — the user-visible text and the action to take — and **both are picked from a whitelist, not written by the model**:

- the user-visible text is an ordered list of phrase IDs drawn from a fixed approved phrase library, and
- the action is one structured command (`navigate`, `update_preference`, `no_action`) whose argument values must also be on the whitelist.

The host app assembles the reply from approved phrases and executes the command only if it matches the whitelist. Anything the model might *want* to say or *want* to do off-script is unreachable.

The number of phrases or actions per turn isn't the point — the point is that every word and every state change came from a human-curated set.

---

## What this proves

**A general-purpose LLM can run a UX flow without ever speaking in its own words.** Every word shown to the user came from a human-curated phrase file. Every state mutation went through a whitelist.

**The model has zero built-in tools.** The host launches the Claude session with `--tools "" --strict-mcp-config`, so Bash, Read, Edit, WebFetch, MCP servers, etc. are all stripped out. The session's `tools` array is literally `[]`. The model's *only* output channel is the JSON reply.

**That JSON reply IS the model's tool-call interface — and the tool calls are real.** From the LLM's point of view it is not roleplaying or describing actions: it emits `{"command": {"action": "update_preference", "args": {"key": "cabinet_color", "value": "green"}}}` and the host code actually mutates `user.json`, actually re-renders the user-visible text from approved phrases, actually navigates the UI. The schema-validated JSON object is functionally a custom tool-call protocol we built on top of the LLM — same shape as native tool use, but with a tiny, fully whitelisted surface (`navigate`, `update_preference`, `no_action`) and a fixed phrase library standing in for free-form `text` content.

**Context-awareness without free text.** The agent receives `user_state` each turn, so it can choose phrases like "you're already on that screen" — but only by selecting a pre-written phrase, never by inventing one.

**Multi-turn refinement comes free.** The Claude CLI's persistent session keeps the phrase library + schema in cache; subsequent turns only send `{user_state, user_message}`.

---

## Example session

A real conversation, verbatim from `chat_app.py`:

```
user:   hi what can I do
agent:  Sorry, I can only update cabinet color and wall color,
        or move you between approved screens.

user:   change my cabinet color to green
agent:  Great! I'll update your cabinet color to green.

user:   now make the walls sage
agent:  Great! I'll update your wall color to sage.

user:   take me to the design page
agent:  Sure, taking you to the design page.

user:   paint my walls neon pink
agent:  Sorry, that color isn't in the approved palette.
```

The last turn is the headline: a free LLM would happily paint the walls neon pink. This one **cannot** — `neon pink` is not in the approved action whitelist, and there is no phrase in the library that even says "neon pink".

---

### How the agent built two of those replies

The agent never *wrote* those sentences. For every reply, it picked an ordered list of phrase IDs; the host looked each one up in `data/phrases.json` and concatenated the results.

#### "Great! I'll update your cabinet color to green."

Agent picks these phrase IDs:

```
confirm_will_update   →   "Great! I'll update your"
noun_cabinet          →   "cabinet"
noun_color            →   "color"
filler_to             →   "to"
color_green           →   "green"
filler_period         →   "."
```

Host joins them with spaces (punctuation attaches to the previous token):

```
"Great! I'll update your cabinet color to green."
```

And in the same reply the agent emits a structured command:

```
update_preference(key=cabinet_color, value=green)
    →  mutates user.json:  cabinet_color: oak → green
```

#### "Sorry, that color isn't in the approved palette."

Agent picks:

```
ack_color_unsupported   →   "Sorry, that color isn't in the approved palette"
filler_period           →   "."
```

Assembled reply:

```
"Sorry, that color isn't in the approved palette."
```

Command:

```
no_action   →   nothing mutates
```

That last turn — the "neon pink" refusal — is the headline. The agent has no way to *say* "neon pink" (no phrase) and no way to *do* "neon pink" (not in `data/approved_actions.json`). Both layers reject it independently.

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
