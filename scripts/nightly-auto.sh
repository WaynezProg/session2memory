#!/usr/bin/env bash
# Unattended maintenance: scan, compress/archive, curate memory, export.
# Meant for a nightly launchd/cron job; safe to run by hand. Each run writes its JSON
# report under <store>/logs and keeps the 60 most recent.
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: scripts/nightly-auto.sh [--dry-run] [extra `session2memory auto run` options]

Environment overrides:
  SESSION2MEMORY_AUTO_STORE           persistent store (default ~/.local/share/session2memory/store)
  SESSION2MEMORY_AUTO_MAX_LLM_CALLS   curation call budget per run (default 40)
  SESSION2MEMORY_AUTO_CURATE_SCOPE    scopes to curate, space separated (default: coding);
                                      "all" curates OpenClaw agents' sessions too
  SESSION2MEMORY_AUTO_INDEX_CMD       command run after export; {memory_dir} is substituted
  SESSION2MEMORY_AUTO_LLM_CMD         curation command (prompt on stdin)
  OPENCLAW_STATE_DIR                  OpenClaw state directory, for its agents' sessions
USAGE
}

case "${1:-}" in
  -h|--help) usage; exit 0 ;;
esac

script_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(CDPATH= cd -- "$script_dir/.." && pwd)"
store="${SESSION2MEMORY_AUTO_STORE:-$HOME/.local/share/session2memory/store}"
max_calls="${SESSION2MEMORY_AUTO_MAX_LLM_CALLS:-40}"
log_dir="$store/logs"
mkdir -p "$log_dir"
report="$log_dir/run-$(date +%Y%m%d-%H%M%S).json"

args=(auto run --store "$store" --max-llm-calls "$max_calls" --json)
# OpenClaw agents produce hundreds of machine-triggered sessions a day and keep their own
# memory; by default only sessions of coding tools are distilled.
curate_scope="${SESSION2MEMORY_AUTO_CURATE_SCOPE:-coding}"
if [ "$curate_scope" != "all" ]; then
  for one in $curate_scope; do
    args+=(--curate-scope "$one")
  done
fi
if [ -n "${SESSION2MEMORY_AUTO_INDEX_CMD:-}" ]; then
  args+=(--index-cmd "$SESSION2MEMORY_AUTO_INDEX_CMD")
fi

cd "$repo_root"
status=0
uv run session2memory "${args[@]}" "$@" > "$report" || status=$?
echo "report=$report exit=$status"

# Keep the most recent reports only.
ls -1t "$log_dir"/run-*.json 2>/dev/null | tail -n +61 | while IFS= read -r old; do
  rm -f -- "$old"
done
exit "$status"
