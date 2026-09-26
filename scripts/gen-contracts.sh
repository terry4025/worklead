#!/usr/bin/env bash
# 계약 재생성: OpenAPI·JSON Schema·fixtures(백엔드) → TypeScript 타입(openapi-typescript)
# 사용: scripts/gen-contracts.sh   (backend: uv, desktop: node 필요)
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/backend"
uv run python scripts/export_contracts.py
cd "$ROOT/desktop"
npx --yes openapi-typescript@7.13.0 ../contracts/openapi.json -o ../contracts/client/schema.d.ts
cd "$ROOT/backend"
uv run pytest tests/test_contracts.py -q
echo "contracts regenerated"
