#!/usr/bin/env bash
# Run the full pipeline end to end: SEC download through the dashboard.
#   SEC_USER_AGENT="Jane Doe jane@example.com" scripts/run_all.sh [first-year]
# Optional: OPENFIGI_API_KEY speeds up ticker mapping, WARD_DATA moves the data folder.
set -euo pipefail

since="${1:-2019}"

if [[ "${SEC_USER_AGENT:-}" != *@* ]]; then
  echo "Set SEC_USER_AGENT to a name and contact email first, e.g." >&2
  echo '  export SEC_USER_AGENT="Jane Doe jane@example.com"' >&2
  exit 1
fi
command -v ward >/dev/null || { echo "ward not found. Run: pip install -e ." >&2; exit 1; }

step() { printf '\n== %s ==\n' "$*"; local t=$SECONDS; "$@"; printf '   done in %ss\n' $((SECONDS - t)); }

step ward download --since "$since"
step ward load
step ward build
step ward market --since "$since"
step ward report
step ward stress
step ward stress --public
step ward dashboard

echo
echo "Finished. Outputs are in ${WARD_DATA:-data}/out. Start the live view with: ward live"
