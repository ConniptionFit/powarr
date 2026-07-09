#!/usr/bin/env bash
# Powarr-aligned Ollama model benchmark for CPU-only hosts (e.g. crucible).
#
# Run on the Ollama machine:
#   ./scripts/crucible-ollama-benchmark.sh
#
# Run remotely from a LAN host:
#   ssh powers@crucible 'bash -s' < scripts/crucible-ollama-benchmark.sh
#
# Skip re-pulling models (already on host):
#   SKIP_PULL=1 ./scripts/crucible-ollama-benchmark.sh
set -euo pipefail

RESULTS_DIR="${RESULTS_DIR:-/tmp/powarr-ollama-benchmark}"
mkdir -p "$RESULTS_DIR"
REPORT="$RESULTS_DIR/report-$(date +%Y%m%d-%H%M%S).txt"

log() { echo "$@" | tee -a "$REPORT"; }

# --- Phase 1: Hardware inventory ---
log "=== Powarr Ollama Benchmark Report ==="
log "Date: $(date -Is)"
log "Host: $(hostname)"
log ""
log "=== CPU ==="
lscpu | grep -E "Model name|Socket|Core|Thread|MHz|Architecture" | tee -a "$REPORT"
log ""
log "=== RAM ==="
free -h | tee -a "$REPORT"
RAM_KB=$(awk '/MemTotal/ {print $2}' /proc/meminfo)
RAM_GB=$((RAM_KB / 1024 / 1024))
log "Total RAM: ~${RAM_GB} GB"
log ""
log "=== SWAP ==="
swapon --show 2>/dev/null | tee -a "$REPORT" || log "(no swap)"
log ""
log "=== DISK ==="
df -h / /var/lib/ollama 2>/dev/null | tee -a "$REPORT" || df -h / | tee -a "$REPORT"
log ""
log "=== OLLAMA ==="
command -v ollama >/dev/null && ollama --version | tee -a "$REPORT" || log "ollama not in PATH"
ollama list 2>/dev/null | tee -a "$REPORT" || true

# Candidate tier from RAM
if [ "$RAM_GB" -lt 6 ]; then
  CANDIDATES=("llama3.2:1b" "qwen2.5:3b")
  TIER="B (<6GB)"
elif [ "$RAM_GB" -lt 14 ]; then
  CANDIDATES=("qwen2.5:3b" "llama3.2:3b")
  TIER="A (6-13GB)"
else
  CANDIDATES=("qwen2.5:3b" "llama3.2:3b" "qwen2.5:7b")
  TIER="A+C (14GB+)"
fi
log ""
log "=== Candidate tier: $TIER ==="
log "Models to test: ${CANDIDATES[*]}"

# Powarr match prompt (simple/classified — small model profile)
MATCH_PROMPT='You match download release names to media library entries.
Release name: Anime.Title.S01E03.1080p.WEB
Candidate library entry: Anime Title - S01E03 - Episode Title
Context: Source app: sonarr. Queue error: no files eligible for import
Deterministic scorer result (computed from title/season/episode/absolute-number comparisons): episode title similarity 0.72; season+episode numbers match (heuristic confidence 0.81)
Judging guidance: ignore file-quality/format/codec/audio details and uploader or release-group identifiers (resolution, source, codec, audio channels, HDR, encoder, release-group tag) — these describe the file, not the show or movie, and are never evidence for or against a match. If the release name is in a different language or script than the candidate'"'"'s title (common for anime/international content), consider whether it could be a known translation, transliteration, or alternate title for the same work rather than judging on string similarity alone.
Do you agree with this match, and does the evidence make you more, less, or same confident than the scorer? Reply with ONLY one line, exactly this form:
agree or disagree | more or less or same | short reason'

ANIME_PROMPT='You match download release names to media library entries.
Release name: Shingeki.no.Kyojin.S04E01.1080p.WEB
Candidate library entry: Attack on Titan - S04E01 - The Other Side of the Sea
Context: Source app: sonarr. Queue error: no files eligible for import
Deterministic scorer result (computed from title/season/episode/absolute-number comparisons): episode title similarity 0.45; season+episode numbers match (heuristic confidence 0.75)
Judging guidance: ignore file-quality/format/codec/audio details and uploader or release-group identifiers (resolution, source, codec, audio channels, HDR, encoder, release-group tag) — these describe the file, not the show or movie, and are never evidence for or against a match. If the release name is in a different language or script than the candidate'"'"'s title (common for anime/international content), consider whether it could be a known translation, transliteration, or alternate title for the same work rather than judging on string similarity alone.
Do you agree with this match, and does the evidence make you more, less, or same confident than the scorer? Reply with ONLY one line, exactly this form:
agree or disagree | more or less or same | short reason'

PACK_PROMPT='You match individual files within a season/series pack to episodes in a media library.
Pack: Example.Show.S01.Complete.1080p
Library entry: Example Show
Files in download: Example.Show.S01E01.mkv, Example.Show.S01E02.mkv, Example.Show.S01E03.mkv
Context: Source app: sonarr. Triggered series: Example Show
Reply with ONLY a JSON array of objects, each with keys: file, season, episode, match_type. match_type must be one of: Exact Match, Title Match, Number Match, Absolute Number Match, Multi-Episode File, Sequence Match, Low Confidence.'

score_parse() {
  local out="$1"
  if echo "$out" | grep -qiE '^(agree|disagree)\s*\|\s*(more|less|same)'; then
    echo "PASS"
  elif echo "$out" | grep -qiE '^(agree|disagree)\b'; then
    echo "PARTIAL"
  else
    echo "FAIL"
  fi
}

