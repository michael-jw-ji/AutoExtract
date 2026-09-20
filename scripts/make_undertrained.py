"""Build a deliberately weak training set, so the gate has something to reject.

The demo's most important claim is that the promotion gate refuses a candidate
that didn't earn promotion. That claim is only worth anything if the rejection
is reproducible -- and until now the undertrained dataset had been built by
hand, so nobody else could regenerate it.

A 20-example set trained for 1 epoch lands around +1.5pp against a required
+2.0pp: a real improvement, just not enough. That is a far better test of the
gate than noise would be, because it proves the threshold discriminates rather
than simply rejecting anything bad.

    python scripts/make_undertrained.py            # 20 examples
    python scripts/make_undertrained.py --size 40
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from core.db import init_db  # noqa: E402
from train.dataset import build_dataset  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="Build an undertrained dataset")
    ap.add_argument("--size", type=int, default=20,
                    help="examples to include (default 20)")
    args = ap.parse_args()

    init_db()
    manifest = build_dataset(max_examples=args.size, prefix="undertrained")
    print(json.dumps(
        {k: v for k, v in manifest.items() if k != "signatures"}, indent=2
    ))
    print(
        f"\nNext:\n"
        f"  python scripts\\train_local.py --dataset {manifest['path']} --epochs 1\n"
        f"  python scripts\\score_local.py --label local-under --adapter models\\lora-<stamp>\n"
        f"  python scripts\\register_local.py --base local-base --lora local-under "
        f"--adapter models\\lora-<stamp>\n"
        f"\nExpect: GATE: REJECTED"
    )


if __name__ == "__main__":
    main()
