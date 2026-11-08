"""
Watch runner: search -> filter -> dedupe -> account guards -> grab.

Account safety comes first (the approach is borrowed from MLM's autograbber):

- Live MAM stats are read once per tick. If they are unavailable, nothing is
  grabbed ("fail closed").
- Unsatisfied-torrent slots: a reserve of slots is always left free.
- A trailing 24h cap on grabs across all watches.
- Cost policy per watch: ``free`` (only torrents that are already free for
  you), ``wedge`` (free, or spend a wedge you already own if wedges are
  allowed in Settings and you have more than the reserve), ``ratio`` (as
  ``wedge``, then download normally if your ratio stays above the floor).
- Watches never buy anything with bonus points.

Matches that cannot be grabbed yet (cap, limits, not free) are not marked
seen, so a later run retries them. The first run of a new watch only records
what already exists (baseline) unless the watch opts in to grabbing it.

The runner never imports ``app``; ``app.py`` injects what it needs through
``configure(...)``. Tests inject fakes.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

from . import store

logger = logging.getLogger("watches")
logger.addHandler(logging.NullHandler())

RESULTS_PER_SEARCH = 100
PAUSE_BETWEEN_WATCHES_SECONDS = 5
# HTTP statuses from MAM that mean "stop for now" (auth, blocked, rate limited).
ABORT_STATUS_CODES = {401, 403, 406, 429}

SETTING_DEFAULTS = {
    "WATCHES_ENABLED": False,
    "WATCHES_ALLOW_WEDGES": False,
    "WATCHES_WEDGE_RESERVE": 5,
    "WATCHES_UNSAT_RESERVE": 10,
    "WATCHES_MIN_RATIO": 2.0,
    "WATCHES_DAILY_GRAB_LIMIT": 10,
}


async def _noop_async(*_args, **_kwargs):
    return None


deps = SimpleNamespace(
    search=None,        # async (params: dict) -> list[dict]  (items carry download_link, size_gb, ...)
    account=None,       # async () -> dict | None  (see Budget.from_account)
    client_mids=None,   # async () -> set[str] | None  (MAM ids already in the torrent client)
    add=None,           # async (item: dict, *, category: str, use_wedge: bool) -> dict
    notify=_noop_async,  # async (event, success, **details)
    toast=_noop_async,   # async (message, category)
    logger=logger,
    config=lambda key, default=None: default,
)

_run_lock = asyncio.Lock()


class WatchAbort(RuntimeError):
    """MAM refused us (auth/blocked/rate-limited): stop the whole tick."""


def configure(**overrides) -> None:
    for key, value in overrides.items():
        if not hasattr(deps, key):
            raise KeyError(f"unknown runner dependency: {key}")
        setattr(deps, key, value)


def setting(key: str):
    default = SETTING_DEFAULTS[key]
    value = deps.config(key, default)
    try:
        if isinstance(default, bool):
            return value if isinstance(value, bool) else str(value).strip().lower() in ("1", "true", "on", "yes")
        if isinstance(default, float):
            return float(value)
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def is_busy() -> bool:
    return _run_lock.locked()


# --------------------------------------------------------------------------- budget
@dataclass
class Budget:
    """What this tick may still spend. ``None`` stats = unknown = grab nothing."""
    available: bool
    reason: str = ""
    unsat_slots: int = 0
    wedges: int = 0
    ratio_allowance_gb: float = 0.0
    daily_remaining: int = 0
    vip_active: bool = False

    @classmethod
    def from_account(cls, account: dict | None, *, grabs_last_24h: int) -> "Budget":
        daily_remaining = max(0, setting("WATCHES_DAILY_GRAB_LIMIT") - grabs_last_24h)
        if not account:
            return cls(False, "MAM account stats unavailable", daily_remaining=daily_remaining)
        try:
            unsat_count = int(account["unsat_count"])
            unsat_limit = int(account["unsat_limit"])
            wedges = int(account.get("wedges") or 0)
            uploaded = float(account["uploaded_gb"])
            downloaded = float(account["downloaded_gb"])
        except (KeyError, TypeError, ValueError):
            return cls(False, "MAM account stats incomplete", daily_remaining=daily_remaining)

        min_ratio = max(1.0, setting("WATCHES_MIN_RATIO"))
        spendable_wedges = wedges - setting("WATCHES_WEDGE_RESERVE") if setting("WATCHES_ALLOW_WEDGES") else 0
        return cls(
            True,
            unsat_slots=max(0, unsat_limit - unsat_count - setting("WATCHES_UNSAT_RESERVE")),
            wedges=max(0, spendable_wedges),
            # Download while uploaded / (downloaded + x) stays >= min_ratio.
            ratio_allowance_gb=max(0.0, uploaded / min_ratio - downloaded),
            daily_remaining=daily_remaining,
            vip_active=bool(account.get("vip_active")),
        )

    def blocker(self) -> str | None:
        """Why no grab at all is possible right now, if so."""
        if not self.available:
            return self.reason
        if self.daily_remaining <= 0:
            return "daily grab limit reached"
        if self.unsat_slots <= 0:
            return "unsatisfied-torrent reserve reached"
        return None

    def payment_for(self, item: dict, cost_policy: str) -> str | None:
        """How this item would be paid for: 'free', 'wedge', 'ratio' or None (not allowed)."""
        if item["is_free"]:
            return "free"
        if cost_policy in ("wedge", "ratio") and self.wedges > 0:
            return "wedge"
        if cost_policy == "ratio" and 0 < item["size_gb"] <= self.ratio_allowance_gb:
            return "ratio"
        return None

    def spend(self, item: dict, payment: str) -> None:
        self.unsat_slots -= 1
        self.daily_remaining -= 1
        if payment == "wedge":
            self.wedges -= 1
        elif payment == "ratio":
            self.ratio_allowance_gb -= item["size_gb"]


def _flag(value) -> bool:
    return str(value or "0").strip().lower() in ("1", "true", "yes")


def annotate_cost(item: dict, *, vip_active: bool) -> None:
    """Set ``is_free`` / ``vip_freeleech`` from MAM's flags (VIP freeleech counts only while VIP)."""
    vip_freeleech = _flag(item.get("fl_vip")) and vip_active
    item["vip_freeleech"] = int(vip_freeleech)
    item["is_free"] = _flag(item.get("free")) or _flag(item.get("personal_freeleech")) or vip_freeleech


