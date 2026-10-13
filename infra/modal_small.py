"""The SMALL model on Modal: Qwen2.5-7B-Instruct, full precision, via vLLM.

This is the model the loop measures and fine-tunes. It runs on Modal (not
local Ollama) for one reason: hosted training fine-tunes the full-precision
Hugging Face weights, and the before/after comparison is only clean if the
baseline was measured on those SAME weights at the SAME precision. Ollama's
qwen2.5:7b is a 4-bit GGUF -- a different model numerically -- so a baseline
there and a fine-tune here would change two things at once.

LoRA support is switched on: every adapter that infra/modal_train.py wrote to
the `insider-reading-adapters` volume is served by this same server under its own
name (e.g. model="form4-lora-v1"), so base and fine-tune are compared like for
like. Redeploy after training to pick up a new adapter.

    modal deploy infra/modal_small.py

Then in .env:
    LLM_BASE_URL=https://<workspace>--insider-reading-small-serve.modal.run/v1
    LLM_API_KEY=<the same string as LARGE_LLM_API_KEY>
    SMALL_MODEL=Qwen/Qwen2.5-7B-Instruct
    LLM_CONCURRENCY=8

Shares the secret and the weight caches with infra/modal_llm.py.
"""

from __future__ import annotations

import os
import subprocess

import modal

MODEL_NAME = "Qwen/Qwen2.5-7B-Instruct"
#: ~15 GB of bf16 weights on a 48 GB card leaves ~25 GB of KV cache: room for
#: many concurrent 16K requests. About $1.95/hr while up (modal.com/pricing).
GPU = "L40S"
#: Longest filing ~5.4k prompt tokens + max_tokens 8192 (serve/extract.py).
#: vLLM rejects prompt + max_tokens above this, so the two move together.
MAX_MODEL_LEN = 16384
MAX_CONCURRENT = 16
#: Ceiling for fine-tuned adapters served later; LoRA ranks above it fail.
MAX_LORA_RANK = 64
PORT = 8000
MINUTES = 60

app = modal.App("insider-reading-small")

image = (
    modal.Image.from_registry("nvidia/cuda:12.9.0-devel-ubuntu22.04", add_python="3.12")
    .entrypoint([])
    .uv_pip_install("vllm==0.21.0", "huggingface_hub[hf_transfer]")
    .env({"HF_HUB_ENABLE_HF_TRANSFER": "1"})
)

hf_cache = modal.Volume.from_name("insider-reading-hf-cache", create_if_missing=True)
vllm_cache = modal.Volume.from_name("insider-reading-vllm-cache", create_if_missing=True)
adapters = modal.Volume.from_name("insider-reading-adapters", create_if_missing=True)


@app.function(
    image=image,
    gpu=GPU,
    scaledown_window=10 * MINUTES,
    timeout=20 * MINUTES,
    volumes={"/root/.cache/huggingface": hf_cache, "/root/.cache/vllm": vllm_cache,
             "/adapters": adapters},
    secrets=[modal.Secret.from_name("insider-reading-llm")],
)
@modal.concurrent(max_inputs=MAX_CONCURRENT)
@modal.web_server(port=PORT, startup_timeout=15 * MINUTES)
def serve() -> None:
    from pathlib import Path
    loras = [f"{d.name}=/adapters/{d.name}" for d in sorted(Path("/adapters").iterdir())
             if (d / "adapter_config.json").exists()]
    subprocess.Popen([
        "vllm", "serve", MODEL_NAME,
        "--served-model-name", MODEL_NAME,
        "--host", "0.0.0.0", "--port", str(PORT),
        "--api-key", os.environ["LLM_API_KEY"],
        "--max-model-len", str(MAX_MODEL_LEN),
        "--max-num-seqs", str(MAX_CONCURRENT),
        "--gpu-memory-utilization", "0.90",
        "--enable-lora", "--max-lora-rank", str(MAX_LORA_RANK),
        *(["--lora-modules", *loras] if loras else []),
    ])
