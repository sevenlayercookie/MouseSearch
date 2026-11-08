"""JSON API for watches, mounted at ``/api/watches``."""
from __future__ import annotations

from quart import Blueprint, jsonify, request

from . import runner, store

watches_bp = Blueprint("watches", __name__, url_prefix="/api/watches")


async def _json_body() -> dict:
    data = await request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _error(message: str, status: int = 400):
    return jsonify({"error": message}), status


def _busy():
    return _error("A watch run is already in progress. Try again in a moment.", 409)


@watches_bp.before_request
async def _require_enabled():
    if not runner.setting("WATCHES_ENABLED"):
        return _error("Watches are disabled. Enable them under Settings → Watches.", 403)


@watches_bp.route("", methods=["GET"])
async def list_watches():
    watches = store.list_watches()
    return jsonify({
        "watches": watches,
        "grabs_last_24h": store.grabs_since(24),
        "daily_limit": runner.setting("WATCHES_DAILY_GRAB_LIMIT"),
        "allow_wedges": runner.setting("WATCHES_ALLOW_WEDGES"),
        "busy": runner.is_busy(),
    })


@watches_bp.route("", methods=["POST"])
async def create_watch():
    try:
        watch = store.create_watch(await _json_body())
    except store.WatchValidationError as exc:
        return _error(str(exc))
    return jsonify({"watch": watch, "message": f"Watch '{watch['name']}' saved."}), 201


@watches_bp.route("/preview", methods=["POST"])
async def preview_unsaved_watch():
    """Dry-run a watch definition from the form (saved or not)."""
    data = await _json_body()
    existing = store.get_watch(int(data["id"])) if str(data.get("id") or "").isdigit() else None
    try:
        watch = store.normalize_watch(data, existing=existing)
    except store.WatchValidationError as exc:
        return _error(str(exc))
    if existing:
        # Previewing edits to a saved watch: reuse what it has seen. A changed
        # search re-baselines on save, so preview it that way too.
        search_changed = any(watch[c] != existing[c] for c in store.SEARCH_DEFINING_COLUMNS)
        watch.update(id=existing["id"], baseline_done=existing["baseline_done"] and not search_changed)
    if runner.is_busy():
        return _busy()
    return jsonify(await runner.run_single(watch, dry_run=True))


@watches_bp.route("/<int:watch_id>", methods=["PUT"])
async def update_watch(watch_id: int):
    try:
        watch = store.update_watch(watch_id, await _json_body())
    except store.WatchValidationError as exc:
        return _error(str(exc))
    if watch is None:
        return _error("Watch not found.", 404)
    return jsonify({"watch": watch, "message": f"Watch '{watch['name']}' updated."})


@watches_bp.route("/<int:watch_id>", methods=["DELETE"])
async def delete_watch(watch_id: int):
    if not store.delete_watch(watch_id):
        return _error("Watch not found.", 404)
    return jsonify({"message": "Watch deleted."})


@watches_bp.route("/<int:watch_id>/test", methods=["POST"])
async def test_watch(watch_id: int):
    watch = store.get_watch(watch_id)
    if watch is None:
        return _error("Watch not found.", 404)
    if runner.is_busy():
        return _busy()
    return jsonify(await runner.run_single(watch, dry_run=True))


@watches_bp.route("/<int:watch_id>/run", methods=["POST"])
async def run_watch_now(watch_id: int):
    watch = store.get_watch(watch_id)
    if watch is None:
        return _error("Watch not found.", 404)
    if runner.is_busy():
        return _busy()
    summary = await runner.run_single(watch, dry_run=False)
    summary["watch"] = store.get_watch(watch_id)
    return jsonify(summary)


@watches_bp.route("/<int:watch_id>/events", methods=["GET"])
async def watch_events(watch_id: int):
    if store.get_watch(watch_id) is None:
        return _error("Watch not found.", 404)
    return jsonify({"events": store.events_for(watch_id, 50)})

