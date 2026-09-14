#!/usr/bin/env bash
# OK Labs: drive a coding agent through the REAL stops of a rebase (Sync v3).
#
# Two modes, one driver:
#
#   conflict  (default)  The rebase is stopped on a conflict the mechanical
#                        resolver (rebase-autoresolve.py) refused. For every
#                        remaining stop: try the resolver first; when it
#                        refuses, brief the agent with the patch, the files
#                        and the upstream history, let it edit the working
#                        tree, verify that no markers remain, stage, continue.
#                        A SKIP verdict drops the patch (superseded upstream).
#   typecheck            The rebase finished but `pnpm typecheck` is red
#                        (--typecheck-log). Brief the agent with the errors,
#                        re-run --verify-cmd, commit the fix as a new
#                        `oklabs:` patch on top of the stack.
#
# The agent is Codex CLI (`codex exec`, non-interactive) through CLIProxyAPI
# on maestro (OpenAI-compatible Responses endpoint); REPAIR_AGENT=claude
# selects Claude Code (`claude -p`) through the same proxy. The agent gets an
# unrestricted shell: the CI container is the sandbox.
#
# Bounded on purpose: --max-iterations agent calls in total and a
# --budget-seconds wall clock; a single call never exceeds
# REPAIR_CALL_TIMEOUT. Everything the agent said and ran lands in
# --transcript; one JSON line per stop lands in --summary for the receipt.
# Exit 0 only when the rebase completed (conflict mode) or the verify command
# is green (typecheck mode); any other outcome exits non-zero with the reason
# on stderr and in the transcript. The rebase is left as it is on failure so
# the caller can `git rebase --abort` and alert.
#
# Env:  CLIPROXY_API_KEY (required)  CLIPROXY_BASE_URL (default maestro)
#       REPAIR_AGENT=codex|claude    REPAIR_MODEL (default gpt-6-astra)
#       REPAIR_CALL_TIMEOUT (seconds per agent call, default 1200)
#       REPAIR_REASONING (codex model_reasoning_effort, default high)
set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIB="$HERE/rebase-repair-lib.py"
TEMPLATE="$HERE/rebase-repair-prompt.md"
SCHEMA="$HERE/rebase-repair-schema.json"
AUTORESOLVE="$HERE/rebase-autoresolve.py"

MODE=conflict
MAX_ITER=6
BUDGET=2700
TRANSCRIPT=""
SUMMARY=""
OLD_BASE=""
NEW_BASE=""
TYPECHECK_LOG=""
VERIFY_CMD="pnpm run typecheck"

usage() {
  sed -n '2,32p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  echo
  echo "usage: rebase-repair.sh [--max-iterations N] [--budget-seconds S] [--transcript F] [--summary F]"
  echo "                        [--old-base SHA] [--new-base SHA] [--autoresolve PATH]"
  echo "                        [--typecheck-log F [--verify-cmd CMD]]"
}

while [ $# -gt 0 ]; do
  case "$1" in
    --max-iterations) MAX_ITER="$2"; shift 2 ;;
    --budget-seconds) BUDGET="$2"; shift 2 ;;
    --transcript) TRANSCRIPT="$2"; shift 2 ;;
    --summary) SUMMARY="$2"; shift 2 ;;
    --old-base) OLD_BASE="$2"; shift 2 ;;
    --new-base) NEW_BASE="$2"; shift 2 ;;
    --autoresolve) AUTORESOLVE="$2"; shift 2 ;;
    --typecheck-log) TYPECHECK_LOG="$2"; MODE=typecheck; shift 2 ;;
    --verify-cmd) VERIFY_CMD="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "rebase-repair: unknown argument $1" >&2; usage >&2; exit 2 ;;
  esac
done

