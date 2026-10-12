"""Unit tests for the RecommendationLatch state machine and arrival preemption."""

import datetime
import os
import sys
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(TESTS_DIR))
sys.path.insert(0, TESTS_DIR)

import ha_mock  # noqa: F401

from custom_components.passable_climate.engines.latch import (
    CLOSE,
    OPEN,
    SAFETY_SCENARIOS,
    RecommendationLatch,
)


class TestRecommendationLatch(unittest.TestCase):
    """Tests for the two-sided anti-flap dwell latch and safety overrides."""

    def setUp(self):
        self.t0 = datetime.datetime(2026, 10, 12, 12, 0, 0, tzinfo=datetime.timezone.utc)
        self.min_open = datetime.timedelta(minutes=45)
        self.min_closed = datetime.timedelta(minutes=20)

    def test_initial_state_assignment(self):
        """First step should adopt the raw state without dwell hold."""
        latch = RecommendationLatch()
        final, held = latch.step(
            OPEN, self.t0, min_open=self.min_open, min_closed=self.min_closed
        )
        self.assertEqual(final, OPEN)
        self.assertFalse(held)
        self.assertEqual(latch.state, OPEN)
        self.assertEqual(latch.since, self.t0)

    def test_open_dwell_holds_against_normal_close(self):
        """Open state should be held for at least min_open during standard transitions."""
        latch = RecommendationLatch()
        latch.step(OPEN, self.t0, min_open=self.min_open, min_closed=self.min_closed)

        # 15 minutes later, raw recommends CLOSE (e.g. minor temp change)
        t_15m = self.t0 + datetime.timedelta(minutes=15)
        final, held = latch.step(
            CLOSE, t_15m, min_open=self.min_open, min_closed=self.min_closed, safety_close=False
        )
        self.assertEqual(final, OPEN)
        self.assertTrue(held)

        # 46 minutes later, min_open expired -> transitions to CLOSE
        t_46m = self.t0 + datetime.timedelta(minutes=46)
        final, held = latch.step(
            CLOSE, t_46m, min_open=self.min_open, min_closed=self.min_closed, safety_close=False
        )
        self.assertEqual(final, CLOSE)
        self.assertFalse(held)

    def test_safety_close_preempts_open_dwell_immediately(self):
        """Safety critical reasons (e.g., rain, high wind, latent breach) must bypass Open dwell."""
        latch = RecommendationLatch()
        latch.step(OPEN, self.t0, min_open=self.min_open, min_closed=self.min_closed)

        # Only 5 minutes in, sudden rain or dew point spike triggers safety_close=True
        t_5m = self.t0 + datetime.timedelta(minutes=5)
        final, held = latch.step(
            CLOSE,
            t_5m,
            min_open=self.min_open,
            min_closed=self.min_closed,
            safety_close=True,
            scenario="latent_breach",
        )
        self.assertEqual(final, CLOSE)
        self.assertFalse(held)
        self.assertEqual(latch.state, CLOSE)

    def test_closed_dwell_holds_against_rapid_reopen(self):
        """Once closed, windows must stay closed for at least min_closed to prevent alert flapping."""
        latch = RecommendationLatch(state=CLOSE, since=self.t0, last_scenario="standard")

        # 10 minutes later, outdoor air briefly dips favorable -> should hold CLOSE
        t_10m = self.t0 + datetime.timedelta(minutes=10)
        final, held = latch.step(
            OPEN, t_10m, min_open=self.min_open, min_closed=self.min_closed
        )
        self.assertEqual(final, CLOSE)
        self.assertTrue(held)

        # 21 minutes later -> transition allowed
        t_21m = self.t0 + datetime.timedelta(minutes=21)
        final, held = latch.step(
            OPEN, t_21m, min_open=self.min_open, min_closed=self.min_closed
        )
        self.assertEqual(final, OPEN)
        self.assertFalse(held)

    def test_arrival_home_preempts_closed_dwell_immediately(self):
        """When someone arrives home after being unoccupied, favorable air should immediately recommend OPEN."""
        # House was unoccupied, holding CLOSE
        latch = RecommendationLatch(state=CLOSE, since=self.t0, last_scenario="unoccupied_veto")

        # 5 minutes later, user arrives home and raw evaluation is OPEN
        t_5m = self.t0 + datetime.timedelta(minutes=5)
        final, held = latch.step(
            OPEN,
            t_5m,
            min_open=self.min_open,
            min_closed=self.min_closed,
            scenario="standard",
        )
        # Closed dwell should be preempted by arrival transition!
        self.assertEqual(final, OPEN)
        self.assertFalse(held)
        self.assertEqual(latch.state, OPEN)

    def test_serialization_and_restoration(self):
        """Latch state must serialize to JSON-compatible dictionary and restore cleanly."""
        latch = RecommendationLatch(state=OPEN, since=self.t0, last_scenario="overcooling")
        data = latch.as_dict()

        restored = RecommendationLatch.from_dict(data)
        self.assertEqual(restored.state, OPEN)
        self.assertEqual(restored.since, self.t0)
        self.assertEqual(restored.last_scenario, "overcooling")

        # Test restore with None / empty
        empty_latch = RecommendationLatch.from_dict(None)
        self.assertEqual(empty_latch.state, CLOSE)
        self.assertIsNone(empty_latch.since)


if __name__ == "__main__":
    unittest.main()
