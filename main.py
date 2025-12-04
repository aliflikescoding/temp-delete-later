import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime, timedelta
import time

# ==========================================================
# FUNCTION: TIDUR SAMPAI CANDLE M30 BERIKUTNYA
# ==========================================================
def sleep_until_next_candle():
    now = datetime.now()

    minute = now.minute

    # tentukan target 00 atau 30 berikutnya
    if minute < 30:
        next_minute_mark = 30
    else:
        next_minute_mark = 60  # berarti ke menit 00 jam berikutnya

    # buat timestamp target
    next_candle_time = now.replace(
        minute=next_minute_mark % 60,
        second=0,
        microsecond=0
    )

    # kalau next_minute_mark 60, berarti jamnya naik 1
    if next_minute_mark == 60:
        next_candle_time += timedelta(hours=1)

    # hitung detik
    sleep_seconds = (next_candle_time - now).total_seconds()

    print(f"Tidur {sleep_seconds:.0f} detik sampai candle M30 baru...\n")
    time.sleep(sleep_seconds)

# ==========================================================
# CONNECT MT5
# ==========================================================
if not mt5.initialize():
    print("MT5 gagal connect:", mt5.last_error())
    quit()

symbol = "XAUUSD"
mt5.symbol_select(symbol, True)
print("Real-time mode ON\n")

# ==========================================================
# GET LAST 3 CANDLES
# ==========================================================
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

# ==========================================================
# ORDER SENDER
# ==========================================================
def send_order(order_type, entry, sl, tp):
    volume = 0.01

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

# ==========================================================
# MAIN BOT LOOP — WITH CANDLE WAIT
# ==========================================================

print("Menunggu setup…\n")

last_signal_time = None
PIP = 0.10          # 1 pip XAUUSD
BUFFER = 8 * PIP    # 8 pip buffer

while True:

    # 🔥 BOT TIDUR SAMPAI CANDLE BARU
    sleep_until_next_candle()

    # Setelah bangun → candle M30 baru sudah selesai
    df = get_last_3()
    if df is None:
        continue

    D2 = df.iloc[0]
    D1 = df.iloc[1]
    D0 = df.iloc[2]

    # Mencegah double signal
    if last_signal_time == D0["time"]:
        continue

    # RULE 1
    if not (D0["body_size"] > D1["body_size"] and D0["body_size"] > D2["body_size"]):
        print(f"[{D0['time']}] Reject: RULE 1 gagal (D0 body tidak terbesar)")
        continue

    # RULE 2
    if D1["body_size"] > D0["body_size"] * 0.5:
        print(f"[{D0['time']}] Reject: RULE 2 gagal (D1 body terlalu besar)")
        continue

    if D2["body_size"] > D0["body_size"] * 0.5:
        print(f"[{D0['time']}] Reject: RULE 2 gagal (D2 body terlalu besar)")
        continue

    # ===== HITUNG SIGNAL =====
    if D0["close"] > D0["open"]:
        signal = "BUY LIMIT"
        entry = D1["body_top"]
        tp = D0["body_top"]
        order_type = mt5.ORDER_TYPE_BUY_LIMIT
        sl = min(D1["low"], D2["low"]) - BUFFER
    else:
        signal = "SELL LIMIT"
        entry = D1["body_bottom"]
        tp = D0["body_bottom"]
        order_type = mt5.ORDER_TYPE_SELL_LIMIT
        sl = max(D1["high"], D2["high"]) + BUFFER

    last_signal_time = D0["time"]

    print("\n=== SETUP TERDETEKSI ===")
    print("Time:", D0["time"])
    print("Signal:", signal)
    print("Entry:", entry)
    print("SL:", sl)
    print("TP:", tp, "\n")

    cancel_all_pending(symbol)
    send_order(order_type, entry, sl, tp)
