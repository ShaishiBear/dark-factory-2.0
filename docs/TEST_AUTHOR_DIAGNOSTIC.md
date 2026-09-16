# Test-author thinking-cap measurement

The owner-dispatched diagnostic measures the actual `test_author` model route with three
fixed, tools-free, one-turn calls. It cannot change worker policy or qualify product work.

Run [35082680640](https://github.com/ShaishiBear/dark-factory-2.0/actions/runs/35082680640)
on 2026-09-16 observed MiniMax M3 at 352 uncapped thinking tokens, 200 with a 1024-token cap,
and 382 with thinking disabled. The original heuristic reported the 1024 cap honored based
on relative reduction. Both samples were below the cap, so that result did not demonstrate
enforcement. The original artifact remains unchanged.

The diagnostic now requires uncapped demand above the cap's allowed range (including its
existing estimation slack). `cap1024_exercised` makes that prerequisite explicit. A false
honored flag can mean insufficient evidence; it does not by itself establish that a route
ignored a cap. Thinking emitted with a zero cap remains direct evidence against disabling.
Even an exercised sample is an observation, not a guarantee about future worker turns.

All worker thinking-cap rows remain unset. No model, role timeout, turn budget, retry limit
or proof rule changes. This correction uses replayed measurements and makes no new API calls.
