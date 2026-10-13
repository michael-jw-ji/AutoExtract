"""An OpenAI-compatible LLM endpoint on Modal (vLLM), for the LARGE model.

Used for repairs and for the "does a bigger model fix this?" comparison, so it
should be the strongest model you can afford. The small model stays on Ollama.

One-time setup:
    pip install modal
    modal setup                                        # log in (already done if ~/.modal.toml exists)
    modal secret create autoextract-llm LLM_API_KEY=<make-up-a-long-random-string>

Deploy:
    modal deploy infra/modal_llm.py

`modal deploy` prints the URL, something like
    https://<workspace>--autoextract-llm-serve.modal.run
Then put these in .env (the key is the one you chose above):
    LARGE_LLM_BASE_URL=https://<workspace>--autoextract-llm-serve.modal.run/v1
    LARGE_LLM_API_KEY=<same string>
    LARGE_MODEL=Qwen/Qwen2.5-32B-Instruct               # = MODEL_NAME below
and check it with:  python scripts/check_models.py

Cost control: the container scales to ZERO after SCALEDOWN of no requests and
bills only while a GPU is up. The first request after idle pays a cold start
(~2-5 min to boot vLLM; the weights are cached in a Volume after the first
download). `modal app stop autoextract-llm` takes it down entirely.

Not run by the test suite; it needs a Modal account and a GPU.
"""

from __future__ import annotations

import os
import subprocess

import modal

# Edit these, then redeploy. They are constants (not env vars) because this file
# is imported again inside the container, where your shell env does not exist.
MODEL_NAME = "Qwen/Qwen2.5-32B-Instruct"
#: Full-precision 32B is ~65 GB of weights, so it needs an 80 GB card. H100 is
#: the fastest option that vLLM supports well (about $3.95/hr while running;
#: see modal.com/pricing). Cheaper fallbacks: "A100-80GB", or the 4-bit
#: "Qwen/Qwen2.5-32B-Instruct-AWQ" on "L40S" (small quality cost).
GPU = "H100"
#: Sized from the worst real repair: ~9.4k prompt tokens (schema + reference +
#: the longest filing + a 12k-char attempt) plus max_tokens=8192 from
#: repair/distill.py. vLLM REJECTS any request where prompt + max_tokens
#: exceeds this, so the two numbers must move together.
MAX_MODEL_LEN = 20480
#: KV cache left after 65 GB of weights holds roughly 35-40k tokens, so only a
#: few full-length requests fit at once; vLLM queues the rest.
MAX_CONCURRENT = 4
PORT = 8000
MINUTES = 60
SCALEDOWN = 10 * MINUTES

app = modal.App("autoextract-llm")

image = (
    modal.Image.from_registry("nvidia/cuda:12.9.0-devel-ubuntu22.04", add_python="3.12")
    .entrypoint([])
    .uv_pip_install("vllm==0.21.0", "huggingface_hub[hf_transfer]")
    .env({"HF_HUB_ENABLE_HF_TRANSFER": "1"})
)

# Weights and compiled kernels survive restarts, so only the first boot is slow.
hf_cache = modal.Volume.from_name("autoextract-hf-cache", create_if_missing=True)
vllm_cache = modal.Volume.from_name("autoextract-vllm-cache", create_if_missing=True)


@app.function(
    image=image,
    gpu=GPU,
    scaledown_window=SCALEDOWN,
    timeout=20 * MINUTES,
    volumes={"/root/.cache/huggingface": hf_cache, "/root/.cache/vllm": vllm_cache},
    secrets=[modal.Secret.from_name("autoextract-llm")],
)
@modal.concurrent(max_inputs=MAX_CONCURRENT)
@modal.web_server(port=PORT, startup_timeout=15 * MINUTES)
def serve() -> None:
    # vLLM enforces this as a Bearer token, which is how the OpenAI SDK sends
    # its api_key. Without it the endpoint would be open to the internet.
    key = os.environ["LLM_API_KEY"]
    subprocess.Popen([
        "vllm", "serve", MODEL_NAME,
        "--served-model-name", MODEL_NAME,
        "--host", "0.0.0.0", "--port", str(PORT),
        "--api-key", key,
        "--max-model-len", str(MAX_MODEL_LEN),
        "--max-num-seqs", str(MAX_CONCURRENT),
        "--gpu-memory-utilization", "0.95",
        # No --dtype: vLLM picks float16 for AWQ checkpoints and bf16 otherwise.
    ])
