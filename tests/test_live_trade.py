from __future__ import annotations

from paisa_trader.live_trade import (
    LiveTradeCall,
    LiveTradeRiskConfig,
    build_entry_call,
    build_exit_call,
    evaluate_exit,
    signal_call_preview,
    update_trailing_stop,
)


def test_build_entry_call_sets_sl_and_target_from_atr():
    call = build_entry_call(
        "RELIANCE",
        "NSE_EQ",
        "Reliance Industries Ltd",
        live_ltp=1000.0,
        atr=10.0,
        quantity=5,
        signal_reasons=["ML UP"],
        risk=LiveTradeRiskConfig(atr_sl_mult=1.5, atr_target_mult=2.5, trail_atr_mult=1.0),
    )
    assert call.call == "BUY"
    assert call.entry_inr == 1000.0
    assert call.stop_loss_inr == 985.0
    assert call.target_inr == 1025.0
    assert call.status == "OPEN"


def test_trailing_stop_ratchet_and_target_exit():
    open_call = build_entry_call(
        "TCS",
        "NSE_EQ",
        "TCS",
        live_ltp=100.0,
        atr=2.0,
        quantity=1,
        signal_reasons=[],
    )
    trailed = update_trailing_stop(open_call, 110.0, 2.0)
    assert trailed.trailing_sl_inr > open_call.trailing_sl_inr
    status, reason = evaluate_exit(trailed, 110.0)
    assert status == "TARGET_HIT"
    assert "Target" in reason


def test_trailing_exit_when_price_reverses():
    open_call = build_entry_call(
        "INFY",
        "NSE_EQ",
        "INFY",
        live_ltp=100.0,
        atr=2.0,
        quantity=1,
        signal_reasons=[],
        risk=LiveTradeRiskConfig(atr_sl_mult=1.5, atr_target_mult=5.0, trail_atr_mult=1.0),
    )
    trailed = update_trailing_stop(open_call, 108.0, 2.0)
    trailed = update_trailing_stop(trailed, 104.0, 2.0)
    status, _ = evaluate_exit(trailed, 104.0)
    assert status in {"TRAIL_EXIT", "SL_HIT"}


def test_signal_preview_has_systematic_fields():
    preview = signal_call_preview("RELIANCE", "NSE_EQ", "Reliance", 2500.0, 12.0, ["ensemble"])
    assert preview.status == "SIGNAL"
    assert preview.segment == "NSE_EQ"
    assert preview.entry_inr == 2500.0


def test_build_exit_call_marks_sell_and_exit():
    open_call = LiveTradeCall(
        symbol="RELIANCE",
        segment="NSE_EQ",
        stock_name="Reliance",
        call="BUY",
        entry_inr=100.0,
        stop_loss_inr=95.0,
        target_inr=110.0,
        trailing_sl_inr=95.0,
        status="OPEN",
        quantity=2,
        opened_at="2026-06-22T10:00:00+00:00",
    )
    closed = build_exit_call(open_call, 110.0, "TARGET_HIT", "Target reached")
    assert closed.call == "SELL"
    assert closed.exit_inr == 110.0
    assert closed.status == "TARGET_HIT"
