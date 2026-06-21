"""
Deribit Put/Call Ratio Signal Processor

Beta-2/3/4/7 + §3.D rows 1-4 contract:
  - ``name`` REQUIRED kwarg in __init__.
  - ``now``, ``decision_id`` REQUIRED kwargs on process().
  - ``pcr_data_override: Optional[dict]`` kwarg on process() — replayer
    always supplies; production callers leave None.
  - ``raw_payload_hash`` always cached; raw payload bytes opt-in via
    env ``RAW_DECISION_SNAPSHOT_INCLUDE_DERIBIT_RAW=1``.
  - HTTP fetch / instrument parse / missing short_pcr → DROP per §3.D
    with the appropriate ``deribit_*_dropped`` counter; no silent
    default (the prior ``or overall_pcr or 1.0`` chain is removed).
  - ``_parse_dte(..., now)`` takes the injected wall-clock.
  - ``last_fetch_diagnostics()`` returns the §4.2 deribit_pcr block.
"""
import hashlib
import json
import os
import sys

import httpx
from decimal import Decimal
from datetime import datetime, timezone
from typing import Optional, Dict, Any
from loguru import logger

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from core.strategy_brain.signal_processors.base_processor import (
    BaseSignalProcessor,
    TradingSignal,
    SignalType,
    SignalDirection,
    SignalStrength,
)

DERIBIT_URL = "https://www.deribit.com/api/v2/public/get_book_summary_by_currency"


