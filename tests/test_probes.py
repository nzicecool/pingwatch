"""Tests for probe result calculations."""

from pingwatch.probes import ProbeResult


class TestProbeResult:
    def test_median_odd_count(self):
        r = ProbeResult(target="test", probe_name="icmp", latencies=[10, 20, 30])
        r.compute_stats()
        assert r.median == 20.0
        assert r.loss_pct == 0.0
        assert r.sent == 3
        assert r.lost == 0

    def test_median_even_count(self):
        r = ProbeResult(target="test", probe_name="icmp", latencies=[10, 20, 30, 40])
        r.compute_stats()
        assert r.median == 25.0

    def test_loss_calculation(self):
        r = ProbeResult(target="test", probe_name="icmp", latencies=[10, None, 30, None, 50])
        r.compute_stats()
        assert r.loss_pct == 40.0
        assert r.sent == 5
        assert r.lost == 2
        assert r.received == 3

    def test_all_lost(self):
        r = ProbeResult(target="test", probe_name="icmp", latencies=[None, None, None])
        r.compute_stats()
        assert r.median is None
        assert r.avg is None
        assert r.loss_pct == 100.0
        assert r.jitter == 0.0

    def test_empty_latencies(self):
        r = ProbeResult(target="test", probe_name="icmp", latencies=[])
        r.compute_stats()
        assert r.median is None
        assert r.loss_pct == 0.0

    def test_min_max_avg(self):
        r = ProbeResult(target="test", probe_name="icmp", latencies=[5, 10, 15, 20])
        assert r.min == 5.0
        assert r.max == 20.0
        assert r.avg == 12.5

    def test_jitter(self):
        r = ProbeResult(target="test", probe_name="icmp", latencies=[10, 10, 10])
        r.compute_stats()
        assert r.jitter == 0.0

    def test_jitter_with_variation(self):
        r = ProbeResult(target="test", probe_name="icmp", latencies=[5, 10, 15, 20])
        r.compute_stats()
        assert r.jitter > 0
