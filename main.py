import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime
import time

# 1. CONNECT MT5
if not mt5.initialize():
    print("MT5 gagal connect:", mt5.last_error())
    quit()

symbol = "XAUUSD"
mt5.symbol_select(symbol, True)
print("Real-time mode ON")

# 2. FUNCTION: GET LAST 3 CANDLES
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


# 3. SEND ORDER
def send_order(order_type, entry, sl, tp):
    volume = 0.02

    request = {
        "action": mt5.TRADE_ACTION_PENDING,
        "symbol": symbol,
        "volume": volume,
        "type": order_type,
        "price": entry,
        "sl": sl,
        "tp": tp,
        "deviation": 20,
        "magic": 20251124,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_RETURN
    }

    result = mt5.order_send(request)
    print("Order send result:", result)


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

# FUNCTION 3: CHECK RULE 1 (D0 > D1 & D2
def is_rule1_acc(D0, D1, D2):
    if not (D0["body_size"] > D1["body_size"] and D0["body_size"] > D2["body_size"]):
        return False

    if D1["body_size"] > D0["body_size"] * 0.5:
        return False

    if D2["body_size"] > D0["body_size"] * 0.5:
        return False

    return True

# FUNCTION 4: RULE BARU TAIL (TAIL ≤ 2× BODY D0
def is_rule2_acc(D0, D1, D2, MAX_TAIL_MULTIPLIER):
    max_tail = D0["body_size"] * MAX_TAIL_MULTIPLIER

    # D1 CHECK
    if D1["upper_tail"] > max_tail or D1["lower_tail"] > max_tail:
        print("Reject: Tail D1 > 2× body D0")
        return False

    # D2 CHECK
    if D2["upper_tail"] > max_tail or D2["lower_tail"] > max_tail:
        print("Reject: Tail D2 > 2× body D0")
        return False

    return True

# FUNCTION 5: BUY / SELL LOGI
def is_signal_buyORsell(D0, D1, D2):
    if D0["close"] > D0["open"]:     # bullish D0 → BUY LIMIT
        return {
            "signal": "BUY LIMIT",
            "entry": D1["body_top"],
            "sl": D2["low"],
            "tp": D0["body_top"],
            "order_type": mt5.ORDER_TYPE_BUY_LIMIT
        }

    else:                            # bearish D0 → SELL LIMIT
        return {
            "signal": "SELL LIMIT",
            "entry": D1["body_bottom"],
            "sl": D2["high"],
            "tp": D0["body_bottom"],
            "order_type": mt5.ORDER_TYPE_SELL_LIMIT
        }

# FUNCTION 1: TIDUR SAMPAI CANDLE BARU M3
def sleep_until_next_candle():
    while True:
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            print("Gagal mendapatkan tick MT5, retry...")
            time.sleep(1)
            continue

        # waktu server MT5
        now = datetime.fromtimestamp(tick.time)
        minute = now.minute

        # Candle M30 → next 00 atau 30
        if minute < 30:
            next_minute_mark = 30
            next_hour = now.hour
        else:
            next_minute_mark = 0
            next_hour = (now.hour + 1) % 24

        next_candle_time = now.replace(
            hour=next_hour,
            minute=next_minute_mark,
            second=0,
            microsecond=0
        )

        sleep_seconds = (next_candle_time - now).total_seconds()

        if sleep_seconds > 0:
            print(f"\nMT5 Server Time: {now}")
            print(f"Tidur {sleep_seconds:.0f} detik sampai candle berikutnya...")
            time.sleep(sleep_seconds)
            return
        
        # Jika sudah lewat beberapa milidetik → retry
        time.sleep(0.2)


print("Menunggu setup…")

last_signal_time = None
MAX_TAIL_MULTIPLIER = 1.0
PIP = 0.10          # 1 pip XAUUSD
BUFFER = 8 * PIP    # 8 pip buffer


while True:

    df = get_last_3()
    if df is None:
        continue

    D2 = df.iloc[0]
    D1 = df.iloc[1]
    D0 = df.iloc[2]

    #if last_signal_time == D0["time"]:
    #continue

    if not is_rule1_acc(D0, D1, D2):
        continue

    if not is_rule2_acc(D0, D1, D2, MAX_TAIL_MULTIPLIER):
        continue

    # dapat signal
    order = is_signal_buyORsell(D0, D1, D2)
    signal = order["signal"]
    entry = float(order["entry"])
    raw_sl = float(order["sl"])
    tp = float(order["tp"])
    order_type = order["order_type"]

    # tambahkan buffer ke SL agar ada ruang wick + buffer
    if order_type == mt5.ORDER_TYPE_BUY_LIMIT:
        sl = raw_sl - BUFFER
    else:  # SELL_LIMIT
        sl = raw_sl + BUFFER

    last_signal_time = D0["time"]

    print("\n=== SETUP TERDETEKSI ===")
    print("Time:", D0["time"])
    print("Signal:", signal)
    print("Entry:", entry)
    print("SL (with wick+8pip):", sl)
    print("TP:", tp)

    cancel_all_pending(symbol)
    send_order(order_type, entry, sl, tp)
