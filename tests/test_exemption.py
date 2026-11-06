import json
import unittest
from unittest.mock import patch

from tests._bootstrap import *  # noqa: F401,F403
from tests import _bootstrap as B

m = B.m
FEED = "https://www.githubstatus.com/api/v2/incidents.json"


def incident(start, end, impact="major", component="Actions", iid="inc1", started_key="started_at", tz="Z"):
    def fmt(epoch):
        import datetime
        d = datetime.datetime.fromtimestamp(epoch, B.UTC)
        if tz == "Z":
            return d.strftime("%Y-%m-%dT%H:%M:%S.123Z")
        sign = 1 if tz[0] == "+" else -1
        off = sign * (int(tz[1:3]) * 3600 + int(tz[4:6]) * 60)
        local = datetime.datetime.fromtimestamp(epoch + off, B.UTC)
        return local.strftime("%Y-%m-%dT%H:%M:%S.123") + tz

    return {
        "id": iid, "name": "Degraded " + component, "impact": impact, started_key: fmt(start),
        "created_at": fmt(start), "resolved_at": fmt(end) if end is not None else None,
        "components": [{"name": component}],
    }


def feed(*incidents):
    return {"page": {"id": "x"}, "incidents": list(incidents)}


def file_exemption(c, sla_id, body, caller=B.PROVIDER, epoch=0):
    web = B.Web(body)
    B.set_caller(caller)
    with patch.object(B.gl.nondet.web, "render", side_effect=web.render):
        return json.loads(c.file_exemption(sla_id, epoch))


