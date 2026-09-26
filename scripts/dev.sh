#!/usr/bin/env bash
# 개발 실행: 백엔드(데모 DB, 고정 포트) + 화면(live 모드). 브라우저에서 http://127.0.0.1:5173
# 사용: scripts/dev.sh [--live-db]   (--live-db: 데모 대신 실제 개발 DB)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TOKEN="dev-$(date +%s)"
DEMO="--demo"
[[ "${1:-}" == "--live-db" ]] && DEMO=""
cd "$ROOT/backend"
WORKLEAD_DEV_TOKEN="$TOKEN" uv run python -m worklead --dev $DEMO --port 8765 --data-dir "$ROOT/backend/.devdata" &
BACK=$!
trap 'kill $BACK 2>/dev/null' EXIT
cd "$ROOT/desktop"
VITE_DATA_MODE=live VITE_WORKLEAD_API_URL=http://127.0.0.1:8765 VITE_WORKLEAD_TOKEN="$TOKEN" npx vite --port 5173 --host 127.0.0.1
