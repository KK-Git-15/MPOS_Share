"""Phase 9b congestion control (CLAUDE.md §3.4.2). Fake clock: never sleeps."""

from __future__ import annotations

import pytest

from mpos.net.congestion import RttEstimator, RttSampler
from tests.helpers.fake_clock import FakeClock

# ============================================================
# RFC 6298 ESTIMATOR (9b-1)
# ============================================================


def test_initial_state() -> None:
    est = RttEstimator(min_rto=1.0, max_rto=10.0)

    assert est.srtt is None and est.rttvar is None
    assert est.rto == 5.0  # RFC 6298 recommends 1 s; the spec starts at 5 s


def test_first_sample() -> None:
    est = RttEstimator(min_rto=0.01, max_rto=10.0)
    est.on_sample(0.2)

    assert est.srtt == pytest.approx(0.2)
    assert est.rttvar == pytest.approx(0.1)
    assert est.rto == pytest.approx(0.2 + 4 * 0.1)


def test_rfc6298_arithmetic_table() -> None:
    est = RttEstimator(min_rto=0.001, max_rto=100.0)
    srtt, rttvar = None, None
    for r in (0.100, 0.120, 0.080, 0.300, 0.110, 0.105):
        est.on_sample(r)
        if srtt is None:
            srtt, rttvar = r, r / 2
        else:
            assert rttvar is not None
            rttvar = 0.75 * rttvar + 0.25 * abs(srtt - r)  # uses the old srtt
            srtt = 0.875 * srtt + 0.125 * r
        assert est.srtt == pytest.approx(srtt)
        assert est.rttvar == pytest.approx(rttvar)
        assert est.rto == pytest.approx(srtt + 4 * rttvar)


def test_rto_is_clamped() -> None:
    low = RttEstimator(min_rto=1.0, max_rto=10.0)
    low.on_sample(0.01)
    assert low.rto == 1.0

    high = RttEstimator(min_rto=1.0, max_rto=10.0)
    high.on_sample(8.0)
    assert high.rto == 10.0

    assert RttEstimator(min_rto=0.5, max_rto=2.0).rto == 2.0  # initial 5 s capped


def test_timeouts_back_off_until_the_next_sample() -> None:
    est = RttEstimator(min_rto=0.1, max_rto=8.0)
    est.on_sample(0.25)  # rto = 0.25 + 4 * 0.125 = 0.75
    assert est.rto == pytest.approx(0.75)

    assert est.on_timeout() == pytest.approx(1.5)
    assert est.on_timeout() == pytest.approx(3.0)
    assert est.on_timeout() == pytest.approx(6.0)
    assert est.on_timeout() == pytest.approx(8.0)  # capped at max_rto
    assert est.on_timeout() == pytest.approx(8.0)

    est.on_sample(0.25)  # a valid sample resets the backoff
    assert est.rto < 1.0


@pytest.mark.parametrize("bad", [0.0, -1.0, float("nan"), float("inf")])
def test_invalid_samples_are_ignored(bad: float) -> None:
    est = RttEstimator(min_rto=1.0, max_rto=10.0)
    est.on_sample(bad)
    assert est.srtt is None


def test_config_validation() -> None:
    with pytest.raises(ValueError):
        RttEstimator(min_rto=0, max_rto=1)
    with pytest.raises(ValueError):
        RttEstimator(min_rto=2, max_rto=1)


# ============================================================
# SAMPLER: Karn's rule and the small-shard filter (9b-1)
# ============================================================


def test_sample_is_send_to_first_ack() -> None:
    clock = FakeClock()
    sampler = RttSampler(clock, nominal_size=1000)

    sampler.sent(("s", 0), size=1000)
    clock.advance(0.040)
    assert sampler.acked(("s", 0)) == pytest.approx(0.040)
    assert sampler.acked(("s", 0)) is None  # only the first ACK counts


