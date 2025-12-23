import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime
import time
import requests
import threading
from decimal import Decimal, ROUND_HALF_UP

# ==========================================================
# DISCORD WEBHOOK
# ==========================================================
DISCORD_WEBHOOK = "https://discord.com/api/webhooks/1448959796421066783/mTVVXtvX6IkiSDRbexRMn6eweUXJ90MXeQshNa0OODE4vYrM4kDCrQU7plg9KJbG6j_c"

def send_discord_message(content: str):
    try:
        requests.post(DISCORD_WEBHOOK, json={"content": content}, timeout=10)
    except Exception as e:
        print("Discord error:", e)

# ==========================================================
# MT5 INIT
# ==========================================================
print("Connecting to MetaTrader 5...")
if not mt5.initialize():
    print("❌ MT5 gagal connect:", mt5.last_error())
    quit()
print("✔ MT5 berhasil connect\n")

terminal_info = mt5.terminal_info()
if not terminal_info or not terminal_info.trade_allowed:
    print("❌ Auto Trading OFF")
    quit()

account_info = mt5.account_info()
print(f"Login: {account_info.login} | Server: {account_info.server}")

# ==========================================================
# CONFIG
# ==========================================================
symbol = "XAUUSD"

SLAVE_WEBHOOKS = [
    "http://topfrag746.mtvps.net:11241/webhook",
    "http://topfrag746.mtvps.net:11242/webhook",
    "http://topfrag746.mtvps.net:11243/webhook",
]

MASTER_SECRET = "TopFrag?!"
MAGIC = 86421357

mt5.symbol_select(symbol, True)

hidden_levels = {}
last_signal_time = None

PIP = 0.10
BUFFER = 8 * PIP
MAX_TAIL_MULTIPLIER = 2.0

# ==========================================================
# SLAVE BROADCAST
# ==========================================================
def send_to_slave(payload):
    payload["secret"] = MASTER_SECRET

    for i, url in enumerate(SLAVE_WEBHOOKS, start=1):
        try:
            r = requests.post(url, json=payload, timeout=5)
            print(f"➡ Slave #{i}:", r.status_code, r.text)
        except Exception as e:
            print(f"❌ Slave #{i} error:", e)

# ==========================================================
# HELPERS
# ==========================================================
def calculate_volume():
    bal = Decimal(str(mt5.account_info().balance))
    vol = (bal / Decimal("10000") / Decimal("2")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )
    return max(float(vol), 0.01)

def get_last_3():
    data = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M30, 1, 3)
    if data is None:
        return None

    df = pd.DataFrame(data)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    df["body_top"] = df[["open", "close"]].max(axis=1)
    df["body_bottom"] = df[["open", "close"]].min(axis=1)
    df["body_size"] = (df["close"] - df["open"]).abs()
    df["upper_tail"] = df["high"] - df["body_top"]
    df["lower_tail"] = df["body_bottom"] - df["low"]
    return df

# ==========================================================
# RULES
# ==========================================================
def is_rule1_acc(D0, D1, D2):
    return (
        D0["body_size"] > D1["body_size"] and
        D0["body_size"] > D2["body_size"] and
        D1["body_size"] <= D0["body_size"] * 0.5 and
        D2["body_size"] <= D0["body_size"] * 0.5
    )

def is_rule2_acc(D0, D1, D2):
    max_tail = D0["body_size"] * MAX_TAIL_MULTIPLIER
    return (
        D1["upper_tail"] <= max_tail and D1["lower_tail"] <= max_tail and
        D2["upper_tail"] <= max_tail and D2["lower_tail"] <= max_tail
    )

def is_signal_buyORsell(D0, D1, D2):
    if D0["close"] > D0["open"]:
        return mt5.ORDER_TYPE_BUY_LIMIT, D1["body_top"], min(D1["low"], D2["low"]), D0["body_top"]
    else:
        return mt5.ORDER_TYPE_SELL_LIMIT, D1["body_bottom"], max(D1["high"], D2["high"]), D0["body_bottom"]

# ==========================================================
# ORDERS
# ==========================================================
def cancel_all_pending():
    orders = mt5.orders_get(symbol=symbol)
    if not orders:
        return
    for o in orders:
        if o.magic == MAGIC:
            mt5.order_send({"action": mt5.TRADE_ACTION_REMOVE, "order": o.ticket})

def send_order(order_type, entry, sl, tp):
    volume = calculate_volume()
    result = mt5.order_send({
        "action": mt5.TRADE_ACTION_PENDING,
        "symbol": symbol,
        "volume": volume,
        "type": order_type,
        "price": entry,
        "magic": MAGIC,
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_RETURN
    })

    if result and result.order:
        hidden_levels[result.order] = {"sl": sl, "tp": tp}
        send_to_slave({
            "action": "OPEN",
            "symbol": symbol,
            "type": "BUY_LIMIT" if order_type == mt5.ORDER_TYPE_BUY_LIMIT else "SELL_LIMIT",
            "entry": entry,
            "master_ticket": result.order
        })

# ==========================================================
# HIDDEN SL/TP THREAD
# ==========================================================
def hidden_sl_tp_loop():
    while True:
        positions = mt5.positions_get(symbol=symbol)
        tick = mt5.symbol_info_tick(symbol)
        if not positions or not tick:
            time.sleep(0.5)
            continue

        for pos in positions:
            if pos.magic != MAGIC or pos.ticket not in hidden_levels:
                continue

            sl = hidden_levels[pos.ticket]["sl"]
            tp = hidden_levels[pos.ticket]["tp"]

            price = tick.bid if pos.type == mt5.POSITION_TYPE_BUY else tick.ask

            if (pos.type == mt5.POSITION_TYPE_BUY and (price <= sl or price >= tp)) or \
               (pos.type == mt5.POSITION_TYPE_SELL and (price >= sl or price <= tp)):

                mt5.order_send({
                    "action": mt5.TRADE_ACTION_DEAL,
                    "symbol": symbol,
                    "position": pos.ticket,
                    "type": mt5.ORDER_TYPE_SELL if pos.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY,
                    "volume": pos.volume
                })

                send_to_slave({
                    "action": "CLOSE",
                    "master_ticket": pos.ticket
                })

                del hidden_levels[pos.ticket]

        time.sleep(0.5)

threading.Thread(target=hidden_sl_tp_loop, daemon=True).start()

# ==========================================================
# MAIN LOOP
# ==========================================================
print("🚀 Master running...")

while True:
    df = get_last_3()
    if df is None:
        time.sleep(1)
        continue

    D2, D1, D0 = df.iloc[0], df.iloc[1], df.iloc[2]

    if last_signal_time == D0["time"]:
        time.sleep(1)
        continue

    if not is_rule1_acc(D0, D1, D2):
        continue
    if not is_rule2_acc(D0, D1, D2):
        continue

    order_type, entry, raw_sl, tp = is_signal_buyORsell(D0, D1, D2)
    sl = raw_sl - BUFFER if order_type == mt5.ORDER_TYPE_BUY_LIMIT else raw_sl + BUFFER

    last_signal_time = D0["time"]

    cancel_all_pending()
    send_to_slave({"action": "CANCEL_PENDING", "symbol": symbol})
    hidden_levels.clear()

    send_order(order_type, float(entry), float(sl), float(tp))

    send_discord_message(
        f"📢 SETUP\n{symbol}\n{D0['time']}\nEntry: {entry}\nSL: {sl}\nTP: {tp}"
    )

    time.sleep(1)
