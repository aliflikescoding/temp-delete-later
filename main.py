import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime

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
    volume = 0.02  # fixed 0.02 lot

    # --- Check margin BEFORE sending the order ---
    margin_check = mt5.order_calc_margin(order_type, symbol, volume, entry)

    if margin_check is None:
        print("Margin check failed:", mt5.last_error())
        return
    
    print("Margin required:", margin_check)

    # SAFE LIMIT = 10% of balance
    balance = mt5.account_info().balance
    max_margin = balance * 0.10  # 10%

    if margin_check > max_margin:
        print(f"❌ Margin too high ({margin_check}), max allowed = {max_margin}")
        print("Trade NOT executed to protect account.")
        return

    # --- SEND ORDER ---
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


print("Menunggu setup…")

last_signal_time = None
MAX_TAIL_MULTIPLIER = 2
PIP = 0.10          # 1 pip XAUUSD
BUFFER = 8 * PIP    # 8 pip buffer


while True:
    df = get_last_3()
    if df is None:
        continue

    D2 = df.iloc[0]
    D1 = df.iloc[1]
    D0 = df.iloc[2]

    if last_signal_time == D0["time"]:
        continue

    # RULE 1
    if not (D0["body_size"] > D1["body_size"] and D0["body_size"] > D2["body_size"]):
        continue

    # RULE 2
    if D1["body_size"] > D0["body_size"] * 0.5: continue
    if D2["body_size"] > D0["body_size"] * 0.5: continue

    # RULE 3 tail tidak boleh panjang
    if (D1["upper_tail"] > D1["body_size"] * MAX_TAIL_MULTIPLIER) or \
       (D1["lower_tail"] > D1["body_size"] * MAX_TAIL_MULTIPLIER):
        print("Reject: Tail D1 terlalu panjang")
        continue

    if (D2["upper_tail"] > D2["body_size"] * MAX_TAIL_MULTIPLIER) or \
       (D2["lower_tail"] > D2["body_size"] * MAX_TAIL_MULTIPLIER):
        print("Reject: Tail D2 terlalu panjang")
        continue


    #   HITUNG SL BARU (RULE SL)
    # BUY = SL di tail terendah antara D1 & D2
    if D0["close"] > D0["open"]:
        signal = "BUY LIMIT"
        entry = D1["body_top"]
        tp = D0["body_top"]
        order_type = mt5.ORDER_TYPE_BUY_LIMIT

        # SL = lowest wick (D1.low or D2.low)
        raw_sl = min(D1["low"], D2["low"])
        sl = raw_sl - BUFFER

    # SELL = SL di tail tertinggi antara D1 & D2
    else:
        signal = "SELL LIMIT"
        entry = D1["body_bottom"]
        tp = D0["body_bottom"]
        order_type = mt5.ORDER_TYPE_SELL_LIMIT

        # SL = highest wick (D1.high or D2.high)
        raw_sl = max(D1["high"], D2["high"])
        sl = raw_sl + BUFFER


    last_signal_time = D0["time"]

    print("\n=== SETUP TERDETEKSI ===")
    print("Time:", D0["time"])
    print("Signal:", signal)
    print("Entry:", entry)
    print("SL (with wick+5pip):", sl)
    print("TP:", tp)

    cancel_all_pending(symbol)
    send_order(order_type, entry, sl, tp)