def test_karns_rule_no_sample_after_a_retransmit() -> None:
    clock = FakeClock()
    sampler = RttSampler(clock, nominal_size=1000)

    sampler.sent(("s", 1), size=1000)
    clock.advance(1.0)
    sampler.sent(("s", 1), size=1000)  # retransmitted
    clock.advance(0.040)

    assert sampler.retransmitted(("s", 1))
    assert sampler.acked(("s", 1)) is None  # ambiguous: which send was ACKed?


def test_small_shards_give_no_sample() -> None:
    """The short last segment's shards would drag the baseline down."""
    clock = FakeClock()
    sampler = RttSampler(clock, nominal_size=1000)

    sampler.sent(("s", 2), size=499)
    sampler.sent(("s", 3), size=500)
    clock.advance(0.05)

    assert sampler.acked(("s", 2)) is None
    assert sampler.acked(("s", 3)) == pytest.approx(0.05)


def test_unknown_or_forgotten_shards() -> None:
    clock = FakeClock()
    sampler = RttSampler(clock, nominal_size=1000)
    assert sampler.acked(("never", 0)) is None

    sampler.sent(("s", 4), size=1000)
    sampler.forget(("s", 4))  # e.g. cancelled once the segment had K ACKs
    assert sampler.acked(("s", 4)) is None
    assert len(sampler) == 0


# ============================================================
# BASELINE (9b-2)
# ============================================================

from mpos.net.congestion import (  # noqa: E402
    BaseRtt,
    CongestionConfig,
    VegasController,
)


def test_baseline_is_per_first_hop_minimum() -> None:
    clock = FakeClock()
    base = BaseRtt(clock, window_s=30)
    for r in (0.050, 0.040, 0.045):
        base.add("r1", r)
    for r in (0.120, 0.110, 0.130):
        base.add("r2", r)

    assert base.base("r1") == pytest.approx(0.040)
    assert base.base("r2") == pytest.approx(0.110)  # a slower path, not a queue


def test_baseline_falls_back_to_global_p10_with_few_samples() -> None:
    clock = FakeClock()
    base = BaseRtt(clock, window_s=30)
    for i in range(10):
        base.add("busy", 0.100 + i * 0.010)  # 100..190 ms
    base.add("new", 0.500)  # only 1 sample on this hop

    # Global 10th percentile, nearest rank: sorted[floor(0.1 * (11 - 1))]
    assert base.base("new") == pytest.approx(0.110)
    assert base.base("never-seen") == pytest.approx(0.110)
    assert BaseRtt(clock, window_s=30).base("x") is None


def test_baseline_window_expires_old_samples() -> None:
    clock = FakeClock()
    base = BaseRtt(clock, window_s=30)
    for _ in range(3):
        base.add("r1", 0.020)
    clock.advance(31)
    for _ in range(3):
        base.add("r1", 0.060)

    assert base.base("r1") == pytest.approx(0.060)  # the 20 ms samples aged out


# ============================================================
# VEGAS CONTROLLER (9b-2)
# ============================================================

N = 6


def _ctl(
    clock: FakeClock | None = None, **overrides: float | int
) -> tuple[VegasController, FakeClock]:
    clock = clock or FakeClock()
    return VegasController(
        CongestionConfig(**overrides), clock, shards_per_segment=N
    ), clock


def _round(
    ctl: VegasController,
    clock: FakeClock,
    rtt: float,
    *,
    hop: str = "r1",
    samples: int | None = None,
    inflight: int | None = None,
) -> None:
    """One full round: send a batch, then ACK each with ``rtt``."""
    count = samples or int(ctl.cwnd)
    keys = [object() for _ in range(count)]
    for key in keys:
        ctl.on_send(key)
    clock.advance(rtt)
    for key in keys:
        ctl.on_ack(
            key,
            rtt,
            first_hop=hop,
            inflight=inflight if inflight is not None else int(ctl.cwnd),
        )


def _to_avoidance(ctl: VegasController, clock: FakeClock, cwnd: int) -> None:
    """Teach a 40 ms baseline, then force congestion avoidance at ``cwnd``."""
    for _ in range(3):
        ctl.baseline.add("r1", 0.040)
    ctl.force(cwnd=cwnd, mode="avoidance")


