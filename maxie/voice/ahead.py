"""Speaking a streamed reply without gaps. Kokoro takes ~0.5 s a sentence (0.9 s for a long one); made
one after another, each sentence waited for that after the one before had played. Here every piece is handed to
one synthesis thread the moment it arrives and played in order on another, so the next is ready when this ends."""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor

import numpy as np

Audio = tuple[np.ndarray, int]


class Ahead:
    """`say(text, made)` plays one piece (False when cut short); `synth(text)` makes it. After a piece is cut short,
    or once `cut()` is true (a barge-in), nothing more is played."""

    def __init__(self, say: Callable[[str, Future], bool], synth: Callable[[str], Audio],
                 cut: Callable[[], bool] = lambda: False):
        self._say, self._synth, self._cut = say, synth, cut
        self._makes = ThreadPoolExecutor(1, thread_name_prefix="synth")
        self._pieces: queue.Queue = queue.Queue()
        self._player = threading.Thread(target=self._play, name="speak", daemon=True)
        self.added = self.spoken = 0
        self.stopped = False
        self.error: Exception | None = None
        self._player.start()

    def add(self, text: str) -> None:
        if not text.strip() or self.stopped:
            return
        self.added += 1
        self._pieces.put((text, self._makes.submit(self._synth, text)))

    def _play(self) -> None:
        while (piece := self._pieces.get()) is not None:
            if self.stopped or self._cut():
                self.stopped = True
                continue
            text, made = piece
            try:
                ok = self._say(text, made)
            except Exception as e:  # raised again by finish(), on the thread that handles the turn  # noqa: BLE001
                self.error, ok = e, False
            if ok:
                self.spoken += 1
            else:
                self.stopped = True

    def finish(self) -> None:
        """Waits until every piece has played (or was dropped after a cut); raises what a piece failed with."""
        self._pieces.put(None)
        self._player.join()
        self._makes.shutdown(wait=False, cancel_futures=True)
        if self.error is not None:
            raise self.error
