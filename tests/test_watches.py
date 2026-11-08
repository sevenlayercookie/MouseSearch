import tempfile
import unittest
from pathlib import Path

from watches import runner, store


def mam_item(mam_id, title="Book", *, free=0, fl_vip=0, personal_freeleech=0, my_snatched=0, size_gb=0.5):
    return {
        "id": mam_id, "title": title, "author_info": "Author", "size": f"{size_gb} GiB", "size_gb": size_gb,
        "added": f"2026-10-{int(mam_id) % 28 + 1:02d} 12:00:00", "free": free, "fl_vip": fl_vip,
        "personal_freeleech": personal_freeleech, "my_snatched": my_snatched,
        "download_link": f"https://mam.example/tor/download.php/x?tid={mam_id}",
    }


def account(**overrides):
    data = {"unsat_count": 5, "unsat_limit": 50, "wedges": 10, "uploaded_gb": 500.0,
            "downloaded_gb": 100.0, "vip_active": False}
    data.update(overrides)
    return data


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        store.configure(Path(self.tmp.name) / "watches.db")

    def tearDown(self):
        store.close()
        self.tmp.cleanup()


class NormalizeWatchTests(unittest.TestCase):
    def test_defaults_are_safe(self):
        watch = store.normalize_watch({"name": "Klune", "query": '"T J Klune"'})
        self.assertEqual(watch["cost"], "free")
        self.assertEqual(watch["search_in"], ["author"])
        self.assertEqual(watch["title_regex"], "")
        self.assertFalse(watch["grab_existing"])
        self.assertEqual(watch["interval_minutes"], 60)

    def test_rejects_invalid_definitions(self):
        bad = [
            {"query": "x"},
            {"name": "x"},
            {"name": "x", "query": "x", "title_regex": "("},
            {"name": "x", "query": "x", "interval_minutes": 5},
            {"name": "x", "query": "x", "max_grabs_per_run": 0},
            {"name": "x", "query": "x", "max_grabs_per_run": 50},
            {"name": "x", "query": "x", "cost": "bonus_points"},
        ]
        for data in bad:
            with self.subTest(data=data), self.assertRaises(store.WatchValidationError):
                store.normalize_watch(data)

    def test_filters_unknown_fields_and_ids(self):
        watch = store.normalize_watch({
            "name": "x", "query": "x", "search_in": ["author", "description", "series"],
            "main_cat": ["13", "all"], "language_ids": "1,abc",
        })
        self.assertEqual(watch["search_in"], ["author", "series"])
        self.assertEqual(watch["main_cat"], ["13"])
        self.assertEqual(watch["language_ids"], ["1"])


class StoreTests(StoreTestCase):
    def test_changing_the_search_resets_the_baseline(self):
        watch = store.create_watch({"name": "A", "query": "a"})
        store.record_run(watch["id"], summary="ok", searched=True)
        self.assertTrue(store.get_watch(watch["id"])["baseline_done"])

        store.update_watch(watch["id"], {"interval_minutes": 90, "enabled": False})
        self.assertTrue(store.get_watch(watch["id"])["baseline_done"])

        store.update_watch(watch["id"], {"query": "b"})
        self.assertFalse(store.get_watch(watch["id"])["baseline_done"])

    def test_failed_search_does_not_complete_the_baseline(self):
        watch = store.create_watch({"name": "A", "query": "a"})
        store.record_run(watch["id"], summary="search failed", searched=False)
        stored = store.get_watch(watch["id"])
        self.assertFalse(stored["baseline_done"])
        self.assertIsNotNone(stored["last_run_at"])

    def test_seen_and_cross_watch_grabbed(self):
        a = store.create_watch({"name": "A", "query": "a"})
        b = store.create_watch({"name": "B", "query": "b"})
        store.mark_seen(a["id"], [("1", "One")], grabbed=True)
        store.mark_seen(a["id"], [("1", "One"), ("2", "Two")])  # must not downgrade "1"
        self.assertEqual(store.seen_ids(a["id"]), {"1", "2"})
        self.assertEqual(store.grabbed_by_any_watch(["1", "2", "3"]), {"1"})
        self.assertEqual(store.seen_ids(b["id"]), set())

    def test_delete_cascades(self):
        watch = store.create_watch({"name": "A", "query": "a"})
        store.mark_seen(watch["id"], [("1", "One")])
        store.log_event(watch["id"], "grabbed", mam_id="1")
        self.assertTrue(store.delete_watch(watch["id"]))
        self.assertEqual(store.seen_ids(watch["id"]), set())
        self.assertEqual(store.events_for(watch["id"]), [])

    def test_grabs_since_counts_grabbed_events_only(self):
        watch = store.create_watch({"name": "A", "query": "a"})
        store.log_event(watch["id"], "grabbed", mam_id="1")
        store.log_event(watch["id"], "grabbed", mam_id="2")
        store.log_event(watch["id"], "error", mam_id="3")
        self.assertEqual(store.grabs_since(24), 2)