def test_initial_window_and_bounds() -> None:
    ctl, _ = _ctl()

    assert ctl.cwnd == N  # init_cwnd = N
    assert ctl.min_cwnd == 2 and ctl.max_cwnd == 16 * N
    assert ctl.mode == "slow_start"
    assert ctl.can_send(N - 1) and not ctl.can_send(N)


@pytest.mark.parametrize(
    ("rtt_ms", "change"),
    [(41, +1), (44, 0), (60, -1)],  # the spec's worked example
)
def test_avoidance_grow_hold_shrink(rtt_ms: int, change: int) -> None:
    ctl, clock = _ctl()
    _to_avoidance(ctl, clock, cwnd=24)

    _round(ctl, clock, rtt_ms / 1000, samples=24)

    assert ctl.cwnd == 24 + change
    expected_q = 24 * (1 - 0.040 / (rtt_ms / 1000))
    assert ctl.queue_est == pytest.approx(expected_q)


def test_slow_start_grows_per_sample_and_exits_at_gamma() -> None:
    ctl, clock = _ctl()
    for _ in range(3):
        ctl.baseline.add("r1", 0.040)

    _round(ctl, clock, 0.040)  # no queue: +1 per sample, so doubles
    assert ctl.cwnd == 2 * N and ctl.mode == "slow_start"

    _round(ctl, clock, 0.080)  # queue = cwnd/2 > gamma: leave slow start
    assert ctl.mode == "avoidance"
    assert ctl.ssthresh == ctl.cwnd


def test_no_growth_when_app_limited() -> None:
    ctl, clock = _ctl()
    _to_avoidance(ctl, clock, cwnd=20)

    _round(ctl, clock, 0.040, samples=5, inflight=3)  # far below cwnd - 1
    assert ctl.cwnd == 20

    ctl.force(cwnd=10, mode="slow_start")
    _round(ctl, clock, 0.040, samples=5, inflight=2)
    assert ctl.cwnd == 10


def test_window_bounds_hold() -> None:
    ctl, clock = _ctl()
    _to_avoidance(ctl, clock, cwnd=3)
    burst = ["r1", "r2", "r3"]
    assert ctl.on_timeout(burst, inflight=3, rto=1.0)
    assert ctl.cwnd == ctl.min_cwnd  # max(2, 3 / 2)
    clock.advance(2)
    ctl.on_timeout(burst, inflight=2, rto=1.0)
    assert ctl.cwnd == ctl.min_cwnd  # never below

    ctl.force(cwnd=ctl.max_cwnd, mode="slow_start")
    _round(ctl, clock, 0.040, samples=10)
    assert ctl.cwnd == ctl.max_cwnd


def test_single_path_timeouts_leave_the_window_alone() -> None:
    ctl, clock = _ctl()
    _to_avoidance(ctl, clock, cwnd=20)

    assert ctl.on_timeout(["r1"] * 10, inflight=20) is False  # one first hop
    assert ctl.cwnd == 20


def test_burst_timeout_halves_once_per_rto() -> None:
    ctl, clock = _ctl()
    _to_avoidance(ctl, clock, cwnd=20)

    burst = ["r1", "r2", "r3", "r1", "r2", "r3", "r1", "r2", "r3", "r1"]
    assert ctl.on_timeout(burst, inflight=20, rto=1.0) is True
    assert ctl.cwnd == 10 and ctl.mode == "avoidance" and ctl.ssthresh == 10

    assert ctl.on_timeout(burst, inflight=10, rto=1.0) is False  # within one rto
    assert ctl.cwnd == 10
    clock.advance(1.1)
    assert ctl.on_timeout(burst, inflight=10, rto=1.0) is True
    assert ctl.cwnd == 5


