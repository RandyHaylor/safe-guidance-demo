# ============================================================
# CLAUDE -p CHEAT SHEET: SESSIONS, FORKS, STREAMING, LOGGING
# ============================================================
# Every -p call here uses --output-format stream-json so events
# (assistant text, tool calls, results, init, rate limits) are
# emitted live as NDJSON to stdout. We redirect to a per-session
# log file so a separate terminal can tail it.
#
# Logs live at ~/.claude/logs/<session-id>.jsonl
# Make sure the dir exists once:
#   mkdir -p ~/.claude/logs
# ============================================================


# === 1. CREATE a brand-new session with a UUID you specify ===
SID=$(uuidgen)
claude -p "first prompt here" \
  --session-id "$SID" \
  --output-format stream-json \
  > ~/.claude/logs/$SID.jsonl 2>&1 &
PID=$!
echo "Session: $SID  PID: $PID"
# ERRORS if $SID already exists: "Session ID is already in use"


# === 2. REUSE / continue an existing session ===
claude -p "next prompt" \
  --resume "$SID" \
  --output-format stream-json \
  >> ~/.claude/logs/$SID.jsonl 2>&1 &
PID=$!
# Use >> (append) so all turns of one session land in the same log.
# ERRORS if $SID does not exist: "no conversation found"


# === 3. TAIL the log from a separate terminal ===
tail -f ~/.claude/logs/$SID.jsonl

# Pretty-print each event as it arrives (requires jq):
tail -f ~/.claude/logs/$SID.jsonl | jq -c .

# Just the assistant text content as it streams (rough filter):
tail -f ~/.claude/logs/$SID.jsonl \
  | jq -r 'select(.type=="assistant") | .message.content[]?.text // empty'


# === 4. FORK an existing session (branch off, parent untouched) ===
FORK=$(uuidgen)
claude -p "diverge here, try a different approach" \
  --resume "$SID" \
  --fork-session \
  --session-id "$FORK" \
  --output-format stream-json \
  > ~/.claude/logs/$FORK.jsonl 2>&1 &
FORK_PID=$!
# Fork inherits parent's full history. Parent session is unchanged.


# === 5. CONTINUE the fork with more turns ===
claude -p "next prompt on the fork" \
  --resume "$FORK" \
  --output-format stream-json \
  >> ~/.claude/logs/$FORK.jsonl 2>&1 &


# === 6. KILL a running -p process ===
kill $PID                   # graceful (SIGTERM, flushes JSONL)
kill -9 $PID                # force (SIGKILL, may leave partial line)
# Lost the PID? Kill by session-id substring in the command line:
pkill -f "claude.*$SID"


# === 7. ABANDON / DISCARD a session (typically a fork) ===
kill "$FORK_PID" 2>/dev/null
find ~/.claude/projects -name "${FORK}.jsonl" -delete
# After this, --resume "$FORK" returns "no conversation found".


# === 8. CHECK if a session ID exists on disk ===
find ~/.claude/projects -name "${SID}.jsonl" -type f
# Empty = doesn't exist. Any output = exists.


# === 9. PARSE the final result text from a finished call ===
# Final event has type="result" with .result containing the text.
jq -r 'select(.type=="result") | .result' ~/.claude/logs/$SID.jsonl


# === 10. TYPICAL PATTERN: create-or-resume with fallback ===
if find ~/.claude/projects -name "${SID}.jsonl" -type f | grep -q .; then
  FLAG="--resume"
else
  FLAG="--session-id"
fi
claude -p "your prompt" \
  $FLAG "$SID" \
  --output-format stream-json \
  >> ~/.claude/logs/$SID.jsonl 2>&1


# ============================================================
# COMMON FLAG REFERENCE (all valid in -p mode)
# ============================================================
#   --session-id <uuid>           create new, fail if exists
#   --resume <uuid>               continue existing, fail if missing
#   --fork-session                branch from --resume target
#   --output-format stream-json   live NDJSON event stream
#   --output-format json          buffered single-envelope (default)
#   --model haiku|sonnet|opus     pick model tier
#   --max-turns 1                 hard cap on agentic loop
#   --system-prompt "..."         REPLACE default system prompt
#   --append-system-prompt "..."  add to default system prompt
#   --permission-mode dontAsk     deny anything not allow-listed
#   --max-budget-usd 0.50         hard cost ceiling
# ============================================================


# ============================================================
# GOTCHAS WORTH KNOWING
# ============================================================
# - Stream events are one JSON object per line. Don't try to
#   parse the file as a single JSON document.
# - If a process is killed mid-stream, the log may end on a
#   partial line. Parsers should tolerate trailing junk.
# - --session-id and --resume are mutually exclusive. Don't
#   pass both for the same call (except via --fork-session,
#   which is the documented exception).
# - Sessions live under ~/.claude/projects/<cwd-hash>/<sid>.jsonl
#   where cwd-hash encodes the directory you ran claude from.
#   --resume must run with the same cwd or it won't find the file.
# - Auth uses your existing `claude login` OAuth. No API key
#   needed. Don't pass --bare (it requires ANTHROPIC_API_KEY).
# - First call in a fresh session loads the full Claude Code
#   harness (~33K tokens). Subsequent --resume calls hit the
#   prompt cache and run much faster (~1-2s vs 5-8s).
# ============================================================
