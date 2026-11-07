"""The run ledger: phase timings, token usage per phase and model, cost, and pipeline counts.

One Ledger per run. Model calls record into it from worker threads (lock-held); the pipeline
sets counts as it goes; `report()` produces the JSON sidecar.
"""

import json
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from . import config


@dataclass
class Call:
    phase: str
    model: str
    tokens_in: int
    tokens_out: int
    seconds: float
    attempt: int  # 1 = first try, 2 = the single retry


@dataclass
class Ledger:
    label: str = ""
    source: str = ""
    started_at: float = field(default_factory=time.time)
    phase_seconds: dict[str, float] = field(default_factory=dict)
    calls: list[Call] = field(default_factory=list)
    counts: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    # --- recording ---------------------------------------------------------------------------

    @contextmanager
    def phase(self, name: str) -> Iterator[None]:
        t0 = time.perf_counter()
        try:
            yield
        finally:
            with self._lock:
                self.phase_seconds[name] = self.phase_seconds.get(name, 0.0) + (time.perf_counter() - t0)

    def record_call(self, phase: str, model: str, tokens_in: int, tokens_out: int, seconds: float, attempt: int) -> None:
        with self._lock:
            self.calls.append(Call(phase, model, tokens_in, tokens_out, seconds, attempt))

    def note(self, text: str) -> None:
        with self._lock:
            self.notes.append(text)

    # --- derived -----------------------------------------------------------------------------

    def tokens(self) -> dict[str, dict[str, dict[str, int]]]:
        """{phase: {model: {in, out, calls}}}"""
        out: dict[str, dict[str, dict[str, int]]] = {}
        for c in self.calls:
            m = out.setdefault(c.phase, {}).setdefault(c.model, {"in": 0, "out": 0, "calls": 0})
            m["in"] += c.tokens_in
            m["out"] += c.tokens_out
            m["calls"] += 1
        return out

    def cost(self) -> dict[str, Any]:
        """USD by model from config.PRICES; models missing from the table are flagged, not guessed."""
        by_model: dict[str, float] = {}
        unpriced: set[str] = set()
        for c in self.calls:
            price = config.PRICES.get(c.model)
            if price is None:
                unpriced.add(c.model)
                continue
            by_model[c.model] = by_model.get(c.model, 0.0) + (
                c.tokens_in * price["input"] + c.tokens_out * price["output"]
            ) / 1_000_000
        return {
            "usd_total": round(sum(by_model.values()), 6),
            "usd_by_model": {m: round(v, 6) for m, v in by_model.items()},
            "prices_recorded": config.PRICES_RECORDED,
            "unpriced_models": sorted(unpriced),
        }

    def report(self) -> dict[str, Any]:
        total_in = sum(c.tokens_in for c in self.calls)
        total_out = sum(c.tokens_out for c in self.calls)
        return {
            "label": self.label,
            "source": self.source,
            "models": {"review": config.REVIEW_MODEL, "triage": config.TRIAGE_MODEL},
            "latency_seconds": {
                "total": round(sum(self.phase_seconds.values()), 3),
                "by_phase": {k: round(v, 3) for k, v in self.phase_seconds.items()},
            },
            "tokens": {"in": total_in, "out": total_out, "by_phase": self.tokens()},
            "cost": self.cost(),
            "calls": len(self.calls),
            "retries": sum(1 for c in self.calls if c.attempt > 1),
            **self.counts,
            "notes": self.notes,
        }

    def write(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.report(), indent=2) + "\n", encoding="utf-8")
