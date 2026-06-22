from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class LiveTradeRiskConfig:
    atr_sl_mult: float = 1.5
    atr_target_mult: float = 2.5
    trail_atr_mult: float = 1.0


@dataclass
class LiveTradeCall:
    """Systematic live paper trade call with entry, SL, target, and exit fields.

    Args:
        symbol: NSE trading symbol.
        segment: Exchange segment such as ``NSE_EQ``.
        stock_name: Human-readable company name.
        call: ``BUY`` for long entry or ``SELL`` for exit.
        entry_inr: Live entry price in INR.
        stop_loss_inr: Initial or trailing stop-loss level in INR.
        target_inr: Profit target in INR.
        exit_inr: Live exit price when closed.
        trailing_sl_inr: Current trailing stop level in INR.
        status: ``SIGNAL``, ``OPEN``, ``TARGET_HIT``, ``SL_HIT``, or ``TRAIL_EXIT``.
        quantity: Paper quantity for the call.
        opened_at: ISO timestamp when the position opened.
        closed_at: ISO timestamp when the position closed.
        exit_reason: Human-readable exit explanation.
        live_ltp_at_signal: Live LTP when the signal fired.
        execution_source: Always ``live`` for market fills.
    """

    symbol: str
    segment: str
    stock_name: str
    call: str
    entry_inr: float
    stop_loss_inr: float
    target_inr: float
    exit_inr: float | None = None
    trailing_sl_inr: float = 0.0
    status: str = "SIGNAL"
    quantity: int = 0
    opened_at: str | None = None
    closed_at: str | None = None
    exit_reason: str | None = None
    live_ltp_at_signal: float = 0.0
    execution_source: str = "live"
    highest_since_entry: float = 0.0
    signal_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["exit"] = self.exit_inr
        return payload

    def copy(self) -> LiveTradeCall:
        return LiveTradeCall(**asdict(self))


def build_entry_call(
    symbol: str,
    segment: str,
    stock_name: str,
    live_ltp: float,
    atr: float,
    quantity: int,
    signal_reasons: list[str],
    risk: LiveTradeRiskConfig | None = None,
) -> LiveTradeCall:
    """Create a systematic BUY call from a live LTP and ATR risk plan.

    Args:
        symbol: NSE trading symbol.
        segment: Exchange segment such as ``NSE_EQ``.
        stock_name: Company display name.
        live_ltp: Current live last traded price.
        atr: ATR(14) from feature candles.
        quantity: Paper quantity to buy.
        signal_reasons: Intelligence reasons backing the entry.
        risk: ATR multipliers for stop, target, and trail.

    Returns:
        A ``LiveTradeCall`` with entry, SL, and target populated.

    Example:
        ``build_entry_call("RELIANCE", "NSE_EQ", "Reliance", 2500.0, 12.0, 10, []).call``
        returns ``"BUY"``.
    """
    cfg = risk or LiveTradeRiskConfig()
    atr_distance = max(float(atr or 0.0), live_ltp * 0.002, 0.05)
    stop = round(live_ltp - atr_distance * cfg.atr_sl_mult, 4)
    target = round(live_ltp + atr_distance * cfg.atr_target_mult, 4)
    now = datetime.now(timezone.utc).isoformat()
    return LiveTradeCall(
        symbol=symbol,
        segment=segment,
        stock_name=stock_name,
        call="BUY",
        entry_inr=round(live_ltp, 4),
        stop_loss_inr=stop,
        target_inr=target,
        trailing_sl_inr=stop,
        status="OPEN",
        quantity=int(quantity),
        opened_at=now,
        live_ltp_at_signal=round(live_ltp, 4),
        highest_since_entry=round(live_ltp, 4),
        signal_reasons=list(signal_reasons),
    )


def build_exit_call(open_call: LiveTradeCall, live_ltp: float, exit_status: str, exit_reason: str) -> LiveTradeCall:
    """Close an open live trade call at the current market price.

    Args:
        open_call: The open ``LiveTradeCall`` to close.
        live_ltp: Current live last traded price.
        exit_status: One of ``TARGET_HIT``, ``SL_HIT``, or ``TRAIL_EXIT``.
        exit_reason: Human-readable exit explanation.

    Returns:
        Updated call with ``SELL`` side fields and exit populated.

    Example:
        ``build_exit_call(call, 2550.0, "TARGET_HIT", "Target reached").call`` returns ``"SELL"``.
    """
    closed = open_call.copy()
    closed.call = "SELL"
    closed.exit_inr = round(live_ltp, 4)
    closed.status = exit_status
    closed.closed_at = datetime.now(timezone.utc).isoformat()
    closed.exit_reason = exit_reason
    closed.trailing_sl_inr = round(open_call.trailing_sl_inr, 4)
    return closed


