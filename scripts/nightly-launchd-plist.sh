#!/usr/bin/env bash
# Print a launchd job (macOS) that runs scripts/nightly-auto.sh once a day.
# Nothing is installed: redirect the output into ~/Library/LaunchAgents and load it
# yourself (see "Nightly job" in the README).
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage: scripts/nightly-launchd-plist.sh [--hour H] [--minute M] [--label NAME]
                                        [-- extra `nightly-auto.sh` options]

Prints a property list on stdout. launchd starts jobs with a bare PATH, so the directories
of `uv`, `claude` and `zstd` as found now are written into the job.

Environment variables of scripts/nightly-auto.sh that are set now
(SESSION2MEMORY_AUTO_*, OPENCLAW_STATE_DIR) are copied into the job.
USAGE
}

hour=5
minute=30
label="com.session2memory.nightly"
extra=()
while [ "$#" -gt 0 ]; do
  case "$1" in
    --hour) hour="$2"; shift 2 ;;
    --minute) minute="$2"; shift 2 ;;
    --label) label="$2"; shift 2 ;;
    --) shift; extra=("$@"); break ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done
case "$hour$minute" in
  *[!0-9]*|"") echo "--hour and --minute take numbers" >&2; exit 2 ;;
esac

script_dir="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(CDPATH= cd -- "$script_dir/.." && pwd)"
store="${SESSION2MEMORY_AUTO_STORE:-$HOME/.local/share/session2memory/store}"

# The directories of the programs a run needs, then the system defaults.
path=""
for program in uv claude zstd; do
  if found="$(command -v "$program" 2>/dev/null)"; then
    dir="$(dirname "$found")"
    case ":$path:" in *":$dir:"*) ;; *) path="${path:+$path:}$dir" ;; esac
  else
    echo "warning: $program is not on PATH; the job will not find it either" >&2
  fi
done
path="${path:+$path:}/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

xml() { printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'; }
pair() { printf '    <key>%s</key>\n    <string>%s</string>\n' "$(xml "$1")" "$(xml "$2")"; }

cat <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$(xml "$label")</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$(xml "$script_dir/nightly-auto.sh")</string>
PLIST
for one in ${extra[@]+"${extra[@]}"}; do
  printf '    <string>%s</string>\n' "$(xml "$one")"
done
cat <<PLIST
  </array>
  <key>WorkingDirectory</key>
  <string>$(xml "$repo_root")</string>
  <key>EnvironmentVariables</key>
  <dict>
PLIST
pair HOME "$HOME"
pair PATH "$path"
for name in SESSION2MEMORY_AUTO_STORE SESSION2MEMORY_AUTO_MAX_LLM_CALLS \
  SESSION2MEMORY_AUTO_CURATE_SCOPE SESSION2MEMORY_AUTO_SEMANTIC \
  SESSION2MEMORY_AUTO_INDEX_CMD SESSION2MEMORY_AUTO_LLM_CMD OPENCLAW_STATE_DIR; do
  if [ -n "${!name:-}" ]; then
    pair "$name" "${!name}"
  fi
done
cat <<PLIST
  </dict>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>
    <integer>$((10#$hour))</integer>
    <key>Minute</key>
    <integer>$((10#$minute))</integer>
  </dict>
  <key>StandardOutPath</key>
  <string>$(xml "$store/logs/launchd.out.log")</string>
  <key>StandardErrorPath</key>
  <string>$(xml "$store/logs/launchd.err.log")</string>
</dict>
</plist>
PLIST
