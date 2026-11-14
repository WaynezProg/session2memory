#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: scripts/daily-session-memory-to-hks.sh [--date YYYY-MM-DD] [--dry-run]

Defaults:
  date: yesterday, for daily "整理前一天 session" runs
  session2memory output root: ./out/session-memory
  HKS repo: ../Hybrid-Knowledge-System, else ../hks
  KS_ROOT: ./out/HKS
  HKS_EMBEDDING_MODEL: simple
  SESSION2MEMORY_TOOLS: claude claude-desktop codex cursor cursor-cli opencode qwen

Environment overrides:
  SESSION2MEMORY_OUTPUT_ROOT
  SESSION2MEMORY_TOOLS
  HKS_REPO
  KS_ROOT
  HKS_EMBEDDING_MODEL
USAGE
}

default_yesterday() {
  if date -v-1d +%F >/dev/null 2>&1; then
    date -v-1d +%F
    return
  fi
  if date -d yesterday +%F >/dev/null 2>&1; then
    date -d yesterday +%F
    return
  fi
  echo "Cannot compute yesterday; pass --date YYYY-MM-DD" >&2
  return 1
}

date_arg=""
dry_run=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --date)
      if [ "$#" -lt 2 ]; then
        echo "--date requires YYYY-MM-DD" >&2
        exit 2
      fi
      date_arg="$2"
      shift 2
      ;;
    --dry-run)
      dry_run=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [ -n "$date_arg" ]; then
  target_date="$date_arg"
else
  target_date="$(default_yesterday)"
fi

case "$target_date" in
  ????-??-??) ;;
  *)
    echo "--date must use YYYY-MM-DD" >&2
    exit 2
    ;;
esac

script_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(CDPATH= cd -- "$script_dir/.." && pwd)"
output_root="${SESSION2MEMORY_OUTPUT_ROOT:-"$repo_root/out/session-memory"}"
dated_output="$output_root/$target_date"
if [ -n "${HKS_REPO:-}" ]; then
  hks_repo="$HKS_REPO"
elif [ -d "$repo_root/../Hybrid-Knowledge-System" ]; then
  hks_repo="$repo_root/../Hybrid-Knowledge-System"
else
  hks_repo="$repo_root/../hks"
fi
# A knowledge base of its own: this script embeds with HKS_EMBEDDING_MODEL (default
# `simple`), which must not be mixed into a KS_ROOT built with another model.
ks_root="${KS_ROOT:-"$repo_root/out/HKS"}"
hks_embedding_model="${HKS_EMBEDDING_MODEL:-simple}"
session2memory_tools="${SESSION2MEMORY_TOOLS:-claude claude-desktop codex cursor cursor-cli opencode qwen}"

if [ ! -d "$hks_repo" ]; then
  echo "HKS_REPO does not exist: $hks_repo" >&2
  exit 1
fi

mkdir -p "$dated_output"

echo "date=$target_date"
echo "session2memory_output=$dated_output"
echo "hks_source_root=$output_root"
echo "hks_repo=$hks_repo"
echo "KS_ROOT=$ks_root"
echo "session2memory_tools=$session2memory_tools"

tool_args=()
for tool in $session2memory_tools; do
  tool_args+=(--tool "$tool")
done

(
  cd "$repo_root"
  uv run session2memory import --date "$target_date" --output "$dated_output" "${tool_args[@]}" --dry-run
)

if [ "$dry_run" -eq 1 ]; then
  (
    cd "$hks_repo"
    KS_ROOT="$ks_root" HKS_EMBEDDING_MODEL="$hks_embedding_model" \
      uv run ks update "$output_root" --dry-run
  )
  exit 0
fi

(
  cd "$repo_root"
  uv run session2memory import --date "$target_date" --output "$dated_output" "${tool_args[@]}"
)

(
  cd "$hks_repo"
  KS_ROOT="$ks_root" HKS_EMBEDDING_MODEL="$hks_embedding_model" \
    uv run ks update "$output_root"
)

(
  cd "$hks_repo"
  KS_ROOT="$ks_root" HKS_EMBEDDING_MODEL="$hks_embedding_model" \
    uv run ks source list --relpath-query "$target_date"
)
