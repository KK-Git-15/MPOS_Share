"""RTT-based congestion control (Phase 9b, CLAUDE.md §3.4.2).

Loss is not a usable congestion signal here: every hop runs over TCP, and a
missing ACK can mean congestion, a killed relay or an injected fault. So,
as in Tor 0.4.7 (Tor-Vegas, itself based on TCP Vegas), the window reacts
to **delay**.

This module is pure and synchronous: no asyncio, and every time comes from
an injected clock, so it can be unit-tested without sleeping.
"""

from __future__ import annotations

import math
import statistics
from collections import deque
from collections.abc import Callable, Hashable
from dataclasses import dataclass
from typing import Final

Clock = Callable[[], float]

INITIAL_RTO: Final = 5.0  # seconds, per the spec (RFC 6298 suggests 1 s)
SMALL_SHARD_FRACTION: Final = 0.5  # smaller shards give no RTT sample


# ============================================================
# RFC 6298 ESTIMATOR
# ============================================================


class RttEstimator:
    """Smoothed RTT, RTT variance and retransmission timeout (RFC 6298).

        first sample r:  srtt = r, rttvar = r / 2
        later samples:   rttvar = 3/4 rttvar + 1/4 |srtt - r|
                         srtt   = 7/8 srtt   + 1/8 r
        rto = clamp(srtt + 4 rttvar, min_rto, max_rto)

    Each timeout doubles ``rto`` (up to ``max_rto``) until the next valid
    sample recomputes it.
    """

    def __init__(self, *, min_rto: float, max_rto: float) -> None:
        if not 0 < min_rto <= max_rto:
            raise ValueError("Need 0 < min_rto <= max_rto")
        self.min_rto = min_rto
        self.max_rto = max_rto
        self.srtt: float | None = None
        self.rttvar: float | None = None
        self._rto = min(INITIAL_RTO, max_rto)

    @property
    def rto(self) -> float:
        return self._rto

    def on_sample(self, r: float) -> None:
        if not math.isfinite(r) or r <= 0:
            return
        if self.srtt is None or self.rttvar is None:
            self.srtt, self.rttvar = r, r / 2
        else:
            self.rttvar = 0.75 * self.rttvar + 0.25 * abs(self.srtt - r)
            self.srtt = 0.875 * self.srtt + 0.125 * r
        self._rto = min(max(self.srtt + 4 * self.rttvar, self.min_rto), self.max_rto)

    def on_timeout(self) -> float:
        """Back off: double ``rto`` up to ``max_rto``. Returns the new value."""
        self._rto = min(self._rto * 2, self.max_rto)
        return self._rto


# ============================================================
# SAMPLER: Karn's rule + small-shard filter
# ============================================================


@dataclass(slots=True)
class _Send:
    sent_at: float
    size: int
    retransmitted: bool = False


class RttSampler:
    """Turns shard sends and their first ACK into RTT samples.

    - ``sent`` is stamped right before the first-hop link write, so RS
      encoding, encryption and onion building are not counted.
    - Karn's rule: a shard sent more than once gives no sample, because
      the ACK cannot be matched to one of the sends.
    - Shards smaller than half the nominal size (the short last segment)
      give no sample: they would drag the baseline down.
    """

    def __init__(self, clock: Clock, *, nominal_size: int) -> None:
        self._clock = clock
        self._nominal = nominal_size
        self._sends: dict[Hashable, _Send] = {}

    def sent(self, key: Hashable, *, size: int) -> None:
        previous = self._sends.get(key)
        if previous is None:
            self._sends[key] = _Send(self._clock(), size)
        else:
            previous.retransmitted = True
            previous.sent_at = self._clock()

    def retransmitted(self, key: Hashable) -> bool:
        send = self._sends.get(key)
        return send is not None and send.retransmitted

    def acked(self, key: Hashable) -> float | None:
        """RTT sample for the first ACK of ``key``, or None if not valid."""
        send = self._sends.pop(key, None)
        if send is None or send.retransmitted:
            return None
        if send.size < SMALL_SHARD_FRACTION * self._nominal:
            return None
        return self._clock() - send.sent_at

    def forget(self, key: Hashable) -> None:
        """Stop tracking (e.g. the segment already had K ACKs)."""
        self._sends.pop(key, None)

    def __len__(self) -> int:
        return len(self._sends)