: "${CLIPROXY_API_KEY:?CLIPROXY_API_KEY is required (Forgejo repo secret)}"
CLIPROXY_BASE_URL="${CLIPROXY_BASE_URL:-http://10.10.0.23:23020/v1}"
REPAIR_AGENT="${REPAIR_AGENT:-codex}"
REPAIR_MODEL="${REPAIR_MODEL:-gpt-6-astra}"
REPAIR_CALL_TIMEOUT="${REPAIR_CALL_TIMEOUT:-1200}"
REPAIR_REASONING="${REPAIR_REASONING:-high}"
STATE_DIR="${REPAIR_STATE_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/rebase-repair}"
mkdir -p "$STATE_DIR"
[ -n "$TRANSCRIPT" ] || TRANSCRIPT="$STATE_DIR/transcript.txt"
[ -n "$SUMMARY" ] || SUMMARY="$STATE_DIR/summary.jsonl"
touch "$TRANSCRIPT"

REPO="$(git rev-parse --show-toplevel 2>/dev/null)" || { echo "rebase-repair: not inside a git repository" >&2; exit 2; }
cd "$REPO" || exit 2
REBASE_DIR="$(git rev-parse --git-path rebase-merge)"
START=$(date +%s)
DEADLINE=$((START + BUDGET))
ITER=0

log() { printf '%s %s\n' "$(date -u +%H:%M:%S)" "$*" | tee -a "$TRANSCRIPT" >&2; }
fail() { log "rebase-repair: FAILED — $*"; echo "$*" > "$STATE_DIR/failure-reason.txt"; exit 1; }
remaining() { echo $(( DEADLINE - $(date +%s) )); }
in_progress() { [ -d "$REBASE_DIR" ]; }
unmerged() { git diff --name-only --diff-filter=U; }

# --- agent runners -----------------------------------------------------------

write_codex_home() {
  CODEX_HOME="$STATE_DIR/codex-home"
  mkdir -p "$CODEX_HOME"
  cat > "$CODEX_HOME/config.toml" <<EOF
model = "$REPAIR_MODEL"
model_provider = "cliproxy"
model_reasoning_effort = "$REPAIR_REASONING"
approval_policy = "never"
sandbox_mode = "danger-full-access"
personality = "pragmatic"
[model_providers.cliproxy]
name = "CLIProxyAPI (maestro)"
base_url = "$CLIPROXY_BASE_URL"
wire_api = "responses"
env_key = "CLIPROXY_API_KEY"
EOF
  export CODEX_HOME
}

# run_agent PROMPT_FILE LAST_FILE EVENTS_FILE TIMEOUT
run_agent() {
  local prompt="$1" last="$2" events="$3" limit="$4" rc
  : > "$last"
  case "$REPAIR_AGENT" in
    codex)
      command -v codex >/dev/null 2>&1 || fail "codex CLI not installed"
      write_codex_home
      timeout -k 30 "$limit" codex exec \
        --dangerously-bypass-approvals-and-sandbox \
        --skip-git-repo-check \
        -C "$REPO" --json \
        --output-schema "$SCHEMA" \
        -o "$last" - < "$prompt" > "$events" 2> "$events.stderr"; rc=$?
      ;;
    claude)
      command -v claude >/dev/null 2>&1 || fail "claude CLI not installed"
      ANTHROPIC_BASE_URL="${CLIPROXY_BASE_URL%/v1}" ANTHROPIC_AUTH_TOKEN="$CLIPROXY_API_KEY" \
      timeout -k 30 "$limit" claude -p --dangerously-skip-permissions \
        --model "$REPAIR_MODEL" --output-format json \
        < "$prompt" > "$events" 2> "$events.stderr"; rc=$?
      # claude -p json: {"result": "...", "usage": {...}} — keep the result as the last message.
      python3 - "$events" "$last" <<'PY' || true
import json, sys
try:
    data = json.load(open(sys.argv[1]))
    open(sys.argv[2], "w").write(str(data.get("result", "")))
except Exception:
    pass
PY
      ;;
    *) fail "unknown REPAIR_AGENT=$REPAIR_AGENT" ;;
  esac
  return $rc
}

# --- context builders --------------------------------------------------------

stack_position() {
  local n="?" total="?"
  [ -f "$REBASE_DIR/msgnum" ] && n=$(cat "$REBASE_DIR/msgnum")
  [ -f "$REBASE_DIR/end" ] && total=$(cat "$REBASE_DIR/end")
  echo "$n $total"
}

