#!/bin/bash
# Double-click this file to start Job Agent. Your browser opens automatically.
# Keep the terminal window it opens in the background; close it to stop Job Agent.
set -e
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
  echo "First-time setup — this takes about a minute..."
  python3 -m venv .venv
  .venv/bin/pip install --quiet -r requirements.txt
fi

# Pick up newly added dependencies after an update.
.venv/bin/python -c "import flask, groq, requests" 2>/dev/null || .venv/bin/pip install --quiet -r requirements.txt

exec .venv/bin/python -m job_agent web --open