class FeedCompute(unittest.TestCase):
    def run_feed(self, body, windows, component=""):
        web = B.Web(body)
        with patch.object(B.gl.nondet.web, "render", side_effect=web.render):
            return m._feed_compute({"url": FEED, "windows": windows, "component": component})

    def test_overlap_rules(self):
        w = [[1000, 2000]]
        self.assertEqual(self.run_feed(feed(incident(900, 1100)), w), "COVERED:inc1")
        self.assertEqual(self.run_feed(feed(incident(1500, 1600)), w), "COVERED:inc1")
        self.assertEqual(self.run_feed(feed(incident(1999, 5000)), w), "COVERED:inc1")
        self.assertEqual(self.run_feed(feed(incident(2000, 5000)), w), "COVERED:inc1")
        self.assertEqual(self.run_feed(feed(incident(1000, 1000)), w), "COVERED:inc1")
        self.assertEqual(self.run_feed(feed(incident(2001, 5000)), w), "NOT_COVERED")
        self.assertEqual(self.run_feed(feed(incident(0, 999)), w), "NOT_COVERED")

    def test_unresolved_incident_covers_everything_after_its_start(self):
        self.assertEqual(self.run_feed(feed(incident(500, None)), [[1000, 2000]]), "COVERED:inc1")

    def test_only_severe_incidents_count(self):
        for impact in ("none", "minor", "", "MAJORISH"):
            self.assertEqual(self.run_feed(feed(incident(900, 2100, impact=impact)), [[1000, 2000]]), "NOT_COVERED")
        for impact in ("major", "critical", "CRITICAL"):
            self.assertTrue(self.run_feed(feed(incident(900, 2100, impact=impact)), [[1000, 2000]]).startswith("COVERED"))

    def test_component_filter(self):
        body = feed(incident(900, 2100, component="Issues"))
        self.assertEqual(self.run_feed(body, [[1000, 2000]], component="Actions"), "NOT_COVERED")
        self.assertEqual(self.run_feed(body, [[1000, 2000]], component="issues"), "COVERED:inc1")
        self.assertEqual(self.run_feed(body, [[1000, 2000]], component=""), "COVERED:inc1")

    def test_every_window_must_be_covered(self):
        body = feed(incident(900, 1100, iid="a"))
        self.assertEqual(self.run_feed(body, [[1000, 1050], [5000, 5100]]), "NOT_COVERED")
        body = feed(incident(900, 1100, iid="a"), incident(4900, 5200, iid="b"))
        self.assertEqual(self.run_feed(body, [[1000, 1050], [5000, 5100]]), "COVERED:a,b")

    def test_timestamps_with_offsets_and_created_at_fallback(self):
        body = feed(incident(900, 2100, tz="-05:00"))
        self.assertEqual(self.run_feed(body, [[1000, 2000]]), "COVERED:inc1")
        body = feed(incident(900, 2100, started_key="started_x"))
        self.assertEqual(self.run_feed(body, [[1000, 2000]]), "COVERED:inc1")
        body = feed(incident(900, 2100, tz="+05:30"))
        self.assertEqual(self.run_feed(body, [[1000, 2000]]), "COVERED:inc1")
        self.assertEqual(self.run_feed(feed(incident(5000, 6000, tz="+05:30")), [[1000, 2000]]), "NOT_COVERED")

    def test_deterministic_pick_when_several_incidents_match(self):
        body = feed(incident(900, 2100, iid="zz"), incident(900, 2100, iid="aa"))
        self.assertEqual(self.run_feed(body, [[1000, 2000]]), "COVERED:aa")

    def test_garbage_is_skipped_not_trusted(self):
        bad = {"id": "bad", "impact": "major", "started_at": "yesterday", "created_at": "soon", "resolved_at": None}
        bad2 = incident(900, 2100, iid="bad2")
        bad2["resolved_at"] = "tomorrow"
        self.assertEqual(self.run_feed(feed(bad, bad2, "text", 5, None), [[1000, 2000]]), "NOT_COVERED")

    def test_unreadable_feeds(self):
        self.assertEqual(self.run_feed(RuntimeError("503"), [[1, 2]]), "UNREADABLE")
        self.assertEqual(self.run_feed("<html>maintenance</html>", [[1, 2]]), "UNREADABLE")
        self.assertEqual(self.run_feed({"incidents": "no"}, [[1, 2]]), "UNREADABLE")
        self.assertEqual(self.run_feed({"other": []}, [[1, 2]]), "UNREADABLE")

    def test_iso_parser(self):
        self.assertEqual(m._iso_epoch("1970-01-01T00:00:00Z"), 0)
        self.assertEqual(m._iso_epoch("1970-01-01T01:00:00+01:00"), 0)
        self.assertEqual(m._iso_epoch("1970-01-01T00:00:00.999999-02:00"), 7200)
        for bad in ("1970-01-01", "1970-13-01T00:00:00Z", "1970-01-01T00:00:00", 5, None, "1970-02-30T00:00:00Z", ""):
            self.assertIsNone(m._iso_epoch(bad))