describe_stages() {  # path -> human description of the index stages
  local path="$1" stages
  stages=$(git ls-files -u -- "$path" | awk '{print $3}' | sort -u | tr '\n' ' ')
  case "${stages% }" in
    "1 2 3") echo "modified on both sides" ;;
    "2 3") echo "ADD/ADD — both sides created this file (no common base); usually a superseded patch" ;;
    "1 2") echo "MODIFY/DELETE — the patch deletes it, upstream modified it" ;;
    "1 3") echo "DELETE/MODIFY — upstream deleted it, the patch modifies it" ;;
    *) echo "index stages ${stages% }" ;;
  esac
}

conflict_context() {  # FILES_LIST_FILE -> stdout
  local files="$1" f
  echo "### Patch being applied"
  git log -1 --format='%H%n%s%n%n%b' REBASE_HEAD 2>/dev/null | sed 's/^/    /'
  echo
  echo "### Files the patch touches (all of them, conflicted or not)"
  git show --format= --stat=110 REBASE_HEAD 2>/dev/null | sed 's/^/    /' | head -n 40
  echo
  echo "### Why the mechanical resolver refused"
  sed 's/^/    /' "$STATE_DIR/autoresolve-err.txt" 2>/dev/null | head -n 30
  echo
  if [ -n "$OLD_BASE" ] && [ -n "$NEW_BASE" ]; then
    echo "### Upstream commits since the last sync that touch the conflicted files"
    while IFS= read -r f; do
      [ -n "$f" ] || continue
      echo "    -- $f"
      git log --oneline --no-decorate "$OLD_BASE".."$NEW_BASE" -- "$f" 2>/dev/null | head -n 15 | sed 's/^/       /'
    done < "$files"
    echo
  fi
  echo "### The other patches in the stack (subjects), oldest first"
  if [ -f "$REBASE_DIR/git-rebase-todo.backup" ]; then
    grep -E '^(pick|p) ' "$REBASE_DIR/git-rebase-todo.backup" | sed -E 's/^(pick|p) [0-9a-f]+ /    /' | head -n 80
  fi
  echo
  echo "### Conflict hunks as they stand (diff3 style; long files truncated)"
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    [ -f "$f" ] || { echo "    -- $f: not in the working tree"; continue; }
    if grep -q '^<<<<<<< ' "$f"; then
      echo "    -- $f"
      awk '/^<<<<<<< /{p=1} p{print "    " $0} /^>>>>>>> /{p=0}' "$f" | head -n 250
    fi
  done < "$files"
}

error_files() {  # tsc diagnostics -> unique file paths (tsc prints `path(line,col): error TS…`)
  grep -oE '[A-Za-z0-9_./@-]+\.(ts|tsx|mts|cts)\([0-9]+,[0-9]+\)' "$1" | sed 's/(.*//' | sort -u | head -n 30
}

typecheck_context() {  # LOG -> stdout (files + upstream history)
  local log="$1" f
  echo "### Files with errors and the upstream commits since the last sync that touch them"
  error_files "$log" | while IFS= read -r f; do
    echo "    -- $f"
    if [ -n "$OLD_BASE" ] && [ -n "$NEW_BASE" ]; then
      git log --oneline --no-decorate "$OLD_BASE".."$NEW_BASE" -- "$f" 2>/dev/null | head -n 10 | sed 's/^/       /'
    fi
  done
  echo
  echo "### Fork patches that touch those files"
  if [ -n "$NEW_BASE" ]; then
    error_files "$log" | while IFS= read -r f; do
      git log --oneline --no-decorate "$NEW_BASE"..HEAD -- "$f" 2>/dev/null | head -n 5 | sed "s|^|    $f: |"
    done
  fi
}

# --- one agent call with bookkeeping ----------------------------------------

