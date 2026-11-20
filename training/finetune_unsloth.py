"""QLoRA fine-tuning of a small model for nakshaCode with Unsloth (Option B).


  # 1. free the GPU: stop the model server while training
  docker compose stop llm-gpu

  # 2. train (about 6-9 GB VRAM with the defaults)
  python finetune_unsloth.py --train data/final/train.jsonl --eval data/final/eval.jsonl \
      --out runs/nakshacode-4b

  # 3. outputs
  runs/nakshacode-4b/lora/        LoRA adapter (small, for further training)
  runs/nakshacode-4b/gguf/        merged model as GGUF (q4_k_m) for llama.cpp

Install (see training/README.md): a fresh venv with `pip install unsloth`
"""

import argparse
import dataclasses
import inspect
import json
from pathlib import Path


def build_kwargs(cls, **wanted):
    """Pass only the arguments this library version understands (TRL renames things often)."""
    if dataclasses.is_dataclass(cls):
        names = {f.name for f in dataclasses.fields(cls)}
    else:
        names = set(inspect.signature(cls).parameters)
    renames = {"max_seq_length": "max_length", "eval_strategy": "evaluation_strategy"}
    for new, old in renames.items():
        if new in wanted and new not in names and old in names:
            wanted[old] = wanted.pop(new)
    if "processing_class" in names:
        wanted.pop("tokenizer", None)  # newer TRL: pass the tokenizer as processing_class only
    return {k: v for k, v in wanted.items() if k in names}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--train", required=True)
    parser.add_argument("--eval")
    parser.add_argument("--out", default="runs/flowlens-4b")
    parser.add_argument("--base", default="unsloth/Qwen3-4B-Instruct-2507")
    parser.add_argument("--max-seq", type=int, default=4096)
    parser.add_argument("--rank", type=int, default=16)
    parser.add_argument("--epochs", type=float, default=2)
    parser.add_argument("--batch", type=int, default=2)
    parser.add_argument("--grad-accum", type=int, default=4)
    parser.add_argument("--lr", type=float, default=2e-4)
    parser.add_argument("--gguf", default="q4_k_m", help="GGUF quantization, or 'none' to skip export")
    parser.add_argument("--push-repo", help="optional Hugging Face repo id to upload the GGUF to")
    args = parser.parse_args()

    # Import here so `--help` works without a GPU stack installed.
    from unsloth import FastLanguageModel  # noqa: I001  (unsloth must be imported before trl/transformers)
    from unsloth.chat_templates import train_on_responses_only
    from datasets import load_dataset
    from trl import SFTConfig, SFTTrainer

    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=args.base, max_seq_length=args.max_seq, load_in_4bit=True,
    )
    model = FastLanguageModel.get_peft_model(
        model, r=args.rank, lora_alpha=args.rank, lora_dropout=0, bias="none",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
        use_gradient_checkpointing="unsloth", random_state=3407,
    )

    files = {"train": args.train} | ({"eval": args.eval} if args.eval else {})
    data = load_dataset("json", data_files=files)

    def to_text(batch):
        return {"text": [tokenizer.apply_chat_template(m, tokenize=False, add_generation_prompt=False)
                         for m in batch["messages"]]}

    data = data.map(to_text, batched=True, remove_columns=[c for c in data["train"].column_names if c != "text"])
    too_long = sum(len(tokenizer(t)["input_ids"]) > args.max_seq for t in data["train"]["text"])
    if too_long:
        print(f"warning: {too_long} training examples are longer than --max-seq {args.max_seq} and will be cut")

    config = SFTConfig(**build_kwargs(
        SFTConfig,
        output_dir=str(Path(args.out) / "checkpoints"),
        dataset_text_field="text",
        max_seq_length=args.max_seq,
        per_device_train_batch_size=args.batch,
        gradient_accumulation_steps=args.grad_accum,
        num_train_epochs=args.epochs,
        learning_rate=args.lr,
        lr_scheduler_type="cosine",
        warmup_ratio=0.03,
        weight_decay=0.01,
        optim="adamw_8bit",
        logging_steps=10,
        save_steps=200,
        save_total_limit=2,
        eval_strategy="steps" if args.eval else "no",
        eval_steps=200,
        bf16=True,
        report_to="none",
        seed=3407,
    ))
    trainer_args = build_kwargs(
        SFTTrainer, model=model, args=config, train_dataset=data["train"],
        eval_dataset=data.get("eval"), processing_class=tokenizer, tokenizer=tokenizer,
    )
    trainer = SFTTrainer(**trainer_args)
    # Learn only the assistant answers, not the long prompts.
    trainer = train_on_responses_only(trainer, instruction_part="<|im_start|>user\n",
                                      response_part="<|im_start|>assistant\n")
    stats = trainer.train()
    print(json.dumps({"train_loss": stats.training_loss}))

    out = Path(args.out)
    model.save_pretrained(str(out / "lora"))
    tokenizer.save_pretrained(str(out / "lora"))
    if args.gguf != "none":
        model.save_pretrained_gguf(str(out / "gguf"), tokenizer, quantization_method=args.gguf)
        if args.push_repo:
            model.push_to_hub_gguf(args.push_repo, tokenizer, quantization_method=args.gguf)
    print(f"done: {out}")


if __name__ == "__main__":
    main()