def not_allowed_reason(cost_policy: str) -> str:
    if cost_policy == "free":
        return "not freeleech (watch only grabs free torrents)"
    if cost_policy == "wedge":
        return "not freeleech and no spendable wedge"
    return "not freeleech, no spendable wedge, and it would drop ratio below the floor"


# --------------------------------------------------------------------------- search
def build_search_params(watch: dict) -> dict:
    """MAM ``loadSearchJSONbasic.php`` params for a watch: newest first, one page."""
    params: dict[str, Any] = {
        "tor[text]": watch["query"],
        "tor[searchType]": "all",
        "tor[searchIn]": "torrents",
        "tor[sortType]": "dateDesc",
        "perpage": RESULTS_PER_SEARCH,
        "dlLink": "true",
    }
    for field in watch.get("search_in") or ["author"]:
        params[f"tor[srchIn][{field}]"] = "true"
    if watch.get("language_ids"):
        params["tor[browse_lang][]"] = list(watch["language_ids"])
    if watch.get("main_cat"):
        params["tor[main_cat][]"] = list(watch["main_cat"])
    return params


def is_due(watch: dict, now: datetime | None = None) -> bool:
    if not watch.get("enabled"):
        return False
    last_run = store.parse_iso(watch.get("last_run_at"))
    if last_run is None:
        return True
    now = now or datetime.now(timezone.utc)
    return now - last_run >= timedelta(minutes=max(store.MIN_INTERVAL_MINUTES, int(watch.get("interval_minutes") or 60)))


def _status_code(exc: BaseException) -> int | None:
    response = getattr(exc, "response", None)
    code = getattr(response, "status_code", None) or getattr(exc, "status_code", None)
    return int(code) if isinstance(code, int) else None


def summarize(summary: dict) -> str:
    verb = "would grab" if summary["dry_run"] else "grabbed"
    parts = [f"found {summary['found']}", f"new {summary['new']}", f"{verb} {summary['grabbed']}"]
    if summary["deferred"]:
        parts.append(f"waiting {summary['deferred']}")
    if summary["baseline"]:
        parts.append(f"baseline {summary['baseline']}")
    if summary["errors"]:
        parts.append(f"errors {summary['errors']}")
    return ", ".join(parts)