# call_agent MODE PROMPT_FILE -> sets VERDICT REASON AGENT_FILES CALL_SECONDS USAGE_JSON
call_agent() {
  local mode="$1" prompt="$2" limit left t0 t1 rc
  ITER=$((ITER + 1))
  [ "$ITER" -le "$MAX_ITER" ] || fail "max iterations ($MAX_ITER) reached"
  left=$(remaining)
  [ "$left" -gt 90 ] || fail "wall-clock budget (${BUDGET}s) exhausted"
  limit=$(( left < REPAIR_CALL_TIMEOUT ? left : REPAIR_CALL_TIMEOUT ))
  local last="$STATE_DIR/last-$ITER.txt" events="$STATE_DIR/events-$ITER.jsonl"
  cp "$prompt" "$STATE_DIR/prompt-$ITER.txt"
  {
    echo
    echo "================================================================"
    echo "agent call $ITER ($mode) — $REPAIR_AGENT/$REPAIR_MODEL, limit ${limit}s, $(date -u +%FT%TZ)"
    echo "================================================================"
    sed -n '/^## This stop/,$p' "$prompt" | head -n 120
    echo "----------------------------------------------------------------"
  } >> "$TRANSCRIPT"
  t0=$(date +%s)
  run_agent "$prompt" "$last" "$events" "$limit"; rc=$?
  t1=$(date +%s)
  CALL_SECONDS=$((t1 - t0))
  if [ "$REPAIR_AGENT" = codex ]; then
    python3 "$LIB" transcript "$events" >> "$TRANSCRIPT" 2>/dev/null || true
    USAGE_JSON=$(python3 "$LIB" usage "$events" 2>/dev/null || echo '{}')
  else
    head -c 20000 "$events" >> "$TRANSCRIPT"; echo >> "$TRANSCRIPT"
    USAGE_JSON='{}'
  fi
  if [ -s "$events.stderr" ]; then
    { echo "--- agent stderr (tail) ---"; tail -n 15 "$events.stderr"; } >> "$TRANSCRIPT"
  fi
  mapfile -t parsed < <(python3 "$LIB" verdict "$last")
  VERDICT="${parsed[0]:-NONE}"; REASON="${parsed[1]:-}"; AGENT_FILES="${parsed[2]:-[]}"
  echo "--- verdict: $VERDICT — $REASON (agent rc=$rc, ${CALL_SECONDS}s, usage $USAGE_JSON)" >> "$TRANSCRIPT"
  log "agent call $ITER: rc=$rc verdict=$VERDICT (${CALL_SECONDS}s)"
  if [ "$rc" -eq 124 ] || [ "$rc" -eq 137 ]; then
    [ "$VERDICT" != NONE ] || fail "agent call $ITER timed out after ${limit}s"
  fi
  return 0
}

record() {  # MODE PATCH VERDICT FILES_JSON
  local mode="$1" patch="$2" verdict="$3" files="$4"
  python3 - "$SUMMARY" "$mode" "$patch" "$verdict" "$files" "$REASON" "$CALL_SECONDS" "$USAGE_JSON" "$ITER" <<'PY'
import json, sys
path, mode, patch, verdict, files, reason, seconds, usage, iteration = sys.argv[1:]
try:
    usage_obj = json.loads(usage)
except ValueError:
    usage_obj = {}
try:
    files_obj = json.loads(files)
except ValueError:
    files_obj = [f for f in files.split("\n") if f]
with open(path, "a", encoding="utf-8") as handle:
    handle.write(json.dumps({
        "mode": mode, "patch": patch, "verdict": verdict, "files": files_obj,
        "reason": reason, "seconds": int(seconds), "usage": usage_obj, "iteration": int(iteration),
    }) + "\n")
PY
}

step_continue() {
  if git diff --cached --quiet; then
    GIT_EDITOR=true git rebase --skip >> "$STATE_DIR/rebase.log" 2>&1
  else
    GIT_EDITOR=true git rebase --continue >> "$STATE_DIR/rebase.log" 2>&1
  fi
}

# --- conflict mode -----------------------------------------------------------