class ConfigMixin:
    """Swap the runner's injected dependencies for the test and put them back after."""

    def setUp(self):
        self._saved_deps = dict(vars(runner.deps))
        super().setUp()

    def tearDown(self):
        super().tearDown()
        vars(runner.deps).update(self._saved_deps)

    def set_config(self, **values):
        settings = dict(runner.SETTING_DEFAULTS, WATCHES_ENABLED=True)
        settings.update(values)
        runner.configure(config=lambda key, default=None: settings.get(key, default))


class BudgetTests(ConfigMixin, unittest.TestCase):
    def test_unknown_account_blocks_everything(self):
        self.set_config()
        budget = runner.Budget.from_account(None, grabs_last_24h=0)
        self.assertEqual(budget.blocker(), "MAM account stats unavailable")
        incomplete = runner.Budget.from_account({"wedges": 3}, grabs_last_24h=0)
        self.assertEqual(incomplete.blocker(), "MAM account stats incomplete")

    def test_unsat_reserve_and_daily_cap(self):
        self.set_config(WATCHES_UNSAT_RESERVE=10, WATCHES_DAILY_GRAB_LIMIT=10)
        self.assertEqual(runner.Budget.from_account(account(unsat_count=38), grabs_last_24h=0).unsat_slots, 2)
        self.assertEqual(
            runner.Budget.from_account(account(unsat_count=45), grabs_last_24h=0).blocker(),
            "unsatisfied-torrent reserve reached",
        )
        self.assertEqual(
            runner.Budget.from_account(account(), grabs_last_24h=10).blocker(), "daily grab limit reached"
        )

    def test_wedges_need_the_setting_and_respect_the_reserve(self):
        self.set_config(WATCHES_ALLOW_WEDGES=False)
        self.assertEqual(runner.Budget.from_account(account(wedges=10), grabs_last_24h=0).wedges, 0)
        self.set_config(WATCHES_ALLOW_WEDGES=True, WATCHES_WEDGE_RESERVE=8)
        self.assertEqual(runner.Budget.from_account(account(wedges=10), grabs_last_24h=0).wedges, 2)
        self.assertEqual(runner.Budget.from_account(account(wedges=3), grabs_last_24h=0).wedges, 0)

    def test_ratio_allowance_keeps_ratio_above_floor(self):
        self.set_config(WATCHES_MIN_RATIO=2.0)
        budget = runner.Budget.from_account(account(uploaded_gb=100, downloaded_gb=40), grabs_last_24h=0)
        self.assertAlmostEqual(budget.ratio_allowance_gb, 10.0)
        # A floor below 1 is treated as 1 (never spend more than the buffer).
        self.set_config(WATCHES_MIN_RATIO=0.5)
        budget = runner.Budget.from_account(account(uploaded_gb=100, downloaded_gb=40), grabs_last_24h=0)
        self.assertAlmostEqual(budget.ratio_allowance_gb, 60.0)

    def test_payment_policies(self):
        self.set_config(WATCHES_ALLOW_WEDGES=True, WATCHES_WEDGE_RESERVE=0)
        budget = runner.Budget.from_account(account(wedges=1, uploaded_gb=100, downloaded_gb=40), grabs_last_24h=0)
        free, paid, huge = mam_item(1, free=1), mam_item(2, size_gb=1), mam_item(3, size_gb=50)
        for item in (free, paid, huge):
            runner.annotate_cost(item, vip_active=False)
        self.assertEqual(budget.payment_for(free, "free"), "free")
        self.assertIsNone(budget.payment_for(paid, "free"))
        self.assertEqual(budget.payment_for(paid, "wedge"), "wedge")
        budget.wedges = 0
        self.assertIsNone(budget.payment_for(paid, "wedge"))
        self.assertEqual(budget.payment_for(paid, "ratio"), "ratio")
        self.assertIsNone(budget.payment_for(huge, "ratio"))

    def test_vip_freeleech_only_counts_while_vip(self):
        item = mam_item(1, fl_vip=1)
        runner.annotate_cost(item, vip_active=False)
        self.assertFalse(item["is_free"])
        runner.annotate_cost(item, vip_active=True)
        self.assertTrue(item["is_free"])
        self.assertEqual(item["vip_freeleech"], 1)