# --------------------------------------------------------------------------- core
async def run_watch(watch: dict, *, dry_run: bool, budget: Budget, client_mids: set[str] | None = None) -> dict:
    """
    Run one watch against a shared ``budget``. ``dry_run`` grabs and writes
    nothing (the budget is still simulated so the preview is honest).
    Raises ``WatchAbort`` when MAM refuses the request.
    """
    watch_id = watch.get("id")
    persist = not dry_run and bool(watch_id)
    log = deps.logger
    baseline = not watch.get("baseline_done") and not watch.get("grab_existing")
    summary: dict[str, Any] = {
        "watch_id": watch_id, "name": watch.get("name"), "dry_run": dry_run, "baseline_run": baseline,
        "found": 0, "new": 0, "grabbed": 0, "deferred": 0, "baseline": 0, "errors": 0,
        "error": None, "items": [],
    }

    def finish(searched: bool):
        summary["summary_text"] = summarize(summary)
        if persist:
            store.record_run(watch_id, summary=summary["summary_text"], searched=searched)
        return summary

    try:
        results = list(await deps.search(build_search_params(watch)) or [])
    except Exception as exc:
        code = _status_code(exc)
        message = f"search failed: {exc}" if str(exc) else f"search failed ({exc.__class__.__name__})"
        log.error(f"[WATCH] '{watch.get('name')}' {message}")
        if persist:
            store.log_event(watch_id, "error", detail=message)
        if code in ABORT_STATUS_CODES:
            raise WatchAbort(f"MAM returned HTTP {code}") from exc
        summary["errors"] += 1
        summary["error"] = message
        return finish(searched=False)

    summary["found"] = len(results)
    pattern = re.compile(watch["title_regex"], re.IGNORECASE) if watch.get("title_regex") else None
    seen = store.seen_ids(watch_id) if watch_id else set()
    already_grabbed = store.grabbed_by_any_watch(str(item.get("id") or "") for item in results)
    in_client = client_mids or set()
    cap = int(watch.get("max_grabs_per_run") or 1)
    attempts = 0  # failed adds count too, so a broken client cannot cause a burst of MAM downloads
    newly_seen: list[tuple[str, str]] = []

    for item in results:
        annotate_cost(item, vip_active=budget.vip_active)
        mam_id = str(item.get("id") or "")
        title = str(item.get("title") or "")
        entry = {"id": mam_id, "title": title, "author": item.get("author_info") or "",
                 "size": item.get("size") or "", "added": item.get("added") or "",
                 "free": bool(item.get("is_free")), "action": "", "detail": ""}

        if not mam_id or (pattern and not pattern.search(title)):
            continue  # not a match; not shown
        summary["items"].append(entry)
        if mam_id in seen:
            entry["action"], entry["detail"] = "seen", "already handled by this watch"
            continue
        summary["new"] += 1

        if str(item.get("my_snatched") or "0") == "1" or mam_id in in_client or mam_id in already_grabbed:
            entry["action"], entry["detail"] = "have", "already snatched or in your client"
            newly_seen.append((mam_id, title))
            continue

        if baseline:
            entry["action"], entry["detail"] = "baseline", "existed when the watch was created"
            summary["baseline"] += 1
            newly_seen.append((mam_id, title))
            continue

        blocker = "per-run limit reached" if attempts >= cap else budget.blocker()
        payment = None if blocker else budget.payment_for(item, watch.get("cost") or "free")
        if blocker or payment is None:
            entry["action"], entry["detail"] = "waiting", blocker or not_allowed_reason(watch.get("cost") or "free")
            summary["deferred"] += 1
            continue

        attempts += 1
        if dry_run:
            budget.spend(item, payment)
            entry["action"], entry["detail"] = "grab", f"would grab ({payment})"
            summary["grabbed"] += 1
            continue

        try:
            result = await deps.add(item, category=watch.get("torrent_category") or "", use_wedge=payment == "wedge")
        except Exception as exc:
            result = {"error": str(exc) or exc.__class__.__name__}
        result = result or {}
        if result.get("error") or result.get("status") == "insufficient_buffer":
            message = result.get("error") or result.get("message") or "unknown error"
            entry["action"], entry["detail"] = "error", message
            summary["errors"] += 1
            log.warning(f"[WATCH] '{watch['name']}' could not grab {mam_id} '{title}': {message}")
            store.log_event(watch_id, "error", mam_id=mam_id, title=title, detail=message)
            continue

        budget.spend(item, payment)
        summary["grabbed"] += 1
        entry["action"], entry["detail"] = "grab", f"grabbed ({payment})"
        store.mark_seen(watch_id, [(mam_id, title)], grabbed=True)
        store.log_event(watch_id, "grabbed", mam_id=mam_id, title=title, detail=f"paid with: {payment}")
        log.info(f"[WATCH] '{watch['name']}' grabbed {mam_id} '{title}' ({payment})")
        try:
            await deps.notify("watch_grabbed", True, task="watch", watch=watch["name"], title=title,
                              mid=mam_id, message=f"paid with: {payment}")
            await deps.toast(f"Watch '{watch['name']}' grabbed: {title}", "success")
        except Exception as exc:  # pragma: no cover - notifications must never break a run
            log.warning(f"[WATCH] notification failed: {exc}")

    if persist and newly_seen:
        store.mark_seen(watch_id, newly_seen)
        if summary["baseline"]:
            store.log_event(watch_id, "baseline",
                            detail=f"{summary['baseline']} existing result(s) recorded; only newer releases will be grabbed")
    return finish(searched=True)


