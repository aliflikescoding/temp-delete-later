import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime
import time

# -------------------------------------------------------
# CONNECT MT5
# -------------------------------------------------------
print("Connecting to MetaTrader 5...")

if not mt5.initialize():
    print("❌ MT5 gagal connect:", mt5.last_error())
    quit()
else:
    print("✔ MT5 berhasil connect\n")


# -------------------------------------------------------
# CEK STATUS AUTO TRADING
# -------------------------------------------------------
terminal_info = mt5.terminal_info()

if terminal_info is None:
    print("❌ Gagal membaca terminal info:", mt5.last_error())
    quit()

if terminal_info.trade_allowed:
    print("✔ Auto Trading: ON (hijau)")
else:
    print("❌ Auto Trading MATI! (tombol merah) → bot tidak bisa kirim order")
    quit()


# -------------------------------------------------------
# CEK ACCOUNT INFO
# -------------------------------------------------------
account_info = mt5.account_info()

if account_info is None:
    print("❌ Gagal membaca account info:", mt5.last_error())
    quit()

print("\n=== ACCOUNT INFO ===")
print(f"Login     : {account_info.login}")
print(f"Nama      : {account_info.name}")
print(f"Server    : {account_info.server}")
print(f"Leverage  : {account_info.leverage}")

print("\n=== SALDO ===")
print(f"Balance       : {account_info.balance}")
print(f"Equity        : {account_info.equity}")
print(f"Free Margin   : {account_info.margin_free}")

print("\n────────────────────────────────────────────\n")

symbol = "XAUUSD"
mt5.symbol_select(symbol, True)
print("Symbol:", symbol, "selected\n")

# Storage for hidden SL/TP
hidden_levels = {}   # {ticket: {sl, tp, order_type}}


# =============================================
# FUNCTION: GET LAST 3 CANDLES
# =============================================
def get_last_3():
    data = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M30, 0, 3)
    if data is None:
        print("MT5 ERROR:", mt5.last_error())
        return None

    df = pd.DataFrame(data)
    df["time"] = pd.to_datetime(df["time"], unit='s')

    df["body_top"] = df[["open", "close"]].max(axis=1)
    df["body_bottom"] = df[["open", "close"]].min(axis=1)
    df["body_size"] = (df["close"] - df["open"]).abs()

    df["upper_tail"] = df["high"] - df["body_top"]
    df["lower_tail"] = df["body_bottom"] - df["low"]

    return df


# =============================================
# 2. SEND HIDDEN ORDER (NO SL/TP SENT TO BROKER)
# =============================================
def send_order(order_type, entry, sl, tp):
    volume = 0.02

    request = {
        "action": mt5.TRADE_ACTION_PENDING,
        "symbol": symbol,
        "volume": volume,
        "type": order_type,
        "price": entry,
        "deviation": 20,
        "magic": 20251124,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_RETURN
    }

    result = mt5.order_send(request)
    print("Order send result:", result)

    # If order creation successful → store hidden SL/TP
    if result and result.order > 0:
        hidden_levels[result.order] = {
            "sl": sl,
            "tp": tp,
            "order_type": order_type
        }
        print(f"Hidden SL/TP stored for ticket {result.order}")

    return result


# =============================================
# CANCEL ALL PENDING ORDERS
# =============================================
def cancel_all_pending(symbol):
    orders = mt5.orders_get(symbol=symbol)
    if orders:
        print(f"Menghapus {len(orders)} pending order lama...")
        for o in orders:
            req = {
                "action": mt5.TRADE_ACTION_REMOVE,
                "order": o.ticket
            }
            result = mt5.order_send(req)
            print(f"Cancel pending #{o.ticket} →", result)
    else:
        print("Tidak ada pending order lama.")


# =============================================
# RULE 1 – D0 body > D1 & D2
# =============================================
def is_rule1_acc(D0, D1, D2):
    if not (D0["body_size"] > D1["body_size"] and D0["body_size"] > D2["body_size"]):
        return False

    if D1["body_size"] > D0["body_size"] * 0.5:
        return False
    if D2["body_size"] > D0["body_size"] * 0.5:
        return False

    return True


# =============================================
# RULE 2 – TAIL FILTER
# =============================================
def is_rule2_acc(D0, D1, D2, MAX_TAIL_MULTIPLIER):
    max_tail = D0["body_size"] * MAX_TAIL_MULTIPLIER

    if D1["upper_tail"] > max_tail or D1["lower_tail"] > max_tail:
        print("Reject: Tail D1 > limit")
        return False
    if D2["upper_tail"] > max_tail or D2["lower_tail"] > max_tail:
        print("Reject: Tail D2 > limit")
        return False

    return True


