# Training a FlowLens model (Option B: small model, fine-tuned locally)

This folder turns outside datasets and your own FlowLens usage into training
data in **FlowLens's exact schema**, fine-tunes **Qwen3-4B-Instruct-2507**
(Apache 2.0) with QLoRA on a single consumer GPU (about 12 GB of VRAM), and measures the
result.

```
 n8n workflows ──► convert_n8n.py ─────┐
 Mermaid charts ─► convert_mermaid.py ─┼─► merge_split.py ─► train.jsonl ─► finetune_unsloth.py ─► GGUF
 FlowLens logs ──► export_logs.py ─────┘                  └► eval.jsonl ──► evaluate.py (before / after)
```

## What the model learns

FlowLens asks the model for three things. Each example teaches one of them,
using the same system prompt and JSON shape the backend uses
(`backend/app/llm/prompts.py`):

| Task | Input | Output | Data source |
|---|---|---|---|
| `flowchart` | a description in plain words | full graph: nodes, edges, analysis | n8n + Mermaid datasets, logs |
| `workflow_labels` | a workflow extracted from code | names + summaries for each node | logs (your reviewed runs) |
| `file_summaries` | file symbols and imports | one sentence per file | logs |

**The workflow's shape (branches, loops, errors) always comes from the code
analysis, never from the model.** Fine-tuning improves names, explanations
and prompt-mode graphs.

## 0. Set up

```bash
cd training
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r ../backend/requirements.txt
```

## 1. Convert public datasets

Both converters detect field names automatically. Pass `--prompt-field` /
`--workflow-field` / `--code-field` if they guess wrong. Add `--limit 50`
for a quick test first.

```bash
# n8n workflows (prompt -> workflow)
python convert_n8n.py --input hf:Ker102/n8n-workflows-36k --out data/n8n.jsonl
python convert_n8n.py --input hf:MustaphaL/training_data_n8n_workflows --out data/n8n_2.jsonl

# Mermaid flowcharts (prompt -> flowchart)
python convert_mermaid.py --input hf:CoreFidelity/Mermaid_50K --out data/mermaid.jsonl

# Local files work too: .jsonl, .json or .parquet
python convert_mermaid.py --input my_pairs.jsonl --prompt-field description --code-field mermaid --out data/mine.jsonl
```

Each run prints how many records were read, skipped and written.

**What the conversion does:**

| n8n | FlowLens |
|---|---|
| `*Trigger`, `webhook` | `trigger` |
| `if` / `filter` (output 0 / 1) | `decision` with `yes` / `no` edges |
| `switch` | `decision` with `case N` edges |
| `splitInBatches` | `loop` (`each` / `done` + back-edge) |
| `agent`, `chainLlm` | `agent`; its chat-model sub-node becomes the agent's service |
| `ai_tool` sub-nodes | `tool` nodes with `tool` edges |
| `onError: continueErrorOutput` | `error` edge |
| `respondToWebhook`, `noOp`, `stopAndError` | `end` |
| sticky notes | removed |

| Mermaid | FlowLens |
|---|---|
| `{...}` / `{{...}}` | `decision` |
| first node with no incoming edge | `start` |
| `([...])`, `((...))` or "End/Done" with no outgoing edge | `end` |
| edge labels Yes/No (and similar) | `yes` / `no` edges |
| `A & B --> C` | edges from both |

Invalid or oversized graphs (more than 30 nodes) are dropped. Ids are
renumbered to `n1..nN` and every graph passes the backend validator.

> **Licenses:** many community datasets were scraped from public sites, so
> their licenses can be unclear. Read each dataset card before you **publish**
> a model trained on it. Data you generate yourself is the safest.

## 2. Collect your own data (best quality)

1. Set `TRAINING_LOG=true` in `.env`, then `docker compose up -d api`.
2. Use FlowLens on code you're allowed to keep: your own repos, or MIT/Apache projects.
3. Copy the logs out and **review them**. Fix bad labels directly in each line's `"response"`, and delete lines that are wrong:
   ```bash
   docker compose cp api:/data/training ./training/logs
   ```
4. Export them:
   ```bash
   python export_logs.py --logs logs --out data/logs.jsonl
   ```