repair_conflicts() {
  in_progress || fail "no rebase in progress"
  log "repair lane: conflict mode, budget ${BUDGET}s, max $MAX_ITER agent calls, agent $REPAIR_AGENT/$REPAIR_MODEL"
  local files_file="$STATE_DIR/unmerged.txt" patch before after
  while in_progress; do
    unmerged > "$files_file"
    patch=$(git log -1 --format='%h %s' REBASE_HEAD 2>/dev/null || echo '?')
    if [ ! -s "$files_file" ]; then
      # Stopped with nothing unmerged (e.g. a previous continue left staged
      # work): just move on; a stop that does not advance is caught below.
      before=$(git rev-parse REBASE_HEAD 2>/dev/null || echo none)
      step_continue
      after=$(git rev-parse REBASE_HEAD 2>/dev/null || echo done)
      [ "$before" != "$after" ] || fail "rebase did not advance past $patch with nothing unmerged"
      continue
    fi
    # Mechanical first; the agent only sees what the rules refuse.
    if python3 "$AUTORESOLVE" --summary "$STATE_DIR/autoresolved.jsonl" 2> "$STATE_DIR/autoresolve-err.txt"; then
      log "mechanical: $(tr '\n' ' ' < "$files_file") ($patch)"
      step_continue
      continue
    fi
    log "REAL stop at $patch: $(tr '\n' ' ' < "$files_file")"
    read -r idx total <<< "$(stack_position)"
    local ctx="$STATE_DIR/context.txt" files_desc="$STATE_DIR/files-desc.txt" prompt="$STATE_DIR/prompt.txt" f
    : > "$files_desc"
    while IFS= read -r f; do
      [ -n "$f" ] && printf -- '- `%s` — %s\n' "$f" "$(describe_stages "$f")" >> "$files_desc"
    done < "$files_file"
    conflict_context "$files_file" > "$ctx"
    python3 "$LIB" prompt --template "$TEMPLATE" --mode conflict \
      --var "PATCH=$patch" --var "PATCH_INDEX=$idx" --var "PATCH_COUNT=$total" \
      --var "OLD_BASE=${OLD_BASE:-<unknown>}" --var "NEW_BASE=${NEW_BASE:-<unknown>}" \
      --var "FILES=@$files_desc" --context "$ctx" > "$prompt" || fail "prompt assembly failed"
    before=$(git rev-parse REBASE_HEAD)
    call_agent conflict "$prompt"
    in_progress || fail "agent broke the rebase state (rebase no longer in progress) at $patch"
    [ "$(git rev-parse REBASE_HEAD)" = "$before" ] || fail "agent advanced the rebase itself at $patch"
    case "$VERDICT" in
      SKIP)
        log "dropping $patch: $REASON"
        # `git rebase --skip` resets index and working tree itself; partial edits vanish with it.
        record conflict "$patch" SKIP "$(python3 -c 'import json,sys;print(json.dumps([l.strip() for l in open(sys.argv[1]) if l.strip()]))' "$files_file")"
        GIT_EDITOR=true git rebase --skip >> "$STATE_DIR/rebase.log" 2>&1
        ;;
      RESOLVED|NONE)
        if leftovers=$(python3 "$LIB" markers < "$files_file"); [ -n "$leftovers" ]; then
          echo "$leftovers" >> "$TRANSCRIPT"
          fail "conflict markers remain after the agent (verdict $VERDICT) at $patch: $(echo "$leftovers" | tr '\n' ' ')"
        fi
        [ "$VERDICT" = RESOLVED ] || log "no verdict from the agent; markers are gone, treating as RESOLVED"
        git add -A -- . ':!**/__pycache__/**' >> "$STATE_DIR/rebase.log" 2>&1
        [ -z "$(unmerged)" ] || fail "files still unmerged after staging at $patch: $(unmerged | tr '\n' ' ')"
        { echo "--- staged after repair:"; git diff --cached --stat=110 | tail -n 25; } >> "$TRANSCRIPT"
        # Receipt lists what the agent touched: the files that stopped the rebase
        # plus whatever else it reports, not the whole patch's file list.
        record conflict "$patch" RESOLVED "$(python3 - "$files_file" "$AGENT_FILES" <<'PY'
import json, sys
files = [l.strip() for l in open(sys.argv[1]) if l.strip()]
try:
    extra = json.loads(sys.argv[2])
except ValueError:
    extra = []
