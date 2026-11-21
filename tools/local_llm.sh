#!/bin/sh
# Free local LLM for the ranker: Ollama confined by a macOS sandbox (config/ollama-sandbox.sb).
#   tools/local_llm.sh setup     download the model (network allowed, files confined)
#   tools/local_llm.sh start     serve on 127.0.0.1:11434 with no outside network and no access to your files
#   tools/local_llm.sh stop | status | verify
#   tools/local_llm.sh env       print the variables that point the ranker at it
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROFILE="$ROOT/config/ollama-sandbox.sb"
HOME_DIR="${AI_JOB_SEARCH_LLM_HOME:-$HOME/.local/share/ai-job-search-llm}"
MODEL="${AI_JOB_SEARCH_LOCAL_MODEL:-qwen2.5:7b-instruct}"
OLLAMA="$(command -v ollama || true)"
A1="$(dirname "$HOME_DIR")"; A2="$(dirname "$A1")"; A3="$(dirname "$A2")"
PIDFILE="$HOME_DIR/server.pid"
LOGFILE="$HOME_DIR/server.log"

[ -n "$OLLAMA" ] || { echo "ollama is not installed: brew install ollama" >&2; exit 1; }
mkdir -p "$HOME_DIR"

confined() {
  mode="$1"; shift
  (
    cd "$HOME_DIR"
    exec env -i PATH="/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin" HOME="$HOME_DIR" TMPDIR="$HOME_DIR/tmp" \
      OLLAMA_HOST=127.0.0.1:11434 OLLAMA_MODELS="$HOME_DIR/models" OLLAMA_KEEP_ALIVE=30m \
      sandbox-exec -D "HOME_DIR=$HOME_DIR" -D "NETWORK=$mode" -D "A1=$A1" -D "A2=$A2" -D "A3=$A3" -f "$PROFILE" "$@"
  )
}

running() { [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; }

case "${1:-}" in
  setup)
    mkdir -p "$HOME_DIR/tmp" "$HOME_DIR/models"
    if running; then echo "stop the server first: tools/local_llm.sh stop" >&2; exit 1; fi
    confined pull "$OLLAMA" serve >"$HOME_DIR/pull-server.log" 2>&1 &
    pid=$!
    trap 'kill $pid 2>/dev/null || true' EXIT
    for _ in 1 2 3 4 5 6 7 8 9 10; do curl -sf http://127.0.0.1:11434/api/version >/dev/null && break; sleep 1; done
    OLLAMA_HOST=127.0.0.1:11434 HOME="$HOME_DIR" "$OLLAMA" pull "$MODEL"
    echo "Model $MODEL stored in $HOME_DIR/models"
    ;;
  start)
    mkdir -p "$HOME_DIR/tmp" "$HOME_DIR/models"
    running && { echo "already running (pid $(cat "$PIDFILE"))"; exit 0; }
    confined serve "$OLLAMA" serve >"$LOGFILE" 2>&1 &
    echo $! >"$PIDFILE"
    for _ in 1 2 3 4 5 6 7 8 9 10; do curl -sf http://127.0.0.1:11434/api/version >/dev/null && break; sleep 1; done
    curl -sf http://127.0.0.1:11434/api/version >/dev/null && echo "Ollama running, sandboxed (pid $(cat "$PIDFILE"))" \
      || { echo "server did not start, see $LOGFILE" >&2; exit 1; }
    ;;
  stop)
    if running; then pkill -P "$(cat "$PIDFILE")" 2>/dev/null || true; kill "$(cat "$PIDFILE")" 2>/dev/null || true; rm -f "$PIDFILE"; echo stopped; else echo "not running"; fi
    ;;
  status)
    if running; then echo "running (pid $(cat "$PIDFILE")), model dir $HOME_DIR/models"; curl -s http://127.0.0.1:11434/api/tags | head -c 400; echo; else echo "stopped"; fi
    ;;
  verify)
    canary="$ROOT/.sandbox-canary"; echo secret > "$canary"; trap 'rm -f "$canary" "$ROOT/.sandbox-write-test"' EXIT
    mkdir -p "$HOME_DIR/tmp"
    cat "$canary" >/dev/null 2>&1 || { echo "control failed: cannot read the canary without the sandbox" >&2; exit 1; }
    echo "Checking the sandbox from the inside (each line must say BLOCKED):"
    probe() {
      if confined serve /bin/sh -c "$2" >/dev/null 2>&1; then echo "  LEAK     $1"; else echo "  BLOCKED  $1"; fi
    }
    probe "read a file in this repository"   "cat '$canary'"
    probe "list your home directory"         "ls '$HOME'"
    probe "read ~/.ssh"                      "ls '$HOME/.ssh'"
    probe "write into this repository"       "touch '$ROOT/.sandbox-write-test'"
    probe "open an outside connection"       "curl -s -m 5 -o /dev/null https://example.com"
    if confined serve /bin/sh -c "touch '$HOME_DIR/tmp/ok' && cat '$HOME_DIR/tmp/ok'" >/dev/null 2>&1; then echo "  ALLOWED  its own model home (expected)"; else echo "  PROBLEM  model home is not writable"; fi
    ;;
  env)
    echo "export OPENAI_BASE_URL=http://127.0.0.1:11434/v1"
    echo "export OPENAI_API_KEY=ollama"
    echo "export AI_JOB_SEARCH_MODEL=$MODEL"
    ;;
  *)
    echo "usage: $0 {setup|start|stop|status|verify|env}" >&2; exit 2 ;;
esac