score_pack() {
  local out="$1"
  if echo "$out" | python3 -c "import sys,json; d=json.load(sys.stdin); sys.exit(0 if isinstance(d,list) and len(d)>=2 else 1)" 2>/dev/null; then
    echo "PASS"
  elif echo "$out" | python3 -c "import sys,json; d=json.load(sys.stdin); sys.exit(0 if isinstance(d,(list,dict)) else 1)" 2>/dev/null; then
    echo "PARTIAL"
  else
    echo "FAIL"
  fi
}

# Powarr uses POST /api/generate with temperature 0 and num_predict caps.
ollama_generate() {
  local model="$1" prompt="$2" json_fmt="${3:-false}" num_predict="${4:-96}"
  python3 - "$model" "$prompt" "$json_fmt" "$num_predict" <<'PY'
import json, sys, urllib.request, time
model, prompt, json_fmt, num_predict = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
body = {
    "model": model,
    "prompt": prompt,
    "stream": False,
    "keep_alive": "10m",
    "options": {"temperature": 0.0, "num_predict": num_predict},
}
if json_fmt == "true":
    body["format"] = "json"
start = time.monotonic()
req = urllib.request.Request(
    "http://127.0.0.1:11434/api/generate",
    data=json.dumps(body).encode(),
    headers={"Content-Type": "application/json"},
    method="POST",
)
with urllib.request.urlopen(req, timeout=180) as r:
    resp = json.load(r)
elapsed = round(time.monotonic() - start, 1)
print(json.dumps({"text": resp.get("response", ""), "latency": elapsed}))
PY
}

run_bench() {
  local model="$1" prompt="$2" json_fmt="${3:-false}" num_predict="${4:-96}"
  local raw
  raw=$(ollama_generate "$model" "$prompt" "$json_fmt" "$num_predict")
  local out latency
  out=$(echo "$raw" | python3 -c "import sys,json; print(json.load(sys.stdin).get('text',''))")
  latency=$(echo "$raw" | python3 -c "import sys,json; print(json.load(sys.stdin).get('latency',0))")
  echo "${latency}|${out}"
}

log ""
log "=== Phase 2: Pull candidates ==="
if [ "${SKIP_PULL:-0}" = "1" ]; then
  log "(SKIP_PULL=1 — using models already on host)"
else
  for model in "${CANDIDATES[@]}"; do
    log "Pulling $model ..."
    ollama pull "$model" 2>&1 | tail -3 | tee -a "$REPORT"
  done
fi

log ""
log "=== Phase 3: Benchmarks ==="
log "Format: model | test | latency_s | parse | output_preview"
log ""

WINNER=""
BEST_SCORE=-1

for model in "${CANDIDATES[@]}"; do
  model_score=0
  log "--- $model ---"

  # Warm-up (not timed) — load model into memory
  run_bench "$model" "reply ok" false 16 >/dev/null || true

  for tname in match anime; do
    case "$tname" in
      match) tprompt="$MATCH_PROMPT" ;;
      anime) tprompt="$ANIME_PROMPT" ;;
    esac

    result=$(run_bench "$model" "$tprompt" false 96)
    latency="${result%%|*}"
    out="${result#*|}"
    parse=$(score_parse "$out")
    preview=$(echo "$out" | tr '\n' ' ' | head -c 120)
    log "$model | $tname | ${latency}s | $parse | $preview"

    [ "$parse" = "PASS" ] && model_score=$((model_score + 2))
    [ "$parse" = "PARTIAL" ] && model_score=$((model_score + 1))
    if python3 -c "exit(0 if float('$latency') <= 15 else 1)" 2>/dev/null; then
      model_score=$((model_score + 2))
    elif python3 -c "exit(0 if float('$latency') <= 20 else 1)" 2>/dev/null; then
      model_score=$((model_score + 1))
    fi
  done

  result=$(run_bench "$model" "$PACK_PROMPT" true 160)
  latency="${result%%|*}"
  out="${result#*|}"
  parse=$(score_pack "$out")
  preview=$(echo "$out" | tr '\n' ' ' | head -c 120)
  log "$model | pack | ${latency}s | $parse | $preview"

  [ "$parse" = "PASS" ] && model_score=$((model_score + 3))
  [ "$parse" = "PARTIAL" ] && model_score=$((model_score + 1))

  log "$model total score: $model_score"
  if [ "$model_score" -gt "$BEST_SCORE" ]; then
    BEST_SCORE=$model_score
    WINNER=$model
  fi
  log ""
done

# Recommend profile based on model size
if echo "$WINNER" | grep -qE '1b|:1b'; then
  PROFILE="model_size=small verbosity=minimal reply_format=simple confidence_style=classified"
elif echo "$WINNER" | grep -qE '7b|:7b'; then
  PROFILE="model_size=medium verbosity=brief reply_format=json confidence_style=numeric"
else
  PROFILE="model_size=small verbosity=minimal reply_format=simple confidence_style=classified"
fi

log "=== RECOMMENDATION ==="
log "Winner: $WINNER (score $BEST_SCORE)"
log "Powarr profile: $PROFILE"
log "Ollama host for Powarr Integrations: http://$(hostname -I 2>/dev/null | awk '{print $1}' || hostname):11434"
log ""
log "Powarr Settings → LLM Assist:"
log "  - Keep-alive: 10 minutes"
log "  - Batch delay: 1000-2000 ms (CPU-only)"
log "  - Blend weight: 0.2-0.3"
log ""
log "Report saved: $REPORT"