class SearchParamsTests(unittest.TestCase):
    def test_newest_first_with_selected_fields(self):
        params = runner.build_search_params({
            "query": '"T J Klune"', "search_in": ["author", "series"], "language_ids": ["1"], "main_cat": ["13"],
        })
        self.assertEqual(params["tor[sortType]"], "dateDesc")
        self.assertEqual(params["tor[text]"], '"T J Klune"')
        self.assertEqual(params["tor[srchIn][author]"], "true")
        self.assertEqual(params["tor[srchIn][series]"], "true")
        self.assertNotIn("tor[srchIn][title]", params)
        self.assertEqual(params["tor[browse_lang][]"], ["1"])
        self.assertEqual(params["tor[main_cat][]"], ["13"])
        self.assertEqual(params["perpage"], runner.RESULTS_PER_SEARCH)


class FakeMam:
    def __init__(self, results=None, account_data=None):
        self.results = results or {}
        self.account_data = account(**(account_data or {})) if account_data is not False else None
        self.added = []
        self.add_result = None
        self.search_error = None
        self.client = set()
        self.notified = []

    async def search(self, params):
        if self.search_error:
            raise self.search_error
        return [dict(item) for item in self.results.get(params["tor[text]"], [])]

    async def account(self):
        return self.account_data

    async def client_mids(self):
        return set(self.client)

    async def add(self, item, *, category, use_wedge):
        self.added.append((str(item["id"]), use_wedge, category))
        return self.add_result or {"message": "added"}

    async def notify(self, event, success, **details):
        self.notified.append((event, details.get("mid")))


class HttpError(Exception):
    def __init__(self, status_code):
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