async def _load_context() -> tuple[Budget, set[str] | None]:
    account = None
    try:
        account = await deps.account() if deps.account else None
    except Exception as exc:
        deps.logger.warning(f"[WATCH] Could not read MAM account stats: {exc}")
    client_mids = None
    try:
        client_mids = await deps.client_mids() if deps.client_mids else None
    except Exception as exc:
        deps.logger.warning(f"[WATCH] Could not list torrents in the client: {exc}")
    return Budget.from_account(account, grabs_last_24h=store.grabs_since(24)), client_mids


async def run_single(watch: dict, *, dry_run: bool) -> dict:
    """Run (or preview) one watch now, outside the schedule."""
    async with _run_lock:
        budget, client_mids = await _load_context()
        before = budget_view(budget)
        try:
            summary = await run_watch(watch, dry_run=dry_run, budget=budget, client_mids=client_mids)
        except WatchAbort as exc:
            return {"error": str(exc), "items": [], "summary_text": str(exc), "dry_run": dry_run}
        summary["budget"] = before
        return summary


def budget_view(budget: Budget) -> dict:
    return {
        "available": budget.available, "blocker": budget.blocker(),
        "unsat_slots": budget.unsat_slots, "wedges": budget.wedges,
        "ratio_allowance_gb": round(budget.ratio_allowance_gb, 2), "daily_remaining": budget.daily_remaining,
    }


async def run_all_watches() -> list[dict]:
    """Scheduler tick: run every due watch, sharing one budget, one at a time."""
    if not setting("WATCHES_ENABLED") or _run_lock.locked():
        return []
    async with _run_lock:
        try:
            due = [w for w in store.list_watches() if is_due(w)]
        except Exception as exc:
            deps.logger.error(f"[WATCH] Unable to load watches: {exc}")
            return []
        if not due:
            return []

        budget, client_mids = await _load_context()
        deps.logger.info(f"[WATCH] Tick: {len(due)} watch(es) due; budget {budget_view(budget)}")
        summaries = []
        for index, watch in enumerate(due):
            if index:
                await asyncio.sleep(PAUSE_BETWEEN_WATCHES_SECONDS)
            try:
                summary = await run_watch(watch, dry_run=False, budget=budget, client_mids=client_mids)
                summaries.append(summary)
                deps.logger.info(f"[WATCH] '{watch['name']}': {summary['summary_text']}")
            except WatchAbort as exc:
                deps.logger.warning(f"[WATCH] Stopping this tick: {exc}")
                break
            except Exception as exc:
                deps.logger.error(f"[WATCH] '{watch.get('name')}' crashed: {exc}", exc_info=True)
                try:
                    store.log_event(watch["id"], "error", detail=f"run crashed: {exc}")
                    store.record_run(watch["id"], summary=f"crashed: {exc}", searched=False)
                except Exception:
                    pass
        return summaries