# ============================================================
# CONFIG
# ============================================================


@dataclass(frozen=True, slots=True)
class CongestionConfig:
    alpha: float = 2.0  # below: grow (pipe not full)
    beta: float = 4.0  # above: shrink (queues building)
    gamma: float = 4.0  # slow start ends above this
    min_cwnd: int = 2
    init_cwnd: int | None = None  # None = N (one segment's shards)
    max_window_segments: int = 16  # max_cwnd = this x N
    base_window_s: float = 30.0
    min_rto: float = 1.0
    max_inflight_bytes: int = 64 * 1024 * 1024

    def __post_init__(self) -> None:
        if not 0 <= self.alpha <= self.beta:
            raise ValueError("Need 0 <= alpha <= beta")
        if self.gamma < 0 or self.min_cwnd < 1 or self.max_window_segments < 1:
            raise ValueError("gamma >= 0, min_cwnd >= 1, max_window_segments >= 1")
        if self.base_window_s <= 0 or self.min_rto <= 0 or self.max_inflight_bytes < 1:
            raise ValueError("base_window_s, min_rto, max_inflight_bytes must be > 0")


# ============================================================
# BASELINE RTT
# ============================================================


class BaseRtt:
    """Uncongested RTT per first-hop relay.

    Shard paths differ (different relays and delays), so one global minimum
    would make normal path variation look like queueing. ``base(hop)`` is
    the minimum over the last ``window_s`` seconds for that first hop; with
    fewer than 3 samples it is the global 10th percentile (nearest rank:
    ``sorted[floor(0.1 * (n - 1))]``) over the same window.
    """

    MIN_SAMPLES: Final = 3

    def __init__(self, clock: Clock, *, window_s: float) -> None:
        self._clock = clock
        self._window = window_s
        self._per_hop: dict[str, deque[tuple[float, float]]] = {}
        self._all: deque[tuple[float, float]] = deque()

    def add(self, hop: str, rtt: float) -> None:
        now = self._clock()
        self._per_hop.setdefault(hop, deque()).append((now, rtt))
        self._all.append((now, rtt))
        self._expire(now)

    def _expire(self, now: float) -> None:
        horizon = now - self._window
        for samples in (*self._per_hop.values(), self._all):
            while samples and samples[0][0] < horizon:
                samples.popleft()

    def base(self, hop: str) -> float | None:
        self._expire(self._clock())
        samples = self._per_hop.get(hop)
        if samples is not None and len(samples) >= self.MIN_SAMPLES:
            return min(r for _, r in samples)
        if not self._all:
            return None
        ordered = sorted(r for _, r in self._all)
        return ordered[int(0.1 * (len(ordered) - 1))]


# ============================================================
# VEGAS CONTROLLER
# ============================================================

SLOW_START: Final = "slow_start"
AVOIDANCE: Final = "avoidance"


