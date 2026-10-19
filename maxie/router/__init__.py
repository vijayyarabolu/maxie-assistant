"""Router: decide what to do with a command, as cheaply as possible.

(a) local rules for the very common commands  ->  (b) Laya typed decision (local, free)
->  (c) Haiku when Laya is down  ->  (d) offline lexicon when nothing else is reachable.
Anything that isn't a confident, harmless direct action goes to the brain (Claude).
"""

from .core import ROUTES, Decision, Router

__all__ = ["ROUTES", "Decision", "Router"]