def test_small_bursts_are_not_bursts() -> None:
    ctl, clock = _ctl()
    _to_avoidance(ctl, clock, cwnd=20)

    assert ctl.on_timeout(["r1", "r2"], inflight=20, rto=1.0) is False  # < 3
    assert ctl.on_timeout(["r1", "r2", "r3"], inflight=20, rto=1.0) is False  # < 50%
    assert ctl.cwnd == 20


def test_a_lost_round_marker_does_not_freeze_rounds() -> None:
    ctl, clock = _ctl()
    _to_avoidance(ctl, clock, cwnd=20)
    marker = object()
    ctl.on_send(marker)
    ctl.on_cancel(marker)  # e.g. its segment completed without it

    _round(ctl, clock, 0.041, samples=20)
    assert ctl.cwnd == 21


def test_property_cwnd_stays_in_bounds_and_finite() -> None:
    import math
    import random

    rng = random.Random(9)
    for _ in range(30):
        ctl, clock = _ctl()
        keys: list[object] = []
        for _ in range(500):
            action = rng.random()
            if action < 0.4 or not keys:
                key = object()
                keys.append(key)
                ctl.on_send(key)
            elif action < 0.85:
                key = keys.pop(rng.randrange(len(keys)))
                clock.advance(rng.uniform(0, 0.3))
                sample = rng.choice([None, rng.uniform(0.001, 2.0)])
                ctl.on_ack(
                    key, sample, first_hop=f"r{rng.randrange(5)}", inflight=len(keys)
                )
            elif action < 0.95:
                hops = [f"r{rng.randrange(5)}" for _ in range(rng.randrange(1, 8))]
                ctl.on_timeout(hops, inflight=len(keys), rto=rng.uniform(0.1, 5))
            else:
                ctl.on_cancel(keys.pop(rng.randrange(len(keys))))
            assert ctl.min_cwnd <= ctl.cwnd <= ctl.max_cwnd
            assert math.isfinite(ctl.cwnd) and math.isfinite(ctl.queue_est)


def test_late_acks_from_an_old_round_do_not_dilute_the_next_round() -> None:
    """Regression: stragglers from round 1 hid round 2's doubled RTT."""
    ctl, clock = _ctl()
    _to_avoidance(ctl, clock, cwnd=20)
    old = [object() for _ in range(10)]
    for key in old:
        ctl.on_send(key)
    clock.advance(0.040)
    ctl.on_ack(old[0], 0.040, first_hop="r1", inflight=20)  # ends round 1
    after_round_1 = ctl.cwnd  # no queue + full window: grew to 21

    new = [object() for _ in range(5)]
    for key in new:
        ctl.on_send(key)
    for key in old[1:]:  # stragglers from round 1, all fast
        ctl.on_ack(key, 0.040, first_hop="r1", inflight=20)
    clock.advance(0.080)
    ctl.on_ack(new[0], 0.080, first_hop="r1", inflight=20)  # ends round 2

    # Only round 2's own shard counts: ratio 2, not the stragglers' 1
    assert after_round_1 == 21
    assert ctl.queue_est == pytest.approx(after_round_1 * (1 - 0.040 / 0.080))
    assert ctl.cwnd == after_round_1 - 1  # queue 10.5 > beta: shrink


def test_slow_start_exits_mid_round_once_the_queue_shows() -> None:
    """Waiting for the round to end lets the window overshoot."""
    ctl, clock = _ctl()
    for _ in range(3):
        ctl.baseline.add("r1", 0.040)
    ctl.force(cwnd=30, mode="slow_start")
    keys = [object() for _ in range(30)]
    for key in keys:
        ctl.on_send(key)
    clock.advance(0.1)
    ctl.on_ack(keys[1], 0.100, first_hop="r1", inflight=30)  # not the marker
    ctl.on_ack(keys[2], 0.100, first_hop="r1", inflight=30)
    assert ctl.mode == "slow_start"  # only 2 samples so far
    ctl.on_ack(keys[3], 0.100, first_hop="r1", inflight=30)

    assert ctl.mode == "avoidance"  # queue 30 x (1 - 0.4) = 18 > gamma
    assert ctl.cwnd == 32  # grew on the first two samples only
