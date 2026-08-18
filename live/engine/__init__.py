"""The live execution engine.

This package is named `engine`, not `strategy`, and the name is the point.

`live/` used to hold a directory called `strategy/`, which collides with the
simulation package of the same name at the repo root. Neither had an
`__init__.py`, so Python merged them into one implicit namespace package whose
`__path__` spanned both trees. Live code writing `from engine.markets import
...` silently reached into the simulation, and whether it resolved at all
depended on which directories happened to be on `sys.path` -- which depended on
the operator's working directory at the moment a real order was about to go out.

Sealing that directory with an `__init__.py` under the same name does not fix
it: a regular package wins over a namespace package and stops extending across
directories, so the repo-root `strategy` becomes unreachable and every
simulation import in the same process fails. Renaming is the fix; sealing is
not. See `research/RESEARCH_LOG.md` and `live/FORKED_FROM.json`.

Modules copied down from the simulation (`config`, `markets`) are forks, not
imports. They are allowed to diverge -- `tests/test_fork_drift.py` reports when
the root copies move so the divergence is a decision rather than a surprise.
"""
