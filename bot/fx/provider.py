"""Currency conversion to USD (the group currency).

- ARS uses dolarapi.com so the group can pick oficial / blue / mep (official vs
  parallel rates diverge ~2x in Argentina — this is a real money decision, set
  per group via WhatsAppGroupLink.fxArsSource).
- Everything else uses a general USD-based FX API (open.er-api.com).

spliit stores ``amount = originalAmount * conversionRate`` (both in cents), so
``conversion_rate`` here is USD-value per unit of the original currency
(= 1 / units-per-USD).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import httpx

from config import settings

logger = logging.getLogger(__name__)

_CACHE_TTL = 300  # seconds; rates don't move minute-to-minute
_cache: dict[str, tuple[float, float]] = {}  # key -> (value, fetched_at)

# dolarapi endpoints by source
_ARS_ENDPOINTS = {
    "oficial": "/v1/dolares/oficial",
    "blue": "/v1/dolares/blue",
    "mep": "/v1/dolares/bolsa",  # MEP is "bolsa" in dolarapi
}


class ConversionError(Exception):
    pass


@dataclass
class ConversionResult:
    usd: float  # converted value in USD major units
    units_per_usd: float  # display rate (e.g. 1200 ARS per USD)
    conversion_rate: float  # USD per unit, for spliit's conversionRate column
    label: str  # e.g. "blue 1200" or "CLP 950"


def _cached(key: str, fetch) -> float:
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[1] < _CACHE_TTL:
        return hit[0]
    value = fetch()
    _cache[key] = (value, now)
    return value


def _ars_units_per_usd(source: str) -> float:
    endpoint = _ARS_ENDPOINTS.get(source, _ARS_ENDPOINTS["blue"])

    def fetch() -> float:
        url = f"{settings.dolarapi_url}{endpoint}"
        resp = httpx.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        venta = data.get("venta") or data.get("compra")
        if not venta:
            raise ConversionError(f"dolarapi returned no rate: {data}")
        return float(venta)

    return _cached(f"ars:{source}", fetch)


def _general_units_per_usd(currency: str) -> float:
    def fetch() -> float:
        url = f"{settings.fx_general_url}/v6/latest/USD"
        resp = httpx.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        rates = data.get("rates") or {}
        rate = rates.get(currency)
        if not rate:
            raise ConversionError(f"no FX rate for {currency}")
        return float(rate)

    return _cached(f"gen:{currency}", fetch)


def convert(amount: float, currency: str, ars_source: str = "blue") -> ConversionResult:
    """Convert ``amount`` of ``currency`` to USD. Raises ConversionError on failure."""
    currency = (currency or "USD").upper()
    if currency == "USD":
        return ConversionResult(usd=amount, units_per_usd=1.0, conversion_rate=1.0, label="USD")

    if currency == "ARS":
        units = _ars_units_per_usd(ars_source)
        label = f"{ars_source} {units:.0f}"
    else:
        units = _general_units_per_usd(currency)
        label = f"{currency} {units:g}"

    if units <= 0:
        raise ConversionError(f"invalid rate for {currency}")
    return ConversionResult(
        usd=amount / units,
        units_per_usd=units,
        conversion_rate=1.0 / units,
        label=label,
    )
