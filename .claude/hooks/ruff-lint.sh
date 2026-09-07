#!/usr/bin/env bash
# PostToolUse hook (Edit|Write): format and lint the Python file that was just written.
# Auto-fixable issues are fixed silently; anything left is printed to stderr with
# exit 2, which feeds the errors back to Claude so it corrects them itself.
set -uo pipefail

# Hook input arrives as JSON on stdin.
file=$(jq -r '.tool_response.filePath // .tool_input.file_path // empty')
[[ "$file" == *.py ]] || exit 0
[[ -f "$file" ]] || exit 0

# Prefer the project venv so the hook uses the pinned ruff, not a stray global one.
root="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
ruff="$root/.venv/bin/ruff"
if [[ ! -x "$ruff" ]]; then
  ruff=$(command -v ruff) || exit 0
fi

"$ruff" format -q "$file" >/dev/null 2>&1
"$ruff" check --fix -q "$file" >/dev/null 2>&1

# Whatever survives --fix needs a human/model decision.
if ! remaining=$("$ruff" check "$file" 2>&1); then
  echo "ruff still reports issues in $file:" >&2
  echo "$remaining" >&2
  exit 2
fi
exit 0
