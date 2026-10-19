"""Calibration: pick per-route confidence thresholds from labelled examples.

Laya's checkpoints are over-confident out of the box, so we don't trust a raw probability.
For each route we find the lowest threshold at which predictions of that route are right
at least `target` of the time (with enough support). Until there are `min_examples` labels
(about 200: the starter set + his real commands), the router stays in "uncalibrated" mode:
only harmless direct actions skip Claude. Re-run weekly (the scheduler does).
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path

NEVER = 1.01  # a threshold no probability reaches


@dataclass
class Calibration:
    calibrated: bool = False
    n: int = 0
    thresholds: dict[str, float] = field(default_factory=dict)
    accuracy: float = 0.0
    updated: float = 0.0

    def threshold(self, route: str, default: float = 0.9) -> float:
        return self.thresholds.get(route, default)

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(asdict(self), indent=2))

    @classmethod
    def load(cls, path: Path) -> Calibration:
        return cls(**json.loads(path.read_text())) if path.exists() else cls()


def calibrate(examples: Iterable[tuple[str, str]], predict: Callable[[str], tuple[str, float] | None],
              target: float = 0.95, min_support: int = 5, min_examples: int = 200) -> Calibration:
    preds = []
    for text, label in examples:
        p = predict(text)
        if p is not None:
            preds.append((p[0], p[1], label))
    thresholds = {}
    for route in sorted({p[0] for p in preds}):
        mine = [(conf, pred == label) for pred, conf, label in preds if pred == route]
        best = NEVER
        # Walk thresholds from high to low; stop at the first that misses the target.
        for t in sorted({c for c, _ in mine}, reverse=True):
            kept = [ok for c, ok in mine if c >= t]
            if sum(kept) / len(kept) < target:
                break
            if len(kept) >= min_support:
                best = t
        thresholds[route] = round(best, 4)
    correct = sum(pred == label for pred, _, label in preds)
    return Calibration(calibrated=len(preds) >= min_examples, n=len(preds), thresholds=thresholds,
                       accuracy=round(correct / len(preds), 4) if preds else 0.0, updated=time.time())


def load_labels(path: Path) -> list[tuple[str, str]]:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return [(r["text"], r["route"]) for r in rows]
