"""TC45 — Alpha-4 shared Gamma resolver helpers parity test.

The helpers in ``analysis.gamma_resolution`` were extracted verbatim from
``calibration_decision_join`` and ``estimate_decision_results``. This test
exercises the shared API directly AND verifies the existing callers still
behave the same (the existing module-level tests in
``test_analyze_calibration.py`` / ``test_estimate_decision_results.py``
keep the per-caller policy honest).
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from analysis import gamma_resolution as gr


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _RecordingClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls: list[tuple[str, dict]] = []

    def get(self, url, params=None):
        self.calls.append((url, dict(params or {})))
        return _Response(self.payload)


def test_parse_finite_decimal_round_trips():
    assert gr.parse_finite_decimal("1.25", "x") == Decimal("1.25")
    assert gr.parse_finite_decimal(2, "x") == Decimal("2")


@pytest.mark.parametrize("bad", ["NaN", "Infinity"])
def test_parse_finite_decimal_rejects_non_finite(bad):
    with pytest.raises(ValueError, match="must be finite"):
        gr.parse_finite_decimal(bad, "x")


def test_parse_json_array_round_trips():
    assert gr.parse_json_array('["yes", "no"]', "outcomes") == ["yes", "no"]


def test_parse_json_array_rejects_non_string():
    with pytest.raises(ValueError, match="JSON-encoded array string"):
        gr.parse_json_array(["yes", "no"], "outcomes")


def test_parse_json_array_rejects_non_array_payload():
    with pytest.raises(ValueError, match="JSON array"):
        gr.parse_json_array(json.dumps({"k": "v"}), "outcomes")


def test_load_decision_records_round_trip(tmp_path: Path):
    path = tmp_path / "decisions.jsonl"
    path.write_text(
        json.dumps({"decision_id": "1"}) + "\n" + json.dumps({"decision_id": "2"}) + "\n",
        encoding="utf-8",
    )
    records = gr.load_decision_records(path)
    assert [r["decision_id"] for r in records] == ["1", "2"]


def test_load_decision_records_rejects_blank_lines(tmp_path: Path):
    path = tmp_path / "decisions.jsonl"
    path.write_text("\n", encoding="utf-8")
    with pytest.raises(ValueError, match="is blank"):
        gr.load_decision_records(path)


def test_fetch_market_by_slug_closed_only_sends_closed_param():
    client = _RecordingClient(
        [{"slug": "x", "closed": True, "outcomes": "[\"yes\"]", "outcomePrices": "[\"1\"]"}]
    )
    market = gr.fetch_market_by_slug(client, "x", closed_only=True)
    assert market["slug"] == "x"
    assert client.calls == [
        (gr.GAMMA_MARKETS_URL, {"slug": "x", "limit": 2, "closed": "true"})
    ]


def test_fetch_market_by_slug_open_caller_omits_closed_param():
    client = _RecordingClient(
        [{"slug": "x", "closed": False, "outcomes": "[\"yes\"]", "outcomePrices": "[\"0.5\"]"}]
    )
    market = gr.fetch_market_by_slug(client, "x", closed_only=False)
    assert market["slug"] == "x"
    assert client.calls == [(gr.GAMMA_MARKETS_URL, {"slug": "x", "limit": 2})]


def test_fetch_market_by_slug_closed_only_returns_none_when_no_markets():
    client = _RecordingClient([])
    market = gr.fetch_market_by_slug(client, "absent", closed_only=True)
    assert market is None


def test_fetch_market_by_slug_closed_only_raises_when_candidates_but_no_exact():
    client = _RecordingClient([{"slug": "different", "closed": True}])
    with pytest.raises(ValueError, match="no exact closed match"):
        gr.fetch_market_by_slug(client, "wanted", closed_only=True)


def test_fetch_market_by_slug_open_caller_raises_on_zero_exact_matches():
    client = _RecordingClient([])
    with pytest.raises(ValueError, match="0 exact matches"):
        gr.fetch_market_by_slug(client, "absent", closed_only=False)


def test_market_is_closed_requires_bool():
    with pytest.raises(ValueError, match="must be a boolean"):
        gr.market_is_closed({"slug": "x", "closed": "true"})


def test_winning_side_open_market_returns_none():
    market = {
        "slug": "x",
        "closed": False,
        "outcomes": '["yes", "no"]',
        "outcomePrices": '["0.5", "0.5"]',
    }
    assert gr.winning_side(market) is None


@pytest.mark.parametrize(
    "label, expected",
    [("yes", "long"), ("Yes", "long"), ("UP", "long"), ("no", "short"), ("down", "short")],
)
def test_winning_side_maps_label_to_direction(label, expected):
    market = {
        "slug": "x",
        "closed": True,
        "outcomes": json.dumps([label, "other"]),
        "outcomePrices": '["1", "0"]',
    }
    assert gr.winning_side(market) == expected


def test_winning_side_no_winner_raises():
    market = {
        "slug": "x",
        "closed": True,
        "outcomes": '["yes", "no"]',
        "outcomePrices": '["0.5", "0.5"]',
    }
    with pytest.raises(ValueError, match="no winning outcome"):
        gr.winning_side(market)


def test_calibration_module_still_re_exports_constants():
    # Existing callers reference these as module attributes; the refactor
    # must preserve them so the prior test suite keeps passing.
    import calibration_decision_join as cdj
    assert cdj.GAMMA_MARKETS_URL == gr.GAMMA_MARKETS_URL
    assert cdj.WINNING_PRICE == gr.WINNING_PRICE


def test_estimate_module_still_re_exports_constants():
    import estimate_decision_results as edr
    assert edr.GAMMA_MARKETS_URL == gr.GAMMA_MARKETS_URL
    assert edr.WINNING_PRICE == gr.WINNING_PRICE
