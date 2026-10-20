from __future__ import annotations

import asyncio
import dataclasses
import logging
import threading

import pytest

from mpos.events import (
    BusLogHandler,
    EventBus,
    LogRecord,
    NodeStatus,
    ShardAcked,
)


def _status(i: int) -> NodeStatus:
    return NodeStatus(node_id=f"n{i}", running=True)


async def test_every_subscriber_gets_every_event() -> None:
    bus = EventBus()
    a, b = bus.subscribe(), bus.subscribe()

    for i in range(3):
        bus.publish(_status(i))

    assert [(await a.get()).node_id for _ in range(3)] == ["n0", "n1", "n2"]  # type: ignore[attr-defined]
    assert [(await b.get()).node_id for _ in range(3)] == ["n0", "n1", "n2"]  # type: ignore[attr-defined]


async def test_slow_subscriber_drops_oldest_without_blocking() -> None:
    bus = EventBus()
    slow = bus.subscribe(maxsize=2)

    for i in range(5):
        bus.publish(_status(i))  # must never block or raise

    assert slow.dropped == 3
    assert [(await slow.get()).node_id for _ in range(2)] == ["n3", "n4"]  # type: ignore[attr-defined]


async def test_unsubscribe() -> None:
    bus = EventBus()
    sub = bus.subscribe()
    sub.close()
    bus.publish(_status(0))

    assert bus.subscriber_count == 0
    assert sub.queue.empty()


def test_events_are_frozen_and_timestamped() -> None:
    event = ShardAcked(node_id="a", transfer_id="t", segment=1, shard=2)

    assert event.ts > 0
    with pytest.raises(dataclasses.FrozenInstanceError):
        event.segment = 5  # type: ignore[misc]


async def test_log_handler_publishes_from_any_thread() -> None:
    bus = EventBus()
    sub = bus.subscribe()
    logger = logging.getLogger("mpos.test_events")
    handler = BusLogHandler(bus, asyncio.get_running_loop())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    try:
        worker = threading.Thread(target=logger.warning, args=("from %s", "worker"))
        worker.start()
        worker.join()
        logger.debug("below level, ignored")

        async with asyncio.timeout(2):
            event = await sub.get()
    finally:
        logger.removeHandler(handler)

    assert isinstance(event, LogRecord)
    assert (event.level, event.message) == ("WARNING", "from worker")
    assert sub.queue.empty()