class DeribitPCRProcessor(BaseSignalProcessor):
    def __init__(
        self,
        *,
        name: str,
        bullish_pcr_threshold: float = 1.20,
        bearish_pcr_threshold: float = 0.70,
        max_days_to_expiry: int = 2,
        min_open_interest: float = 100.0,
        cache_seconds: int = 300,
        min_confidence: float = 0.55,
    ):
        super().__init__(name)

        self.bullish_pcr_threshold = bullish_pcr_threshold
        self.bearish_pcr_threshold = bearish_pcr_threshold
        self.max_days_to_expiry = max_days_to_expiry
        self.min_open_interest = min_open_interest
        self.cache_seconds = cache_seconds
        self.min_confidence = min_confidence

        self._cached_result: Optional[Dict] = None
        self._cache_time: Optional[datetime] = None
        self._cached_raw_payload: Optional[bytes] = None
        self._cached_raw_payload_hash: Optional[str] = None
        self._last_diagnostics: Dict[str, Any] = {}

        logger.info(
            f"Initialized Deribit PCR Processor: "
            f"bullish_pcr>{bullish_pcr_threshold}, "
            f"bearish_pcr<{bearish_pcr_threshold}, "
            f"max_dte={max_days_to_expiry}d"
        )

    def effective_params(self) -> Dict[str, Any]:
        return dict(sorted({
            "name": self.name,
            "bullish_pcr_threshold": self.bullish_pcr_threshold,
            "bearish_pcr_threshold": self.bearish_pcr_threshold,
            "max_days_to_expiry": self.max_days_to_expiry,
            "min_open_interest": self.min_open_interest,
            "cache_seconds": self.cache_seconds,
            "min_confidence": self.min_confidence,
        }.items()))

    def last_fetch_diagnostics(self) -> Dict[str, Any]:
        return dict(self._last_diagnostics)

    def _get_client(self) -> httpx.Client:
        return httpx.Client(timeout=8.0)

    def _parse_dte(self, instrument_name: str, *, now: datetime) -> Optional[int]:
        """
        Parse days to expiry. §3.D row-3: malformed name → DROP
        (increment ``deribit_instrument_parse_dropped`` at caller) and
        return None. No fallback substitution.
        """
        parts = instrument_name.split("-")
        if len(parts) < 3:
            return None
        try:
            expiry_str = parts[1]
            expiry_dt = datetime.strptime(expiry_str, "%d%b%y").replace(tzinfo=timezone.utc)
            dte = (expiry_dt - now).days
            return max(0, dte)
        except (ValueError, IndexError):
            return None

    def _fetch_pcr(self, *, now: datetime) -> Optional[Dict]:
        """
        §3.D row-1/2/3/4 DROP. HTTP failure → drop, return None.
        Malformed instrument names → drop per-instrument, continue.
        Missing short_pcr key → drop the short_pcr field (no 1.0 default).
        """
        try:
            with self._get_client() as client:
                resp = client.get(
                    DERIBIT_URL,
                    params={"currency": "BTC", "kind": "option"},
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception as e:
            logger.warning(f"Deribit PCR fetch failed: {e}")
            self._increment_drop("deribit_fetch_dropped")
            return None

        summaries = data.get("result", [])
        if not summaries:
            logger.warning("Deribit returned empty options data")
            self._increment_drop("deribit_fetch_dropped")
            return None

        # Beta-7: hash the raw upstream list BEFORE deriving fields.
        raw_bytes = json.dumps(summaries, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
        self._cached_raw_payload_hash = hashlib.sha256(raw_bytes).hexdigest()
        if os.environ.get("RAW_DECISION_SNAPSHOT_INCLUDE_DERIBIT_RAW") == "1":
            self._cached_raw_payload = raw_bytes
        else:
            self._cached_raw_payload = None

        put_oi = 0.0
        call_oi = 0.0
        short_put_oi = 0.0
        short_call_oi = 0.0

        for item in summaries:
            name = item.get("instrument_name", "")
            try:
                oi = float(item.get("open_interest", 0))
            except (TypeError, ValueError):
                self._increment_drop("deribit_instrument_parse_dropped")
                continue

            if oi < self.min_open_interest:
                continue

            is_put = name.endswith("-P")
            is_call = name.endswith("-C")

            if is_put:
                put_oi += oi
            elif is_call:
                call_oi += oi
            else:
                # Not a put or call; skip silently (well-formed but irrelevant
                # instrument type, e.g. future_combo).
                continue

            dte = self._parse_dte(name, now=now)
            if dte is None:
                self._increment_drop("deribit_instrument_parse_dropped")
                continue
            if dte <= self.max_days_to_expiry:
                if is_put:
                    short_put_oi += oi
                elif is_call:
                    short_call_oi += oi

        overall_pcr = put_oi / call_oi if call_oi > 0 else None

        if short_call_oi > 0:
            short_pcr = short_put_oi / short_call_oi
        else:
            # §3.D row-4: short_pcr is missing; DROP the short value, do NOT
            # substitute overall_pcr or 1.0. Caller's _generate_signal will
            # see short_pcr=None and treat the signal as unobservable.
            short_pcr = None
            self._increment_drop("deribit_short_pcr_missing_dropped")

        result = {
            "overall_pcr": round(overall_pcr, 4) if overall_pcr is not None else None,
            "short_pcr": round(short_pcr, 4) if short_pcr is not None else None,
            "put_oi": round(put_oi, 2),
            "call_oi": round(call_oi, 2),
            "short_put_oi": round(short_put_oi, 2),
            "short_call_oi": round(short_call_oi, 2),
            "total_contracts": len(summaries),
            "fetched_at": now.isoformat(),
        }

        logger.info(
            f"Deribit: overall_PCR={overall_pcr}, short_PCR={short_pcr} "
            f"(puts={short_put_oi:.0f} vs calls={short_call_oi:.0f} short-dated)"
        )

        return result

    def process(
        self,
        current_price: Decimal,
        historical_prices: list,
        metadata: Dict[str, Any],
        *,
        now: datetime,
        decision_id: str,
        pcr_data_override: Optional[Dict] = None,
    ) -> Optional[TradingSignal]:
        self._reset_signal_ordinal()
        self._last_diagnostics = {
            "now": now,
            "fresh_fetch_performed": False,
            "cache_hit": False,
            "raw_payload_hash": self._cached_raw_payload_hash,
        }

        if not self.is_enabled:
            return None

        # Beta-7: replayer always supplies pcr_data_override.
        if pcr_data_override is not None:
            pcr_data = pcr_data_override
            self._last_diagnostics["fresh_fetch_performed"] = False
            self._last_diagnostics["cache_hit"] = False
            self._last_diagnostics["pcr_data_override_used"] = True
            return self._generate_signal(current_price, pcr_data, now=now, decision_id=decision_id)

        cache_valid = (
            self._cached_result is not None and
            self._cache_time is not None and
            (now - self._cache_time).total_seconds() < self.cache_seconds
        )

        if cache_valid:
            pcr_data = self._cached_result
            self._last_diagnostics["cache_hit"] = True
            logger.debug(
                f"DeribitPCR: using cached data (PCR={pcr_data.get('short_pcr')})"
            )
        else:
            pcr_data = self._fetch_pcr(now=now)
            self._last_diagnostics["fresh_fetch_performed"] = True
            self._last_diagnostics["raw_payload_hash"] = self._cached_raw_payload_hash
            if pcr_data is None:
                return None
            self._cached_result = pcr_data
            self._cache_time = now

        return self._generate_signal(current_price, pcr_data, now=now, decision_id=decision_id)

    def _generate_signal(
        self,
        current_price: Decimal,
        pcr_data: Dict,
        *,
        now: datetime,
        decision_id: str,
    ) -> Optional[TradingSignal]:
        # §3.D row-4: short_pcr absent → no signal (NOT a 1.0 substitute).
        short_pcr = pcr_data.get("short_pcr")
        if short_pcr is None:
            logger.debug(
                "DeribitPCR: short_pcr unavailable → no signal "
                "(no overall_pcr / 1.0 substitution)"
            )
            return None
        pcr = short_pcr

        if pcr >= self.bullish_pcr_threshold:
            direction = SignalDirection.BULLISH
            extremeness = (pcr - self.bullish_pcr_threshold) / self.bullish_pcr_threshold
            confidence = min(0.80, 0.57 + extremeness * 0.15)
            if pcr >= 1.60:
                strength = SignalStrength.VERY_STRONG
            elif pcr >= 1.40:
                strength = SignalStrength.STRONG
            else:
                strength = SignalStrength.MODERATE
            logger.info(
                f"DeribitPCR: HIGH PCR={pcr:.3f} (excessive puts = fear) → contrarian BULLISH"
            )

        elif pcr <= self.bearish_pcr_threshold:
            direction = SignalDirection.BEARISH
            extremeness = (self.bearish_pcr_threshold - pcr) / self.bearish_pcr_threshold
            confidence = min(0.80, 0.57 + extremeness * 0.15)
            if pcr <= 0.45:
                strength = SignalStrength.VERY_STRONG
            elif pcr <= 0.55:
                strength = SignalStrength.STRONG
            else:
                strength = SignalStrength.MODERATE
            logger.info(
                f"DeribitPCR: LOW PCR={pcr:.3f} (excessive calls = greed) → contrarian BEARISH"
            )

        else:
            logger.debug(
                f"DeribitPCR: balanced PCR={pcr:.3f} "
                f"(range {self.bearish_pcr_threshold}–{self.bullish_pcr_threshold}) — no signal"
            )
            return None

        if confidence < self.min_confidence:
            return None

        signal = TradingSignal(
            timestamp=now,
            source=self.name,
            signal_type=SignalType.SENTIMENT_SHIFT,
            direction=direction,
            strength=strength,
            confidence=confidence,
            current_price=current_price,
            signal_id=self._next_signal_id(decision_id),
            metadata={
                "pcr": round(pcr, 4),
                "overall_pcr": pcr_data.get("overall_pcr"),
                "short_put_oi": pcr_data.get("short_put_oi"),
                "short_call_oi": pcr_data.get("short_call_oi"),
                "interpretation": (
                    "excessive_puts_fear" if direction == SignalDirection.BULLISH
                    else "excessive_calls_greed"
                ),
            }
        )

        self._record_signal(signal)

        logger.info(
            f"Generated {direction.value.upper()} signal (DeribitPCR): "
            f"PCR={pcr:.3f}, confidence={confidence:.2%}, score={signal.score:.1f}"
        )

        return signal