class ExemptionFlow(unittest.TestCase):
    def setUp(self):
        self.c = B.make_contract()
        self.sla_id = B.active_sla(self.c, status_feed=FEED, feed_component="Actions")
        B.prove_outage(self.c, self.sla_id, epoch=0, caller=B.WATCHER)
        sla = B.get(self.c, self.sla_id)
        self.window = sla["epochs"][0]["windows"][0]
        B.CLOCK.set_epoch(sla["start_epoch"] + B.EPOCH_SECONDS + 5)

    def covered_feed(self):
        return feed(incident(self.window[0] - 60, self.window[1] + 60))

    def test_covered_epoch_is_excused_and_refunded_without_penalty(self):
        result = file_exemption(self.c, self.sla_id, self.covered_feed())
        self.assertEqual(result["exemption"], "covered")
        self.assertEqual(result["incidents"], "inc1")
        B.settle_time(self.c, self.sla_id, 0)
        sla = B.settle_due(self.c, self.sla_id)
        self.assertEqual(sla["epochs"][0]["result"], "excused")
        self.assertEqual((sla["excused"], sla["breaches"]), (1, 0))
        self.assertEqual(B.pending(self.c, B.CUSTOMER), B.FEE_PER_EPOCH)
        self.assertEqual(B.pending(self.c, B.WATCHER), 0)
        self.assertEqual(sla["bond_left"], str(B.BOND))
        self.assertTrue(B.accounting(self.c)["solvent"])

    def test_denied_exemption_is_final_and_the_breach_stands(self):
        result = file_exemption(self.c, self.sla_id, feed(incident(0, 10)))
        self.assertEqual(result["exemption"], "denied")
        with self.assertRaises(B.gl.vm.UserError):
            file_exemption(self.c, self.sla_id, self.covered_feed())
        B.settle_time(self.c, self.sla_id, 0)
        sla = B.settle_due(self.c, self.sla_id)
        self.assertEqual(sla["epochs"][0]["result"], "breach")

    def test_unreadable_feed_reverts_and_can_be_retried(self):
        before = dict(self.c.slas)
        with self.assertRaises(B.gl.vm.UserError) as ctx:
            file_exemption(self.c, self.sla_id, RuntimeError("503"))
        self.assertIn("could not be read", str(ctx.exception))
        self.assertEqual(dict(self.c.slas), before)
        self.assertEqual(file_exemption(self.c, self.sla_id, self.covered_feed())["exemption"], "covered")

    def test_only_the_provider(self):
        for who in (B.CUSTOMER, B.STRANGER, B.WATCHER):
            with self.assertRaises(B.gl.vm.UserError):
                file_exemption(self.c, self.sla_id, self.covered_feed(), caller=who)

    def test_needs_a_declared_feed(self):
        c = B.make_contract()
        sla_id = B.active_sla(c)
        B.prove_outage(c, sla_id, caller=B.WATCHER)
        B.CLOCK.set_epoch(B.get(c, sla_id)["start_epoch"] + B.EPOCH_SECONDS + 5)
        with self.assertRaises(B.gl.vm.UserError) as ctx:
            file_exemption(c, sla_id, feed())
        self.assertIn("no upstream", str(ctx.exception))

    def test_needs_an_ended_epoch_with_a_proven_outage(self):
        sla = B.get(self.c, self.sla_id)
        B.CLOCK.set_epoch(sla["start_epoch"] + 100)
        with self.assertRaises(B.gl.vm.UserError) as ctx:
            file_exemption(self.c, self.sla_id, self.covered_feed())
        self.assertIn("not ended", str(ctx.exception))
        B.CLOCK.set_epoch(sla["start_epoch"] + 2 * B.EPOCH_SECONDS + 5)
        with self.assertRaises(B.gl.vm.UserError) as ctx:
            file_exemption(self.c, self.sla_id, self.covered_feed(), epoch=1)
        self.assertIn("no proven outage", str(ctx.exception))
        with self.assertRaises(B.gl.vm.UserError):
            file_exemption(self.c, self.sla_id, self.covered_feed(), epoch=99)

    def test_window_closes_when_settlement_opens(self):
        B.settle_time(self.c, self.sla_id, 0)
        with self.assertRaises(B.gl.vm.UserError) as ctx:
            file_exemption(self.c, self.sla_id, self.covered_feed())
        self.assertIn("closed", str(ctx.exception))

    def test_cannot_file_for_an_already_settled_epoch(self):
        B.settle_time(self.c, self.sla_id, 0)
        B.settle_due(self.c, self.sla_id)
        B.CLOCK.set_epoch(B.get(self.c, self.sla_id)["start_epoch"] + B.EPOCH_SECONDS + 10)
        with self.assertRaises(B.gl.vm.UserError):
            file_exemption(self.c, self.sla_id, self.covered_feed())

    def test_cannot_decide_twice(self):
        file_exemption(self.c, self.sla_id, self.covered_feed())
        with self.assertRaises(B.gl.vm.UserError) as ctx:
            file_exemption(self.c, self.sla_id, self.covered_feed())
        self.assertIn("already decided", str(ctx.exception))

    def test_fetches_only_the_anchored_feed(self):
        web = B.Web(self.covered_feed())
        B.set_caller(B.PROVIDER)
        with patch.object(B.gl.nondet.web, "render", side_effect=web.render):
            self.c.file_exemption(self.sla_id, 0)
        self.assertEqual(web.urls, [FEED])


if __name__ == "__main__":
    unittest.main()