# =============================================
# BUY / SELL LOGIC
# =============================================
def is_signal_buyORsell(D0, D1, D2):
    if D0["close"] > D0["open"]:     # bullish D0 → BUY LIMIT
        return {
            "signal": "BUY LIMIT",
            "entry": D1["body_top"],
            "sl": D2["low"],
            "tp": D0["body_top"],
            "order_type": mt5.ORDER_TYPE_BUY_LIMIT
        }
    else:  # bearish → SELL LIMIT
        return {
            "signal": "SELL LIMIT",
            "entry": D1["body_bottom"],
            "sl": D2["high"],
            "tp": D0["body_bottom"],
            "order_type": mt5.ORDER_TYPE_SELL_LIMIT
        }


# =============================================
# MONITOR HIDDEN SL/TP
# =============================================
def check_hidden_sl_tp():
    positions = mt5.positions_get(symbol=symbol)

    if positions is None:
        return

    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        return

    bid = tick.bid
    ask = tick.ask

    for pos in positions:
        ticket = pos.ticket

        if ticket not in hidden_levels:
            continue

        info = hidden_levels[ticket]
        sl = info["sl"]
        tp = info["tp"]

        # BUY LIMIT turns into BUY position
        if pos.type == mt5.ORDER_TYPE_BUY:
            price = bid

            if price <= sl:
                print(f"🚨 HIDDEN SL HIT (BUY) → closing ticket {ticket}")
                mt5.order_send({
                    "action": mt5.TRADE_ACTION_DEAL,
                    "symbol": symbol,
                    "position": ticket,
                    "type": mt5.ORDER_TYPE_SELL,
                    "volume": pos.volume
                })
                del hidden_levels[ticket]

            elif price >= tp:
                print(f"🎯 HIDDEN TP HIT (BUY) → closing ticket {ticket}")
                mt5.order_send({
                    "action": mt5.TRADE_ACTION_DEAL,
                    "symbol": symbol,
                    "position": ticket,
                    "type": mt5.ORDER_TYPE_SELL,
                    "volume": pos.volume
                })
                del hidden_levels[ticket]

        # SELL LIMIT turns into SELL position
        elif pos.type == mt5.ORDER_TYPE_SELL:
            price = ask

            if price >= sl:
                print(f"🚨 HIDDEN SL HIT (SELL) → closing ticket {ticket}")
                mt5.order_send({
                    "action": mt5.TRADE_ACTION_DEAL,
                    "symbol": symbol,
                    "position": ticket,
                    "type": mt5.ORDER_TYPE_BUY,
                    "volume": pos.volume
                })
                del hidden_levels[ticket]

            elif price <= tp:
                print(f"🎯 HIDDEN TP HIT (SELL) → closing ticket {ticket}")
                mt5.order_send({
                    "action": mt5.TRADE_ACTION_DEAL,
                    "symbol": symbol,
                    "position": ticket,
                    "type": mt5.ORDER_TYPE_BUY,
                    "volume": pos.volume
                })
                del hidden_levels[ticket]


# =============================================
# MAIN LOOP
# =============================================
print("Menunggu setup…")

last_signal_time = None
MAX_TAIL_MULTIPLIER = 1.0
PIP = 0.10
BUFFER = 8 * PIP

while True:

    # Always monitor hidden SL/TP in every loop
    check_hidden_sl_tp()

    df = get_last_3()
    if df is None:
        continue

    D2 = df.iloc[0]
    D1 = df.iloc[1]
    D0 = df.iloc[2]

    if not is_rule1_acc(D0, D1, D2):
        continue
    if not is_rule2_acc(D0, D1, D2, MAX_TAIL_MULTIPLIER):
        continue

    order = is_signal_buyORsell(D0, D1, D2)
    signal = order["signal"]
    entry = float(order["entry"])
    raw_sl = float(order["sl"])
    tp = float(order["tp"])
    order_type = order["order_type"]

    # buffer added
    if order_type == mt5.ORDER_TYPE_BUY_LIMIT:
        sl = raw_sl - BUFFER
    else:
        sl = raw_sl + BUFFER

    print("\n=== SETUP TERDETEKSI ===")
    print("Time:", D0["time"])
    print("Signal:", signal)
    print("Entry:", entry)
    print("HIDDEN SL:", sl)
    print("HIDDEN TP:", tp)

    cancel_all_pending(symbol)
    send_order(order_type, entry, sl, tp)

    time.sleep(1)