class RunnerTests(ConfigMixin, StoreTestCase, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.mam = FakeMam()
        runner.configure(search=self.mam.search, account=self.mam.account, client_mids=self.mam.client_mids,
                         add=self.mam.add, notify=self.mam.notify)
        self.set_config()
        self._pause = runner.PAUSE_BETWEEN_WATCHES_SECONDS
        runner.PAUSE_BETWEEN_WATCHES_SECONDS = 0

    def tearDown(self):
        runner.PAUSE_BETWEEN_WATCHES_SECONDS = self._pause
        super().tearDown()

    def make_watch(self, **fields):
        data = {"name": "W", "query": "q"}
        data.update(fields)
        return store.create_watch(data)

    async def run_now(self, watch_id, dry_run=False):
        return await runner.run_single(store.get_watch(watch_id), dry_run=dry_run)

    async def test_first_run_only_records_existing_then_grabs_new(self):
        self.mam.results["q"] = [mam_item(1, free=1), mam_item(2, free=1)]
        watch = self.make_watch()

        first = await self.run_now(watch["id"])
        self.assertTrue(first["baseline_run"])
        self.assertEqual(first["baseline"], 2)
        self.assertEqual(self.mam.added, [])
        self.assertEqual(store.seen_ids(watch["id"]), {"1", "2"})
        self.assertEqual(store.events_for(watch["id"])[0]["kind"], "baseline")

        self.mam.results["q"].insert(0, mam_item(3, free=1))
        second = await self.run_now(watch["id"])
        self.assertEqual(second["grabbed"], 1)
        self.assertEqual(self.mam.added, [("3", False, "")])
        self.assertEqual(self.mam.notified, [("watch_grabbed", "3")])
        self.assertEqual(store.grabs_since(24), 1)

    async def test_failed_first_search_keeps_baseline_pending(self):
        watch = self.make_watch()
        self.mam.search_error = RuntimeError("timeout")
        summary = await self.run_now(watch["id"])
        self.assertEqual(summary["errors"], 1)
        self.mam.search_error = None
        self.mam.results["q"] = [mam_item(1, free=1)]
        summary = await self.run_now(watch["id"])
        self.assertEqual(summary["baseline"], 1)
        self.assertEqual(self.mam.added, [])

    async def test_grab_existing_respects_cap_and_retries_the_rest(self):
        self.mam.results["q"] = [mam_item(i, free=1) for i in (1, 2, 3)]
        watch = self.make_watch(grab_existing=True, max_grabs_per_run=2)
        summary = await self.run_now(watch["id"])
        self.assertEqual([a[0] for a in self.mam.added], ["1", "2"])
        self.assertEqual(summary["deferred"], 1)
        self.assertNotIn("3", store.seen_ids(watch["id"]))
        await self.run_now(watch["id"])
        self.assertEqual([a[0] for a in self.mam.added], ["1", "2", "3"])

    async def test_free_policy_waits_on_paid_torrents(self):
        self.mam.results["q"] = [mam_item(1), mam_item(2, free=1)]
        watch = self.make_watch(grab_existing=True)
        summary = await self.run_now(watch["id"])
        self.assertEqual(self.mam.added, [("2", False, "")])
        waiting = [i for i in summary["items"] if i["id"] == "1"][0]
        self.assertEqual(waiting["action"], "waiting")
        self.assertIn("not freeleech", waiting["detail"])
        self.assertNotIn("1", store.seen_ids(watch["id"]))

    async def test_wedge_policy_spends_owned_wedges_only_when_allowed(self):
        self.mam.results["q"] = [mam_item(1), mam_item(2), mam_item(3)]
        watch = self.make_watch(grab_existing=True, cost="wedge", max_grabs_per_run=10)

        await self.run_now(watch["id"])
        self.assertEqual(self.mam.added, [], "wedges are off by default")

        self.set_config(WATCHES_ALLOW_WEDGES=True, WATCHES_WEDGE_RESERVE=8)
        self.mam.account_data["wedges"] = 10
        await self.run_now(watch["id"])
        self.assertEqual(self.mam.added, [("1", True, ""), ("2", True, "")])

    async def test_already_owned_torrents_are_skipped(self):
        self.mam.results["q"] = [mam_item(1, free=1, my_snatched=1), mam_item(2, free=1), mam_item(3, free=1)]
        self.mam.client = {"2"}
        other = self.make_watch(name="Other", query="other")
        store.mark_seen(other["id"], [("3", "Book")], grabbed=True)
        watch = self.make_watch(grab_existing=True, max_grabs_per_run=10)
        summary = await self.run_now(watch["id"])
        self.assertEqual(self.mam.added, [])
        self.assertEqual({i["action"] for i in summary["items"]}, {"have"})
        self.assertEqual(store.seen_ids(watch["id"]), {"1", "2", "3"})

    async def test_no_account_stats_means_no_grabs(self):
        self.mam.account_data = None
        self.mam.results["q"] = [mam_item(1, free=1)]
        watch = self.make_watch(grab_existing=True)
        summary = await self.run_now(watch["id"])
        self.assertEqual(self.mam.added, [])
        self.assertEqual(summary["items"][0]["detail"], "MAM account stats unavailable")

    async def test_daily_limit_and_unsat_reserve_stop_grabs(self):
        self.set_config(WATCHES_DAILY_GRAB_LIMIT=1)
        self.mam.results["q"] = [mam_item(1, free=1), mam_item(2, free=1)]
        watch = self.make_watch(grab_existing=True, max_grabs_per_run=10)
        await self.run_now(watch["id"])
        self.assertEqual(len(self.mam.added), 1)
        await self.run_now(watch["id"])
        self.assertEqual(len(self.mam.added), 1)

        self.set_config(WATCHES_DAILY_GRAB_LIMIT=10, WATCHES_UNSAT_RESERVE=10)
        self.mam.account_data["unsat_count"] = 40
        summary = await self.run_now(watch["id"])
        self.assertEqual(len(self.mam.added), 1)
        self.assertIn("unsatisfied", summary["items"][1]["detail"])

    async def test_ratio_policy_stays_above_floor(self):
        self.set_config(WATCHES_MIN_RATIO=2.0)
        self.mam.account_data.update(uploaded_gb=100, downloaded_gb=48)  # 2 GB of headroom
        self.mam.results["q"] = [mam_item(1, size_gb=1.5), mam_item(2, size_gb=1.0)]
        watch = self.make_watch(grab_existing=True, cost="ratio", max_grabs_per_run=10)
        await self.run_now(watch["id"])
        self.assertEqual(self.mam.added, [("1", False, "")])

    async def test_dry_run_writes_nothing(self):
        self.mam.results["q"] = [mam_item(1, free=1)]
        watch = self.make_watch(grab_existing=True)
        summary = await self.run_now(watch["id"], dry_run=True)
        self.assertEqual(summary["grabbed"], 1)
        self.assertEqual(summary["items"][0]["detail"], "would grab (free)")
        self.assertEqual(self.mam.added, [])
        self.assertEqual(store.seen_ids(watch["id"]), set())
        self.assertIsNone(store.get_watch(watch["id"])["last_run_at"])

    async def test_failed_add_is_retried_later(self):
        self.mam.results["q"] = [mam_item(1, free=1)]
        self.mam.add_result = {"status": "insufficient_buffer", "message": "Insufficient buffer"}
        watch = self.make_watch(grab_existing=True)
        summary = await self.run_now(watch["id"])
        self.assertEqual(summary["errors"], 1)
        self.assertNotIn("1", store.seen_ids(watch["id"]))
        self.mam.add_result = None
        await self.run_now(watch["id"])
        self.assertIn("1", store.seen_ids(watch["id"]))
        self.assertEqual(store.grabs_since(24), 1)

    async def test_failed_adds_count_toward_the_per_run_cap(self):
        self.mam.results["q"] = [mam_item(i, free=1) for i in (1, 2, 3)]
        self.mam.add_result = {"error": "client offline"}
        watch = self.make_watch(grab_existing=True, max_grabs_per_run=1)
        summary = await self.run_now(watch["id"])
        self.assertEqual(len(self.mam.added), 1)
        self.assertEqual(summary["errors"], 1)
        self.assertEqual(summary["deferred"], 2)

    async def test_title_regex_filters_results(self):
        self.mam.results["q"] = [mam_item(1, "Series Book 5", free=1), mam_item(2, "Box Set", free=1)]
        watch = self.make_watch(grab_existing=True, title_regex=r"book \d+")
        summary = await self.run_now(watch["id"])
        self.assertEqual([i["id"] for i in summary["items"]], ["1"])
        self.assertEqual(self.mam.added, [("1", False, "")])

    async def test_tick_runs_due_watches_and_stops_when_mam_refuses(self):
        a = self.make_watch(name="A", query="a")
        b = self.make_watch(name="B", query="b")
        self.make_watch(name="Off", query="c", enabled=False)
        self.mam.results.update(a=[mam_item(1, free=1)], b=[mam_item(2, free=1)])

        summaries = await runner.run_all_watches()
        self.assertEqual(sorted(s["name"] for s in summaries), ["A", "B"])
        self.assertEqual(await runner.run_all_watches(), [], "nothing is due right after a run")

        store.update_watch(a["id"], {"query": "a2"})  # re-baselines, and is still not due
        with store._lock:
            store._get_conn().execute("UPDATE watches SET last_run_at = NULL")
        self.mam.search_error = HttpError(406)
        summaries = await runner.run_all_watches()
        self.assertEqual(summaries, [])
        self.assertEqual(len(store.events_for(a["id"])), 2, "first watch logged the refusal; second never ran")
        self.assertEqual(len(store.events_for(b["id"])), 1)

    async def test_tick_does_nothing_when_disabled(self):
        self.set_config(WATCHES_ENABLED=False)
        self.make_watch()
        self.assertEqual(await runner.run_all_watches(), [])


if __name__ == "__main__":
    unittest.main()
