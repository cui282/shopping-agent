# Clarify within the existing research task

A task with a blocking ambiguity enters an awaiting-clarification state before any marketplace call,
and the shopper's answer resumes the same thread rather than creating another history entry. This
adds a non-terminal state and reply path to the API, but preserves one coherent research intent and
keeps clarification distinct from rerunning a completed snapshot with fresh market data.
