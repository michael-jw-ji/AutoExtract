"""Train the LoRA locally, on one consumer GPU.

Why this exists: Baseten Training Jobs returned 403 ("not authorized for
Baseten training") for this workspace, confirmed with both the official CLI
and truss. Local training is the fallback -- and it is arguably better
science, because it compares Qwen-base against Qwen+LoRA. Same model, one
variable, which is the clean A/B the Baseten path could not give us without
first deploying a base endpoint.

Memory plan for 8GB:
  * Qwen2.5-1.5B-Instruct in bf16 (~3.1GB) -- NO quantization, so no
    bitsandbytes, which is the genuinely painful Windows dependency
  * LoRA only, so optimizer state is tiny
  * gradient checkpointing, batch 1, grad accumulation for the effective batch
  * COMPACT_PROMPT=1 keeps sequences near 1.8k instead of 3.7k tokens

    COMPACT_PROMPT=1 python scripts/train_local.py --epochs 3
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from core.config import ROOT, settings

MODELS_DIR = ROOT / "models"


def main() -> None:
    ap = argparse.ArgumentParser(description="Local LoRA fine-tune")
    ap.add_argument("--dataset", default=None, help="path to a train-*.jsonl")
    ap.add_argument("--base", default=None, help="HF base model id")
    ap.add_argument("--epochs", type=float, default=3.0)
    ap.add_argument("--lora-r", type=int, default=16)
    ap.add_argument("--lr", type=float, default=2e-4)
    # 2560, not 2048: with the compact prompt the longest example lands near
    # 2.1k tokens, so a 2048 window would truncate the assistant's answer off
    # the end of the worst cases. Still comfortable for 1.5B on 8GB.
    ap.add_argument("--max-length", type=int, default=2560)
    ap.add_argument("--batch", type=int, default=1)
    ap.add_argument("--accum", type=int, default=16)
    args = ap.parse_args()

    if not settings.compact_prompt:
        print(
            "WARNING: COMPACT_PROMPT is not set. With the full JSON Schema in\n"
            "every example, sequences exceed --max-length and the assistant's\n"
            "answer gets truncated away. Build the dataset AND train AND score\n"
            "with COMPACT_PROMPT=1, or raise --max-length and expect OOM.\n"
        )

    import torch
    from datasets import load_dataset
    from peft import LoraConfig
    from transformers import AutoModelForCausalLM, AutoTokenizer
    from trl import SFTConfig, SFTTrainer

    if not torch.cuda.is_available():
        raise SystemExit(
            "CUDA not available to torch. Check the install:\n"
            "  uv pip install --index-url https://download.pytorch.org/whl/cu128 torch"
        )
    dev = torch.cuda.get_device_properties(0)
    print(f"GPU: {dev.name}  {dev.total_memory / 1024**3:.1f} GB  sm_{dev.major}{dev.minor}")

    dataset_path = args.dataset or str(
        sorted((settings.dataset_dir).glob("train-*.jsonl"))[-1]
    )
    base = args.base or settings.local_base_model
    print(f"dataset: {dataset_path}")
    print(f"base   : {base}")

    ds = load_dataset("json", data_files=dataset_path, split="train")
    print(f"examples: {len(ds)}")

    tok = AutoTokenizer.from_pretrained(base)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token

    # Length sanity check against the real tokenizer, not a char heuristic.
    # Render to text first, then tokenize. apply_chat_template(tokenize=True)
    # returns a BatchEncoding in current transformers, so len() counts its
    # KEYS (2) rather than tokens -- which silently turns this whole guard
    # into a no-op. Measured the wrong thing once already.
    lens = [
        len(tok(tok.apply_chat_template(r["messages"], tokenize=False))["input_ids"])
        for r in ds.select(range(min(64, len(ds))))
    ]
    lens.sort()
    print(f"token length p50={lens[len(lens)//2]}  max(sample)={lens[-1]}  "
          f"max_length={args.max_length}")
    if lens[-1] > args.max_length:
        over = sum(1 for x in lens if x > args.max_length)
        print(f"  WARNING: {over}/{len(lens)} sampled examples exceed max_length "
              f"and WILL be truncated from the right, cutting the answer.")

    model = AutoModelForCausalLM.from_pretrained(
        base, torch_dtype=torch.bfloat16, device_map={"": 0}, use_cache=False,
    )

    peft_config = LoraConfig(
        r=args.lora_r,
        lora_alpha=args.lora_r * 2,
        target_modules="all-linear",
        lora_dropout=0.05,
        task_type="CAUSAL_LM",
    )

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = MODELS_DIR / f"lora-{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)

    steps = max(1, int(len(ds) * args.epochs / (args.batch * args.accum)))
    wanted = dict(
        learning_rate=args.lr,
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch,
        gradient_accumulation_steps=args.accum,
        gradient_checkpointing=True,
        max_length=args.max_length,
        warmup_steps=max(1, steps // 10),
        lr_scheduler_type="cosine",
        logging_steps=1,
        save_strategy="no",
        bf16=True,
        report_to=[],
        output_dir=str(out_dir),
        # Train on the assistant turn only. Without this the model spends most
        # of its loss re-learning the prompt it receives on every request.
        assistant_only_loss=True,
    )

    # TRL's SFTConfig signature drifts between versions -- 1.13 dropped
    # warmup_ratio for warmup_steps, for instance. Filter to what this build
    # actually accepts instead of guessing, and say what got dropped rather
    # than silently training with different settings than intended.
    supported = set(getattr(SFTConfig, "__dataclass_fields__", {})) or set(
        __import__("inspect").signature(SFTConfig.__init__).parameters
    )
    dropped = sorted(k for k in wanted if k not in supported)
    if dropped:
        print(f"  NOTE: SFTConfig (trl) does not accept {dropped}; dropping them")
    cfg = SFTConfig(**{k: v for k, v in wanted.items() if k in supported})
    print(f"steps ~{steps}  loss: "
          f"{'assistant-only' if 'assistant_only_loss' in supported else 'full-sequence'}")

    trainer = SFTTrainer(
        model=model, args=cfg, train_dataset=ds,
        processing_class=tok, peft_config=peft_config,
    )
    trainer.train()
    trainer.save_model(str(out_dir))
    tok.save_pretrained(str(out_dir))

    (out_dir / "meta.json").write_text(
        json.dumps({
            "base": base, "dataset": dataset_path, "examples": len(ds),
            "epochs": args.epochs, "lora_r": args.lora_r, "lr": args.lr,
            "max_length": args.max_length,
            "compact_prompt": settings.compact_prompt,
        }, indent=2), encoding="utf-8",
    )
    print(f"\nadapter saved -> {out_dir}")
    print("next:")
    print(f"  python scripts/score_local.py --label after --adapter {out_dir}")


if __name__ == "__main__":
    main()
