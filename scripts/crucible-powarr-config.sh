#!/usr/bin/env bash
# Configure Powarr Ollama integration after crucible benchmark.
# Run from a host that can reach both Powarr and crucible (e.g. docker host).
#
# Usage:
#   ./scripts/crucible-powarr-config.sh <crucible-ip> <model> [powarr-url]
#
# Example:
#   ./scripts/crucible-powarr-config.sh 10.1.1.15 qwen2.5:3b http://10.1.1.2:7979
set -euo pipefail

CRUCIBLE_IP="${1:?Usage: $0 <crucible-ip> <model> [powarr-url]}"
MODEL="${2:?Usage: $0 <crucible-ip> <model> [powarr-url]}"
POWARR_URL="${3:-http://10.1.1.2:7979}"
OLLAMA_HOST="http://${CRUCIBLE_IP}:11434"

# Profile by model size
if echo "$MODEL" | grep -qE '1b|:1b'; then
  MODEL_SIZE="small"; VERBOSITY="minimal"; REPLY_FORMAT="simple"; CONFIDENCE_STYLE="classified"
elif echo "$MODEL" | grep -qE '7b|:7b|mistral'; then
  MODEL_SIZE="medium"; VERBOSITY="brief"; REPLY_FORMAT="json"; CONFIDENCE_STYLE="numeric"
else
  MODEL_SIZE="small"; VERBOSITY="minimal"; REPLY_FORMAT="simple"; CONFIDENCE_STYLE="classified"
fi

echo "Configuring Powarr Ollama integration..."
echo "  Host: $OLLAMA_HOST"
echo "  Model: $MODEL"
echo "  Profile: size=$MODEL_SIZE verbosity=$VERBOSITY format=$REPLY_FORMAT confidence=$CONFIDENCE_STYLE"

# Fetch current settings to preserve custom prompts
CURRENT=$(curl -sf "${POWARR_URL}/api/v1/settings/ollama" 2>/dev/null || echo '{}')

PAYLOAD=$(python3 - <<PY
import json
current = json.loads('''$CURRENT''' or '{}')
payload = {
    "enabled": True,
    "host": "$OLLAMA_HOST",
    "model": "$MODEL",
    "api_style": "ollama",
    "verbosity": "$VERBOSITY",
    "model_size": "$MODEL_SIZE",
    "keep_alive_minutes": 10,
    "reply_format": "$REPLY_FORMAT",
    "confidence_style": "$CONFIDENCE_STYLE",
    "batch_delay_ms": 1500,
    "match_prompt": current.get("match_prompt", ""),
    "explain_prompt": current.get("explain_prompt", ""),
    "pack_prompt": current.get("pack_prompt", ""),
}
print(json.dumps(payload))
PY
)

curl -sf -X PUT "${POWARR_URL}/api/v1/settings/ollama" \
  -H 'Content-Type: application/json' \
  -d "$PAYLOAD" | python3 -m json.tool

echo ""
echo "Running Powarr benchmark (canned match prompt)..."
BENCH=$(curl -sf -X POST "${POWARR_URL}/api/v1/settings/ollama/preview" \
  -H 'Content-Type: application/json' \
  -d '{"task":"match","use_real_data":false}')
echo "$BENCH" | python3 -m json.tool

LATENCY=$(echo "$BENCH" | python3 -c "import sys,json; print(json.load(sys.stdin).get('latency_ms',0))")
VALID=$(echo "$BENCH" | python3 -c "import sys,json; print(json.load(sys.stdin).get('json_valid',False))")
echo ""
if [ "$VALID" = "True" ] || [ "$VALID" = "true" ]; then
  echo "Benchmark PASSED: ${LATENCY}ms, verdict parsed"
else
  echo "Benchmark WARNING: verdict did not parse (${LATENCY}ms) — try Simple/Minimal profile"
fi

echo ""
echo "Optional: test with real failed import data:"
echo "  curl -s -X POST ${POWARR_URL}/api/v1/settings/ollama/preview -H 'Content-Type: application/json' -d '{\"task\":\"match\",\"use_real_data\":true}'"
