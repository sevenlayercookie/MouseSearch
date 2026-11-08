"""
Watches (beta): saved MAM searches that run on a schedule and grab new matches.

- ``store``   sqlite persistence (watches, per-watch seen ids, event history)
- ``runner``  search -> filter -> dedupe -> account guards -> grab
- ``routes``  JSON API mounted at ``/api/watches``
"""
