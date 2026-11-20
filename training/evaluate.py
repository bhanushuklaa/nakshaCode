"""Measure a model on the held-out eval set, before and after fine-tuning.

Point it at any OpenAI-compatible server (the llama.cpp container on :8081):

  python evaluate.py --data data/final/eval.jsonl --base-url http://localhost:8081/v1 \
      --api-key $LLM_API_KEY --limit 200 --out reports/base.json

Run once with the base model and once with your fine-tuned GGUF, then compare.

Metrics
-------
  json_ok       answer parsed as JSON
  valid         flowchart: passes the backend validator; labels: returned every node id
  node_count    flowchart: |predicted nodes - reference nodes| (lower is better)
  type_match    flowchart: share of reference node types present in the prediction
  seconds       average time per answer
"""

import argparse
import json
import statistics
import time
from collections import Counter
from pathlib import Path

import httpx

from common import Graph, find_problems, prompts, read_records

SCHEMAS = {
    prompts.FLOWCHART_SYSTEM: ("flowchart", prompts.FLOWCHART_SCHEMA),
    prompts.WORKFLOW_SYSTEM: ("workflow_labels", prompts.WORKFLOW_SCHEMA),
    prompts.SUMMARY_SYSTEM: ("file_summaries", prompts.SUMMARY_SCHEMA),
}


def _graph(answer: dict) -> Graph:
    return Graph(mode=answer.get("mode", "flowchart"), title=answer.get("title", ""),
                 nodes=[{**n, "services": [n["service"]] if n.get("service") else []} for n in answer["nodes"]],
                 edges=answer["edges"])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", required=True)
    parser.add_argument("--base-url", default="http://localhost:8081/v1")
    parser.add_argument("--api-key", default="not-needed")
    parser.add_argument("--model", default="flowlens-llm")
    parser.add_argument("--limit", type=int, default=200)
    parser.add_argument("--out")
    args = parser.parse_args()

    # Imported lazily so the module loads without the backend's config.
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
    from app.llm.client import LLMError, parse_json

    http = httpx.Client(base_url=args.base_url.rstrip("/"), timeout=300,
                        headers={"Authorization": f"Bearer {args.api_key}"})
    results: dict[str, list[dict]] = {}

    for example in read_records(args.data, limit=args.limit):
        messages = example["messages"]
        task, schema = SCHEMAS.get(messages[0]["content"], (None, None))
        if task is None:
            continue
        reference = json.loads(messages[-1]["content"])
        started = time.time()
        response = http.post("/chat/completions", json={
            "model": args.model, "messages": messages[:-1], "temperature": 0.2, "max_tokens": 3000,
            "response_format": {"type": "json_schema", "json_schema": {"name": task, "schema": schema, "strict": True}},
        })
        row = {"seconds": time.time() - started, "json_ok": False, "valid": False}
        try:
            response.raise_for_status()
            answer = parse_json(response.json()["choices"][0]["message"]["content"] or "")
            row["json_ok"] = True
            if task == "flowchart":
                graph = _graph(answer)
                row["valid"] = not find_problems(graph)
                row["node_count"] = abs(len(answer["nodes"]) - len(reference["nodes"]))
                want, got = Counter(n["type"] for n in reference["nodes"]), Counter(n["type"] for n in answer["nodes"])
                row["type_match"] = sum((want & got).values()) / max(sum(want.values()), 1)
            elif task == "workflow_labels":
                sent = {s["id"] for s in json.loads(messages[-2]["content"])["steps"]}
                row["valid"] = sent == {s.get("id") for s in answer.get("steps", [])}
            else:
                row["valid"] = all(f.get("summary") for f in answer.get("files", []))
        except (httpx.HTTPError, LLMError, KeyError, TypeError, ValueError):
            pass
        results.setdefault(task, []).append(row)
        print(f"{task}: json={row['json_ok']} valid={row['valid']} {row['seconds']:.1f}s", flush=True)

    report = {}
    for task, rows in results.items():
        summary = {"n": len(rows)}
        for metric in ("json_ok", "valid", "node_count", "type_match", "seconds"):
            values = [float(r[metric]) for r in rows if metric in r]
            if values:
                summary[metric] = round(statistics.mean(values), 3)
        report[task] = summary
    print(json.dumps(report, indent=2))
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