files += [str(f) for f in extra if isinstance(f, str) and str(f) not in files]
print(json.dumps(files))
PY
)"
        step_continue
        ;;
      GIVEUP)
        record conflict "$patch" GIVEUP "[]"
        fail "agent gave up at $patch: $REASON"
        ;;
      *) fail "unexpected verdict $VERDICT at $patch" ;;
    esac
    after=$(git rev-parse REBASE_HEAD 2>/dev/null || echo done)
    if in_progress && [ "$after" = "$before" ]; then
      tail -n 10 "$STATE_DIR/rebase.log" >> "$TRANSCRIPT"
      fail "rebase did not advance past $patch after the agent's resolution"
    fi
  done
  log "rebase completed: HEAD $(git rev-parse --short HEAD), $ITER agent call(s), $(( $(date +%s) - START ))s"
  return 0
}

# --- typecheck mode ----------------------------------------------------------

repair_typecheck() {
  in_progress && fail "typecheck mode needs a completed rebase"
  [ -s "$TYPECHECK_LOG" ] || fail "typecheck log $TYPECHECK_LOG is missing or empty"
  local errors="$STATE_DIR/errors.txt" ctx="$STATE_DIR/context.txt" prompt="$STATE_DIR/prompt.txt" log="$TYPECHECK_LOG" head0
  log "repair lane: typecheck mode, budget ${BUDGET}s, max $MAX_ITER agent calls, verify: $VERIFY_CMD"
  head0=$(git rev-parse HEAD)
  while :; do
    grep -E 'error TS[0-9]+' "$log" | grep -v 'TS377' | sort -u | head -n 60 > "$errors"
    [ -s "$errors" ] || grep -iE 'error' "$log" | head -n 60 > "$errors"
    typecheck_context "$errors" > "$ctx"
    python3 "$LIB" prompt --template "$TEMPLATE" --mode typecheck \
      --var "OLD_BASE=${OLD_BASE:-<unknown>}" --var "NEW_BASE=${NEW_BASE:-<unknown>}" \
      --var "ERRORS=@$errors" --context "$ctx" > "$prompt" || fail "prompt assembly failed"
    call_agent typecheck "$prompt"
    [ "$(git rev-parse HEAD)" = "$head0" ] || fail "agent moved HEAD during typecheck repair"
    case "$VERDICT" in
      GIVEUP) record typecheck "typecheck" GIVEUP "[]"; fail "agent gave up on the typecheck repair: $REASON" ;;
      SKIP) fail "SKIP is not a valid verdict in typecheck mode" ;;
    esac
    if leftovers=$(git diff --name-only | python3 "$LIB" markers); [ -n "$leftovers" ]; then
      fail "conflict markers in the typecheck repair: $(echo "$leftovers" | tr '\n' ' ')"
    fi
    [ -n "$(git status --porcelain)" ] || fail "agent changed nothing (verdict $VERDICT)"
    log "verifying: $VERIFY_CMD"
    log="$STATE_DIR/verify-$ITER.log"
    if bash -c "$VERIFY_CMD" > "$log" 2>&1; then
      local files subject
      files=$(git status --porcelain | awk '{print $2}' | python3 -c 'import json,sys;print(json.dumps([l.strip() for l in sys.stdin if l.strip()]))')
      subject=$(printf '%s' "$REASON" | tr '\n' ' ' | cut -c1-72)
      git add -A -- . ':!**/__pycache__/**'
      git commit -q -m "oklabs: repair: adapt fork-only code to upstream${subject:+ — $subject}" \
        -m "Automated typecheck repair after the nightly rebase (rebase-repair.sh, $REPAIR_AGENT/$REPAIR_MODEL)." \
        -m "$(sed 's/^/  /' "$errors" | head -n 30)" \
        || fail "commit of the typecheck repair failed"
      record typecheck "typecheck" RESOLVED "$files"
      log "typecheck green after repair; committed $(git rev-parse --short HEAD)"
      return 0
    fi
    log "verify still red after call $ITER: $(grep -cE 'error TS[0-9]+' "$log" || true) error line(s)"
    { echo "--- verify output (tail):"; tail -n 20 "$log"; } >> "$TRANSCRIPT"
    [ "$ITER" -lt "$MAX_ITER" ] || { record typecheck "typecheck" FAILED "[]"; fail "typecheck still red after $ITER agent call(s)"; }
  done
}

case "$MODE" in
  conflict) repair_conflicts ;;
  typecheck) repair_typecheck ;;
esac
