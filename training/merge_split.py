"""Merge example files, remove duplicates, balance sources, and split train / eval.

  python merge_split.py --inputs data/n8n.jsonl data/mermaid.jsonl data/logs.jsonl \
      --max-per-source 4000 --eval-ratio 0.05 --out data/final

Writes data/final/train.jsonl and data/final/eval.jsonl. Keep the eval file
untouched: it is how you measure whether fine-tuning actually helped.
"""

import argparse
import hashlib
import json
import random
from collections import Counter, defaultdict
from pathlib import Path

from common import read_records, split_train_eval, write_jsonl


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--inputs", nargs="+", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--eval-ratio", type=float, default=0.05)
    parser.add_argument("--max-per-source", type=int, default=0, help="cap examples per input file (0 = no cap)")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    rng = random.Random(args.seed)
    seen: set[str] = set()
    by_source: dict[str, list[dict]] = defaultdict(list)
    for path in args.inputs:
        for row in read_records(path):
            key = hashlib.sha256(json.dumps(row["messages"][1:], sort_keys=True).encode()).hexdigest()
            if key not in seen:
                seen.add(key)
                by_source[path].append(row)

    rows = []
    for items in by_source.values():
        rng.shuffle(items)
        rows.extend(items[: args.max_per_source] if args.max_per_source else items)

    train, evaluation = split_train_eval(rows, args.eval_ratio, args.seed)
    out = Path(args.out)
    write_jsonl(out / "train.jsonl", train)
    write_jsonl(out / "eval.jsonl", evaluation)
    tasks = Counter(r.get("meta", {}).get("task", "?") for r in rows)
    print(json.dumps({"train": len(train), "eval": len(evaluation), "tasks": tasks,
                      "per_source": {p: len(v) for p, v in by_source.items()}}))


if __name__ == "__main__":
    main()
