"""Turn FlowLens training logs (TRAINING_LOG=true) into chat training examples.

With TRAINING_LOG=true the backend writes every model request + answer to
DATA_DIR/training/YYYY-MM-DD.jsonl. Review those answers (fix bad labels
directly in the "response" field, or delete bad lines), then export:

  # copy logs out of the Docker volume
  docker compose cp api:/data/training ./training/logs

  python export_logs.py --logs training/logs --out data/logs.jsonl
  python export_logs.py --logs training/logs --task workflow_labels --out data/labels.jsonl

Only answers that pass the same checks the backend applies are exported.
"""

import argparse
import hashlib
import json
from pathlib import Path

from common import Graph, find_problems, write_jsonl

TASKS = {"file_summaries", "workflow_labels", "flowchart"}


def _valid(task: str, messages: list[dict], response: dict) -> bool:
    if task == "flowchart":
        try:
            graph = Graph(mode=response.get("mode", "flowchart"), title=response.get("title", ""),
                          nodes=[{**n, "services": [n["service"]] if n.get("service") else []} for n in response["nodes"]],
                          edges=response["edges"])
        except (KeyError, TypeError, ValueError):
            return False
        return not find_problems(graph)
    if task == "workflow_labels":
        try:
            sent = {n["id"] for n in json.loads(messages[-1]["content"])["steps"]}
        except (KeyError, json.JSONDecodeError):
            return False
        got = {s.get("id") for s in response.get("steps", [])}
        return sent == got and all(s.get("label") for s in response.get("steps", []))
    if task == "file_summaries":
        return bool(response.get("files")) and all(f.get("summary") for f in response["files"])
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--logs", required=True, help="folder with YYYY-MM-DD.jsonl files")
    parser.add_argument("--out", required=True)
    parser.add_argument("--task", choices=sorted(TASKS), help="only export one task")
    args = parser.parse_args()

    seen: set[str] = set()
    stats = {"read": 0, "duplicate": 0, "invalid": 0, "written": 0}

    def examples():
        for file in sorted(Path(args.logs).glob("*.jsonl")):
            for line in file.open(encoding="utf-8"):
                if not line.strip():
                    continue
                stats["read"] += 1
                record = json.loads(line)
                task = record.get("task")
                if task not in TASKS or (args.task and task != args.task):
                    continue
                key = hashlib.sha256(json.dumps(record["messages"], sort_keys=True).encode()).hexdigest()
                if key in seen:
                    stats["duplicate"] += 1
                    continue
                seen.add(key)
                if not _valid(task, record["messages"], record["response"]):
                    stats["invalid"] += 1
                    continue
                stats["written"] += 1
                yield {
                    "messages": record["messages"] + [
                        {"role": "assistant", "content": json.dumps(record["response"], ensure_ascii=False)}
                    ],
                    "meta": {"task": task, "source": f"log:{file.name}"},
                }

    write_jsonl(args.out, examples())
    print(json.dumps(stats))


if __name__ == "__main__":
    main()
