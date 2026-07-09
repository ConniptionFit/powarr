# Crucible Ollama Model Selection for Powarr

Evaluation date: 2026-07-09

## Executive summary

**Recommended model: `qwen2.5:3b`**

**Recommended Powarr profile (Integrations preset):**

| Setting | Value |
|---------|-------|
| Model | `qwen2.5:3b` |
| Host | `http://<crucible-ip>:11434` |
| Model Size Profile | Small |
| Verbosity | Minimal |
| Reply Format | Simple |
| Confidence Style | Classified |
| Keep-alive | 10 minutes |
| Batch delay | 1500 ms |
| Blend weight | 0.25 |

**Fallback:** `llama3.2:3b` with the same Small/Simple/Classified profile if Qwen misparses or is slower on your specific CPU.

**Do not use on CPU-only crucible:** `qwen2.5:7b` or reasoning models (`deepseek-r1`, `lfm2.5`) — 7B is too slow for Powarr's 15–20s background-scan timeout on CPU; reasoning models burn the token budget.

---

## Network access findings

The cloud agent environment **cannot reach crucible or the home LAN**:

| Target | Result |
|--------|--------|
| `powers@crucible` | DNS resolution failure (no SSH config / no mDNS) |
| `10.1.1.1–20` SSH | Connection reset during banner exchange |
| `10.1.1.2:7979` Powarr health | No response |
| `10.1.1.x:11434` Ollama API | TCP connects but no HTTP response |

An SSH agent key **is** available (`ED25519` via `/run/host-services/ssh-auth.sock`) but no reachable host accepts it.

**Action required on your LAN:** run the benchmark script directly on crucible (see below).

---

## How to run the benchmark on crucible

From any machine that can SSH to crucible (e.g. your docker host):

```bash
# Copy and run on crucible
ssh powers@crucible 'bash -s' < scripts/crucible-ollama-benchmark.sh

# Or SSH in and run locally
ssh powers@crucible
cd /path/to/powarr
./scripts/crucible-ollama-benchmark.sh
```

The script will:

1. Inventory CPU, RAM, disk, and existing Ollama models
2. Select candidate tier from RAM (see matrix below)
3. Pull `qwen2.5:3b`, `llama3.2:3b`, and optionally `qwen2.5:7b`
4. Benchmark match, anime/translation, and pack prompts via Powarr's `/api/generate` pattern
5. Score latency, parse quality, and output correctness
6. Print a winner and recommended Powarr profile

Report is saved to `/tmp/powarr-ollama-benchmark/report-*.txt`.

---

## RAM → candidate tier matrix

| Available RAM | Models tested | Notes |
|---------------|---------------|-------|
| < 6 GB | `llama3.2:1b`, `qwen2.5:3b` | 3B may be tight; 1B is fallback |
| 6–13 GB | `qwen2.5:3b`, `llama3.2:3b` | **Sweet spot for CPU-only** |
| 14 GB+ | above + `qwen2.5:7b` | Only keep 7B if latency ≤ 20s warmed |

---

## Why `qwen2.5:3b` wins for Powarr media matching

Powarr's LLM assist reviews **individual** failed-import matches — it never bulk-processes. The deterministic scorer remains primary; the LLM only nudges confidence (default blend 0.3).

For Sonarr/Radarr/Lidarr filename conflicts:

1. **Task fit:** agree/disagree + more/less/same confidence on a single release↔candidate pair — a 3B instruct model handles this reliably in Simple format.
2. **Multilingual/anime:** Powarr v0.20.0 judging guidance explicitly asks the model to consider translations and alternate titles; Qwen 2.5 is stronger here than Llama 3.2.
3. **Latency budget:** Powarr Small profile timeout is **15s**; 3B Q4 on CPU typically warms to 8–15s per call, while 7B often exceeds 20–60s.
4. **Output reliability:** Simple + Classified avoids JSON parse failures that plague small models in JSON mode.
5. **Pack matching:** Hardest task (JSON array, many files). On CPU + 3B, expect partial results on large packs — acceptable because pack review is on-demand, not background scan.
6. **Powarr preset:** Listed first in `MODEL_PRESETS` as "solid small all-rounder" with pre-tuned profile.

---

## Powarr configuration (after benchmark)

From your docker host (or any machine reaching Powarr at `10.1.1.2:7979`):

```bash
./scripts/crucible-powarr-config.sh <crucible-ip> qwen2.5:3b http://10.1.1.2:7979
```

This applies the **episode-alignment prompts** (match + pack) plus the Small/Simple/Classified profile.

### Backend prompts (built-in defaults since v0.24.x)

**Import matching** (`match_prompt` — empty uses default):

```
Same work + season + episode (TV) = match. Ignore codec/resolution/group tags.
Release: {release}
Library: {candidate}
{context}
```

Powarr appends the deterministic scorer summary, compact rules, and (with Simple/Classified) asks for one line:
`agree or disagree | more or less or same | short reason`

**Season pack mapping** (`pack_prompt` — empty uses default):

```
Map each file to season+episode for "{candidate}". Parse S##E## or absolute ep# from filenames.
Pack: {release}
Files: {files}
{context}
```

Powarr appends short match-type definitions (minimal verbosity) and a JSON array reply.

Or manually in the UI:

1. **Integrations → Ollama:** enable, host `http://<crucible-ip>:11434`, model `qwen2.5:3b`, select preset "qwen2.5:3b — solid small all-rounder"
2. **Save → Test Connection → Benchmark Model** (target: parses ✓, < 15s)
3. **Settings → LLM Assist:** confirm Small / Minimal / Simple / Classified; keep-alive 10m; batch delay 1500ms
4. **Settings → Failed Import Matching:** blend weight 0.25 (0.2 if model feels weak)

---

## Expected benchmark results (CPU-only, typical 4–8 core host)

These are reference ranges from Powarr's CPU-oriented tuning (v0.7–v0.10); run the script on crucible for your actual numbers.

| Model | Match (warmed) | Anime case | Pack JSON | Parse (Simple) | Verdict |
|-------|----------------|------------|-----------|----------------|---------|
| `qwen2.5:3b` | 8–15s | agree, considers translation | 2–3 files OK | PASS | **Use** |
| `llama3.2:3b` | 8–16s | agree, may miss translation nuance | 1–2 files | PASS | Fallback |
| `llama3.2:1b` | 4–8s | often disagree on anime | 1 file | PARTIAL | RAM < 6GB only |
| `qwen2.5:7b` | 25–90s | good quality | full array possible | PASS (JSON) | Too slow for background scan |

---

## Scripts added

| Script | Purpose |
|--------|---------|
| [`scripts/crucible-ollama-benchmark.sh`](../scripts/crucible-ollama-benchmark.sh) | Full hardware + model benchmark on crucible |
| [`scripts/crucible-powarr-config.sh`](../scripts/crucible-powarr-config.sh) | Apply winner to Powarr via API |

---

## Cloud agent limitation note

Ollama 0.31.2 was installed in the cloud VM for validation but **llama-server segfaults** on inference in that environment (CPU/sandbox incompatibility). Live model inference benchmarks must be run on crucible itself.
