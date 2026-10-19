from datetime import datetime

from maxie.budget import HAIKU, OPUS, SONNET, Budget, cost_usd
from maxie.db import DB
from tests.conftest import TZ, Clock


def test_cost_counts_every_usage_field():
    usage = {"input_tokens": 1_000_000, "output_tokens": 100_000, "cache_creation_input_tokens": 1_000_000,
             "cache_read_input_tokens": 1_000_000, "server_tool_use": {"web_search_requests": 2}}
    # 2 + 1 + 2*2 (a 1-hour cache write) + 0.2 + 2 searches * 0.01
    assert abs(cost_usd(SONNET, usage) - 7.22) < 1e-9
    assert cost_usd(HAIKU, {"input_tokens": 1_000_000}) == 0.10
    # unknown id: the dearest known price in its tier x1.5 (no tier: Opus x1.5), so the cap never under-counts
    assert cost_usd("claude-unknown", {"output_tokens": 1_000_000}) == 1.5 * cost_usd(OPUS, {"output_tokens": 1_000_000})
    assert cost_usd("claude-sonnet-9", {"output_tokens": 1_000_000}) == 1.5 * cost_usd(SONNET, {"output_tokens": 1_000_000})


def test_warn_then_cap_then_haiku_only():
    clock = Clock(datetime(2026, 10, 15, 12, tzinfo=TZ))
    b = Budget(DB(), cap_usd=10, clock=clock)
    assert b.status().state == "ok"
    b.record(SONNET, {"output_tokens": 800_000})  # $8
    assert b.status().state == "warn" and "heads up" in b.status().line()
    b.record(SONNET, {"output_tokens": 200_000})  # $10
    s = b.status()
    assert s.state == "capped" and "budget reached" in s.line()
    assert not b.allows(SONNET) and not b.allows(OPUS)
    assert b.allows(HAIKU)  # direct actions keep working ...
    b.record(SONNET, {"output_tokens": 60_000})  # ... until 5% over the cap
    assert not b.allows(HAIKU)


def test_month_and_day_reset():
    clock = Clock(datetime(2026, 10, 31, 23, tzinfo=TZ))
    b = Budget(DB(), cap_usd=10, clock=clock)
    b.record(SONNET, {"output_tokens": 1_000_000})
    assert b.status().state == "capped" and b.status().today_usd == 10
    clock.set(datetime(2026, 11, 1, 0, 30, tzinfo=TZ))
    s = b.status()
    assert s.state == "ok" and s.month_usd == 0 and s.today_usd == 0
