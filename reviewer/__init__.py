"""Size-adaptive AI code review agent for django/django pull requests.

Pipeline: fetch -> triage -> route -> review -> verify -> groundedness -> synthesize -> report.
Everything above the route fork is cheap and deterministic; everything below it converges back
to a single writer. See config.py for every tunable constant and README.md for the reasoning.
"""

__version__ = "0.1.0"
