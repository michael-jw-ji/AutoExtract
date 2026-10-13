"""LoRA fine-tuning of the serving 7B on Modal (the loop's "train" step).

Fine-tunes Qwen/Qwen2.5-7B-Instruct, the same bf16 weights the server in
infra/modal_small.py serves (same Hugging Face cache volume), so a before/after
comparison changes one thing: the adapter. The adapter is written to the
`autoextract-adapters` volume, which the server mounts and loads by name.

Loss is on the assistant tokens only (the JSON answer); the system prompt and
the page are context. A small fixed validation slice reports held-out loss.

    modal run infra/modal_train.py --dataset data/datasets/form4_lora_v1.jsonl --name form4-lora-v1

then redeploy the server so it loads the new adapter:
    modal deploy infra/modal_small.py
"""

from __future__ import annotations

import json
import time

import modal

MODEL_NAME = "Qwen/Qwen2.5-7B-Instruct"
#: bf16 7B (~15 GB) + LoRA + activations for 8K-token examples with gradient
#: checkpointing fit comfortably in 80 GB. ~$3.95/hr while running.
GPU = "H100"
MAX_LEN = 8192
MINUTES = 60

app = modal.App("autoextract-train")

image = (
    modal.Image.debian_slim(python_version="3.12")
    .uv_pip_install("torch==2.7.1", "transformers==4.53.2", "peft==0.16.0",
                    "accelerate==1.9.0", "huggingface_hub[hf_transfer]")
    .env({"HF_HUB_ENABLE_HF_TRANSFER": "1"})
)

hf_cache = modal.Volume.from_name("autoextract-hf-cache", create_if_missing=True)
adapters = modal.Volume.from_name("autoextract-adapters", create_if_missing=True)


@app.function(image=image, gpu=GPU, timeout=90 * MINUTES,
              volumes={"/root/.cache/huggingface": hf_cache, "/adapters": adapters})
def train(examples: list[dict], name: str, epochs: int = 2, rank: int = 16,
          lr: float = 1e-4, grad_accum: int = 8, seed: int = 0) -> dict:
    import random

    import torch
    from peft import LoraConfig, get_peft_model
    from transformers import AutoModelForCausalLM, AutoTokenizer, get_cosine_schedule_with_warmup

    torch.manual_seed(seed)
    tok = AutoTokenizer.from_pretrained(MODEL_NAME)
    model = AutoModelForCausalLM.from_pretrained(MODEL_NAME, torch_dtype=torch.bfloat16,
                                                 device_map="cuda")
    model.gradient_checkpointing_enable()
    model.enable_input_require_grads()
    model = get_peft_model(model, LoraConfig(
        r=rank, lora_alpha=2 * rank, lora_dropout=0.05, task_type="CAUSAL_LM",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                        "gate_proj", "up_proj", "down_proj"]))

    def encode(ex: dict) -> dict | None:
        msgs = ex["messages"]
        prompt = tok.apply_chat_template(msgs[:-1], tokenize=True, add_generation_prompt=True)
        full = tok.apply_chat_template(msgs, tokenize=True)
        if full[:len(prompt)] != prompt or len(full) > MAX_LEN:
            return None
        return {"input_ids": full, "labels": [-100] * len(prompt) + full[len(prompt):]}

    data = [e for e in map(encode, examples) if e is not None]
    dropped = len(examples) - len(data)
    rng = random.Random(seed)
    rng.shuffle(data)
    n_val = max(1, len(data) // 20)
    val, tr = data[:n_val], data[n_val:]

    def loss_of(batch: dict) -> torch.Tensor:
        ids = torch.tensor([batch["input_ids"]], device="cuda")
        labels = torch.tensor([batch["labels"]], device="cuda")
        return model(input_ids=ids, labels=labels).loss

    @torch.no_grad()
    def val_loss() -> float:
        model.eval()
        out = sum(loss_of(b).item() for b in val) / len(val)
        model.train()
        return round(out, 4)

    steps = epochs * len(tr) // grad_accum
    opt = torch.optim.AdamW([p for p in model.parameters() if p.requires_grad], lr=lr,
                            weight_decay=0.0)
    sched = get_cosine_schedule_with_warmup(opt, max(1, steps // 10), steps)
    history = [{"step": 0, "val_loss": val_loss()}]
    model.train()
    started, step, running = time.time(), 0, 0.0
    for epoch in range(epochs):
        rng.shuffle(tr)
        for i, b in enumerate(tr, 1):
            loss = loss_of(b) / grad_accum
            loss.backward()
            running += loss.item()
            if i % grad_accum == 0:
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                opt.step(); sched.step(); opt.zero_grad()
                step += 1
                if step % 5 == 0 or step == steps:
                    print(f"epoch {epoch + 1} step {step}/{steps} train_loss {running / 5:.4f}",
                          flush=True)
                    running = 0.0
        history.append({"step": step, "epoch": epoch + 1, "val_loss": val_loss()})
        print(f"epoch {epoch + 1} done: val_loss {history[-1]['val_loss']}", flush=True)

    out = f"/adapters/{name}"
    model.save_pretrained(out)
    tok.save_pretrained(out)
    summary = {"name": name, "base_model": MODEL_NAME, "examples": len(examples),
               "dropped_too_long": dropped, "train": len(tr), "val": len(val),
               "epochs": epochs, "rank": rank, "lr": lr, "grad_accum": grad_accum,
               "steps": step, "history": history, "gpu": GPU,
               "train_seconds": round(time.time() - started)}
    with open(f"{out}/training_summary.json", "w") as fh:
        json.dump(summary, fh, indent=2)
    adapters.commit()
    return summary


@app.local_entrypoint()
def main(dataset: str, name: str, epochs: int = 2, rank: int = 16) -> None:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from core import runlog

    examples = [json.loads(x) for x in Path(dataset).read_text(encoding="utf-8").splitlines()]
    with runlog.run("train"):
        print(f"training {name} on {len(examples)} examples from {dataset} ({GPU})", flush=True)
        summary = train.remote(examples, name, epochs=epochs, rank=rank)
        print(json.dumps(summary, indent=2))
        runlog.save_result({"training": summary, "dataset": dataset})