class VegasController:
    """Delay-based congestion window, counted in shards (TCP/Tor Vegas).

    Each round (about one RTT) estimates how many of our shards sit in queues:

        queued = cwnd x (1 - 1 / median(rtt / base))

    Slow start: +1 per valid sample, until queued > gamma (checked on every
    ACK once the round has 3 samples, to limit overshoot) or ssthresh.
    Avoidance:  once per round, queued < alpha -> +1, queued > beta -> -1.
    No growth while app-limited (inflight < cwnd - 1). Timeouts on one path
    change nothing (path penalties handle them); a burst across >= 2 first
    hops halves the window, at most once per rto.

    Pure and synchronous: the caller passes the clock and every event.
    """

    def __init__(
        self, config: CongestionConfig, clock: Clock, *, shards_per_segment: int
    ) -> None:
        if shards_per_segment < 1:
            raise ValueError("shards_per_segment must be >= 1")
        self.config = config
        self._clock = clock
        self.baseline = BaseRtt(clock, window_s=config.base_window_s)
        self.min_cwnd = config.min_cwnd
        self.max_cwnd = max(
            self.min_cwnd, config.max_window_segments * shards_per_segment
        )
        self._cwnd = float(self._clamp(config.init_cwnd or shards_per_segment))
        self.ssthresh = float(self.max_cwnd)
        self.mode = SLOW_START
        self.queue_est = 0.0
        self._marker: Hashable | None = None
        self._round = 0
        self._sent_round: dict[Hashable, int] = {}  # key -> round it was sent in
        self._ratios: list[float] = []
        self._full = False  # was the window full at some ACK this round?
        self._timeouts: deque[tuple[float, str]] = deque()
        self._last_burst = -math.inf

    # ------------------------------------------------------------------

    @property
    def cwnd(self) -> float:
        return self._cwnd

    def can_send(self, inflight: int) -> bool:
        return inflight < math.floor(self._cwnd)

    def force(self, *, cwnd: float, mode: str) -> None:
        """Set the state directly (tests and experiments)."""
        if mode not in (SLOW_START, AVOIDANCE):
            raise ValueError(f"Unknown mode {mode!r}")
        self._cwnd = self._clamp(cwnd)
        self.mode = mode

    def _clamp(self, value: float) -> float:
        return float(min(max(value, self.min_cwnd), self.max_cwnd))

    def _reset_round(self) -> None:
        self._marker = None
        self._round += 1
        self._ratios = []
        self._full = False

    # ------------------------------------------------------------------

    def on_send(self, key: Hashable) -> None:
        self._sent_round[key] = self._round
        if self._marker is None:
            self._marker = key  # this round ends when it is ACKed

    def on_cancel(self, key: Hashable) -> None:
        """A shard left inflight without an ACK (cancelled or timed out)."""
        self._sent_round.pop(key, None)
        if key is self._marker:
            self._reset_round()

    def on_ack(
        self, key: Hashable, sample: float | None, *, first_hop: str, inflight: int
    ) -> None:
        limited = inflight < self._cwnd - 1
        self._full = self._full or not limited
        sent_in = self._sent_round.pop(key, None)

        if sample is not None and math.isfinite(sample) and sample > 0:
            self.baseline.add(first_hop, sample)
            base = self.baseline.base(first_hop) or sample
            if sent_in == self._round:  # judge a round only by its own shards
                self._ratios.append(sample / base)
            if self.mode == SLOW_START:
                if self._queue_now() > self.config.gamma:
                    self._leave_slow_start()  # don't wait for the round to end
                elif not limited:
                    self._cwnd = self._clamp(self._cwnd + 1)
                    if self._cwnd >= self.ssthresh:
                        self._leave_slow_start()

        if key is self._marker:
            self._end_round()

    def _queue_now(self) -> float:
        """Queue estimate from this round so far (needs 3 samples)."""
        if len(self._ratios) < 3:
            return 0.0
        return self._cwnd * (1 - 1 / max(1.0, statistics.median(self._ratios)))

    def _leave_slow_start(self) -> None:
        self.mode = AVOIDANCE
        self.ssthresh = self._cwnd

    def _end_round(self) -> None:
        if self._ratios:
            ratio = max(1.0, statistics.median(self._ratios))
            self.queue_est = self._cwnd * (1 - 1 / ratio)
            if self.mode == SLOW_START:
                if self.queue_est > self.config.gamma:
                    self._leave_slow_start()
            elif self.queue_est < self.config.alpha and self._full:
                self._cwnd = self._clamp(self._cwnd + 1)
            elif self.queue_est > self.config.beta:
                self._cwnd = self._clamp(self._cwnd - 1)
        self._reset_round()

    def on_timeout(
        self, first_hops: list[str], *, inflight: int, rto: float = 1.0
    ) -> bool:
        """Shards timed out. True if it was a burst and the window was halved."""
        now = self._clock()
        self._timeouts.extend((now, hop) for hop in first_hops)
        while self._timeouts and self._timeouts[0][0] < now - rto:
            self._timeouts.popleft()

        recent = [hop for _, hop in self._timeouts]
        burst = (
            len(recent) >= max(3, 0.5 * inflight)
            and len(set(recent)) >= 2
            and now - self._last_burst >= rto
        )
        if not burst:
            return False

        self._cwnd = self._clamp(self._cwnd / 2)
        self.ssthresh = self._cwnd
        self.mode = AVOIDANCE
        self._last_burst = now
        self._timeouts.clear()
        self._reset_round()
        return True