def update_trailing_stop(call: LiveTradeCall, live_ltp: float, atr: float, risk: LiveTradeRiskConfig | None = None) -> LiveTradeCall:
    """Ratchet the trailing stop higher as price moves in favour.

    Args:
        call: Open long ``LiveTradeCall``.
        live_ltp: Current live last traded price.
        atr: ATR(14) from feature candles.
        risk: Trailing ATR multiplier config.

    Returns:
        Updated call with a raised ``trailing_sl_inr`` when appropriate.

    Example:
        ``update_trailing_stop(call, 2520.0, 10.0).trailing_sl_inr`` moves the trail up on strength.
    """
    cfg = risk or LiveTradeRiskConfig()
    atr_distance = max(float(atr or 0.0), live_ltp * 0.002, 0.05)
    highest = max(call.highest_since_entry, live_ltp)
    trail = max(call.trailing_sl_inr, highest - atr_distance * cfg.trail_atr_mult)
    updated = call.copy()
    updated.highest_since_entry = round(highest, 4)
    updated.trailing_sl_inr = round(trail, 4)
    updated.stop_loss_inr = round(trail, 4)
    return updated


def evaluate_exit(call: LiveTradeCall, live_ltp: float) -> tuple[str | None, str]:
    """Check whether an open long should exit at the live market price.

    Args:
        call: Open long ``LiveTradeCall``.
        live_ltp: Current live last traded price.

    Returns:
        A tuple of ``(exit_status, exit_reason)`` or ``(None, "")`` when still open.

    Example:
        ``evaluate_exit(call, 2600.0)`` returns ``("TARGET_HIT", "...")`` when target is hit.
    """
    if live_ltp >= call.target_inr:
        return "TARGET_HIT", f"Target {call.target_inr:.2f} hit at live LTP {live_ltp:.2f}"
    if live_ltp <= call.trailing_sl_inr:
        if call.trailing_sl_inr > call.stop_loss_inr + 0.01:
            return "TRAIL_EXIT", f"Trailing SL {call.trailing_sl_inr:.2f} hit at live LTP {live_ltp:.2f}"
        return "SL_HIT", f"Stop loss {call.trailing_sl_inr:.2f} hit at live LTP {live_ltp:.2f}"
    return None, ""


def signal_call_preview(
    symbol: str,
    segment: str,
    stock_name: str,
    live_ltp: float,
    atr: float,
    signal_reasons: list[str],
    risk: LiveTradeRiskConfig | None = None,
) -> LiveTradeCall:
    """Build a non-executed trade signal preview at the live market price.

    Args:
        symbol: NSE trading symbol.
        segment: Exchange segment such as ``NSE_EQ``.
        stock_name: Company display name.
        live_ltp: Current live last traded price.
        atr: ATR(14) from feature candles.
        signal_reasons: Intelligence reasons for the signal.
        risk: ATR multipliers for stop and target preview.

    Returns:
        A ``SIGNAL`` status call without quantity or execution timestamps.

    Example:
        ``signal_call_preview("TCS", "NSE_EQ", "TCS", 3500.0, 8.0, ["ML UP"]).status`` returns ``"SIGNAL"``.
    """
    cfg = risk or LiveTradeRiskConfig()
    atr_distance = max(float(atr or 0.0), live_ltp * 0.002, 0.05)
    stop = round(live_ltp - atr_distance * cfg.atr_sl_mult, 4)
    target = round(live_ltp + atr_distance * cfg.atr_target_mult, 4)
    return LiveTradeCall(
        symbol=symbol,
        segment=segment,
        stock_name=stock_name,
        call="BUY",
        entry_inr=round(live_ltp, 4),
        stop_loss_inr=stop,
        target_inr=target,
        trailing_sl_inr=stop,
        status="SIGNAL",
        live_ltp_at_signal=round(live_ltp, 4),
        signal_reasons=list(signal_reasons),
    )