Even 300–500 reviewed examples improve the model noticeably on *your* kind of code.

**Turn `TRAINING_LOG` off on a public instance** unless users have explicitly
opted in. It stores the content they send.

## 3. Merge and split

```bash
python merge_split.py --inputs data/n8n.jsonl data/mermaid.jsonl data/logs.jsonl \
    --max-per-source 4000 --eval-ratio 0.05 --out data/final
```

- **Sources:** cap large public sources (`--max-per-source`) so they don't drown out your own reviewed data.
- **Size:** 2,000–8,000 examples in total is a good range for this task.
- **Eval set:** never train on `eval.jsonl`.

## 4. Measure the base model first

Keep the FlowLens model server running (`docker compose --profile gpu up -d llm-gpu`):

```bash
python evaluate.py --data data/final/eval.jsonl --api-key "$LLM_API_KEY" --out reports/base.json
```

## 5. Fine-tune on your GPU

Training needs the GPU to itself:

```bash
docker compose stop llm-gpu
```

Install Unsloth in a **separate** environment, because it pins its own PyTorch.
For RTX 50-series GPUs, use the official Docker image or follow Unsloth's
Blackwell install guide:

```bash
# Option 1: Docker (simplest; same image for all NVIDIA GPUs)
docker run --gpus all -it --rm -v "$PWD/..":/work -w /work/training unsloth/unsloth bash

# Option 2: pip in a fresh venv
python -m venv .venv-train && source .venv-train/bin/activate
pip install unsloth          # see Unsloth's docs for the RTX 50-series variant
```

Then:

```bash
python finetune_unsloth.py --train data/final/train.jsonl --eval data/final/eval.jsonl --out runs/flowlens-4b
```

| Flag | Default | Notes |
|---|---|---|
| `--base` | `unsloth/Qwen3-4B-Instruct-2507` | Any chat model Unsloth supports |
| `--max-seq` | `4096` | The script warns about longer examples |
| `--rank` | `16` | LoRA rank. 32 learns more but uses more VRAM. |
| `--epochs` | `2` | Use 3 for under 2k examples, 1 for over 10k |
| `--batch` / `--grad-accum` | `2` / `4` | Lower `--batch` to 1 on out-of-memory |
| `--gguf` | `q4_k_m` | Exported for llama.cpp; `none` to skip |

Only the assistant answers are trained (`train_on_responses_only`), so the
long system prompts don't waste capacity.

**Outputs:**
- `runs/flowlens-4b/lora/`: the adapter.
- `runs/flowlens-4b/gguf/`: the merged GGUF model.

## 6. Use the fine-tuned model in FlowLens

Upload the GGUF to a Hugging Face repo (private is fine):

```bash
huggingface-cli upload <you>/flowlens-4b-GGUF runs/flowlens-4b/gguf .
```

Then in `.env`:

```env
LLM_GGUF_REPO=<you>/flowlens-4b-GGUF:Q4_K_M
LLM_CPU_MOE=0          # a 4B dense model fits fully in VRAM
LLM_PARALLEL=4         # small model: more requests at once
HF_TOKEN=hf_...        # needed for a private repo
```

```bash
docker compose --profile gpu up -d --force-recreate llm-gpu
python evaluate.py --data data/final/eval.jsonl --api-key "$LLM_API_KEY" --out reports/finetuned.json
```

**Compare `reports/base.json` with `reports/finetuned.json`.** Ship the new
model only if `valid` and `type_match` go up. Also read 20 or so answers yourself.

## 7. Improve in a loop

1. Run FlowLens with the new model and `TRAINING_LOG=true`.
2. Fix the bad answers, then export, merge, retrain and evaluate.

Every round makes the model better at the code *you* actually analyze.

## Releasing the model

Qwen3-4B-Instruct-2507 is Apache 2.0. If you publish your fine-tune:
- Include the Apache 2.0 license.
- Credit the base model ("fine-tuned from Qwen/Qwen3-4B-Instruct-2507").
- List the datasets you used.
- Give it your own name, e.g. "FlowLens-4B", without implying endorsement by the Qwen team.