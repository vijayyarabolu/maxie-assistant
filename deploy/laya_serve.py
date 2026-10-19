"""The optional local Laya router server (`make laya`, or launchd's dev.maxie.laya on a Mac).

Laya (pip package `laya`, Apache 2.0) is a small non-autoregressive classifier: one forward pass gives the
router a typed decision with a calibrated probability. The English checkpoint (421M parameters) comes from
Hugging Face (convaiinnovations/laya) the first time it loads; nothing is in this repo.

Bound to 127.0.0.1 only. The API key is LAYA_API_KEY from Maxie's secret store (made on first run if there
is none, so the hub and this server agree). The checkpoint revision is pinned to the reviewed SHA inside the
laya package, and SHA-256 digests are checked when deploy/laya_digests.json has them.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIGESTS = ROOT / "deploy" / "laya_digests.json"
sys.path.insert(0, str(ROOT))


def api_key() -> str:
    from maxie.config import secret, store_secret
    key = secret("LAYA_API_KEY")
    if not key:
        key = secrets.token_urlsafe(24)
        store_secret("LAYA_API_KEY", key)
    return key


def device() -> str:
    if sys.platform == "darwin":
        return "mps"
    try:
        import torch
        return "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        return "cpu"


# Loads on the first request and unloads after 5 idle minutes: loaded, it held 2.4 GB on my 8 GB Mac.
# One checkpoint at a time, fp16 autocast on the GPU. launchd starts it at low priority (Nice 10).
ENV = {
    "LAYA_HOST": "127.0.0.1",              # never 0.0.0.0
    "LAYA_PORT": "8000",
    "LAYA_MODELS": "english",              # checkpoint "laya" (English, 421M parameters)
    "LAYA_DEFAULT_MODEL": "english",
    "LAYA_PRELOAD": "0",                   # load on the first request, not at start
    "LAYA_IDLE_UNLOAD_SECONDS": "300",     # give the memory back after 5 idle minutes
    "LAYA_MAX_LOADED": "1",
    "LAYA_MPS_AMP_MIN_ROWS": "1",          # fp16 autocast on MPS even for one command
    "LAYA_REVISION": "reviewed",           # the reviewed commit SHA pinned inside the laya package
    "LAYA_LOG_LEVEL": "info",
}


def main() -> None:
    env = {"LAYA_API_KEY": api_key(), "LAYA_DEVICE": device(), **ENV}
    for k, v in env.items():
        os.environ.setdefault(k, v)
    # Digests are per checkpoint name; laya applies LAYA_SHA256_DIGESTS to every load, so they are
    # set only when exactly one checkpoint is loaded and it is in the file.
    models = [m for m in os.environ["LAYA_MODELS"].split(",") if m]
    pinned = json.loads(DIGESTS.read_text()) if DIGESTS.exists() else {}
    if "LAYA_SHA256_DIGESTS" not in os.environ and len(models) == 1 and models[0] in pinned:
        os.environ["LAYA_SHA256_DIGESTS"] = json.dumps(pinned[models[0]])
    from laya.serve import main as serve
    serve()


if __name__ == "__main__":
    main()
