import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime
import time
import requests
import threading
from decimal import Decimal, ROUND_HALF_UP


# 0. DISCORD WEBHOOK
DISCORD_WEBHOOK = "https://discord.com/api/webhooks/1448959796421066783/mTVVXtvX6IkiSDRbexRMn6eweUXJ90MXeQshNa0OODE4vYrM4kDCrQU7plg9KJbG6j_c"

def send_discord_message(content: str):
    """Kirim pesan notifikasi ke Discord."""
    try:
        data = {"content": content}
        r = requests.post(DISCORD_WEBHOOK, json=data, timeout=10)
        print("Discord Webhook status:", r.status_code)
    except Exception as e:
        print("Gagal kirim Discord:", e)

print("Connecting to MetaTrader 5...")

# 1. CONNECT MT5
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

SLAVE_WEBHOOKS = [
    "http://http://127.0.0.1:8000/webhook",
]

MASTER_SECRET = "TopFrag?!"
MAGIC = 86421357

mt5.symbol_select(symbol, True)
print("Real-time mode ON")

# Storage for hidden SL/TP
hidden_levels = {}   # {ticket: {sl, tp, order_type}}

# ===============================
# SLAVE WEBHOOK
# ===============================
def send_to_slave(payload):
    payload["secret"] = MASTER_SECRET

    for i, url in enumerate(SLAVE_WEBHOOKS, start=1):
        try:
            r = requests.post(url, json=payload, timeout=5)
            print(f"➡ Slave #{i}:", r.status_code, r.text)
        except Exception as e:
            print(f"❌ Slave #{i} error:", e)

# 2. FUNCTION: GET LAST 3 CANDLES
def get_last_3():
    data = mt5.copy_rates_from_pos(symbol, mt5.TIMEFRAME_M30, 1, 3)
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

def calculate_volume():
    account = mt5.account_info()
    if account is None:
        raise Exception("Gagal mengambil account info MT5")

    balance = Decimal(str(account.balance))
    raw_volume = (balance / Decimal("10000")) / Decimal("2")
    volume = raw_volume.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    if volume < Decimal("0.01"):
        volume = Decimal("0.01")

    return float(volume)


# 3. SEND ORDER
def send_order(order_type, entry, sl, tp):
    volume = calculate_volume()

    request = {
        "action": mt5.TRADE_ACTION_PENDING,
        "symbol": symbol,
        "volume": volume,
        "type": order_type,
        "price": entry,
        "deviation": 20,
        "magic": MAGIC,
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

        order_type_str = (
            "BUY_LIMIT"
            if order_type == mt5.ORDER_TYPE_BUY_LIMIT
            else "SELL_LIMIT"
        )

        send_to_slave({
            "action": "OPEN",
            "symbol": symbol,
            "type": order_type_str,
            "entry": entry,
            "master_ticket": result.order
        })


    return result


def cancel_all_pending(symbol):
    orders = mt5.orders_get(symbol=symbol)
    if orders:
        print(f"Mengecek pending order (filter magic)...")
        for o in orders:
            if o.magic != MAGIC:
                continue   # 🔒 PROTEKSI MAGIC

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
        print("D0 tidak paling besar.")
        return False

    if D1["body_size"] > D0["body_size"] * 0.5:
        print("D0 tidak 2X D1.")
        return False

    if D2["body_size"] > D0["body_size"] * 0.5:
        print("D0 tidak 2X D2.")
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
        sl_value = min(D1["low"], D2["low"])
        return {
            "signal": "BUY LIMIT",
            "entry": D1["body_top"],
            "sl": sl_value,
            "tp": D0["body_top"],
            "order_type": mt5.ORDER_TYPE_BUY_LIMIT
        }

    else:                            # bearish D0 → SELL LIMIT
        sl_value = max(D1["high"], D2["high"])
        return {
            "signal": "SELL LIMIT",
            "entry": D1["body_bottom"],
            "sl": sl_value,
            "tp": D0["body_bottom"],
            "order_type": mt5.ORDER_TYPE_SELL_LIMIT
        }
    
# =============================================
# CLEAR THE HIDDEN LEVEL OBJECT
# =============================================
def clear_hidden_levels():
    count = len(hidden_levels)
    hidden_levels.clear()
    print(f"🧹 Cleared {count} hidden SL/TP entries.")

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
        if pos.magic != MAGIC:
            continue   # 🔒 PROTEKSI MAGIC

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
                send_to_slave({
                        "action": "CLOSE",
                        "master_ticket": pos.ticket
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
                send_to_slave({
                        "action": "CLOSE",
                        "master_ticket": pos.ticket
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
                send_to_slave({
                        "action": "CLOSE",
                        "master_ticket": pos.ticket
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
                send_to_slave({
                        "action": "CLOSE",
                        "master_ticket": pos.ticket
                    })
                
                del hidden_levels[ticket]

def hidden_sl_tp_loop():
    print("🔄 Hidden SL/TP monitor thread started")
    while True:
        try:
            check_hidden_sl_tp()
            time.sleep(0.5)  # aman, tidak DDOS
        except Exception as e:
            print("Hidden SL/TP error:", e)
            time.sleep(1)


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

# proteksi sebelum sleep lagi
def time_protection(symbol, last_candle_time, max_wait=30):
    print(f"[{last_candle_time}] Candle masih sama, menunggu candle baru...")

    waited = 0

    while waited < max_wait:
        time.sleep(1)
        waited += 1

        print(f"  - Cek ulang ({waited}s)...")

        df_new = get_last_3()
        if df_new is None:
            print("  - Gagal ambil candle, retry...")
            continue

        D0_new = df_new.iloc[2]

        # Jika candle sudah berubah → return data baru
        if D0_new["time"] != last_candle_time:
            print(f"  ✔ Candle baru terdeteksi: {D0_new['time']}")
            return df_new  # return seluruh df baru

    # timeout
    print("❌ Timeout 30 detik: Tidak ada candle baru muncul.")
    return None


print("Menunggu setup…")

last_signal_time = None
MAX_TAIL_MULTIPLIER = 2.0
PIP = 0.10          # 1 pip XAUUSD
BUFFER = 8 * PIP    # 8 pip buffer

sl_tp_thread = threading.Thread(
    target=hidden_sl_tp_loop,
    daemon=True
)
sl_tp_thread.start()


while True:
    sleep_until_next_candle()

    df = get_last_3()
    if df is None:
        print("Gagal ambil candle")
        continue

    D2 = df.iloc[0]
    D1 = df.iloc[1]
    D0 = df.iloc[2]

    print("\n=== CEK URUTAN CANDLE DARI MT5 ===")
    print("D0 =", D0['time'])
    print("D1 =", D1['time'])
    print("D2 =", D2['time'])


    # CEK DUPLIKASI SIGNAL
    if last_signal_time == D0["time"]:
        # time protection: ngecek lagi setiap 1 detik selama 30 detik
        df_new = time_protection(symbol, last_signal_time)
    
        # kalau gagal dapat candle baru
        if df_new is None:
            print("selaman time protection berlangsung, tidak meneukan candle baru. tidur lagi sampai .00 or .30. SEGERA LAKUKAN PENGECEKAN CODE!!! ADA KEMUNGKINAN KESALAHAN")
            continue
    
        # update D0, D1, D2 ke candle baru
        D2 = df_new.iloc[0]
        D1 = df_new.iloc[1]
        D0 = df_new.iloc[2]


    if not is_rule1_acc(D0, D1, D2):
        continue

    print("Rule 1 ACC: D0 paling besar dan 2X dari D1 dan D2")

    if not is_rule2_acc(D0, D1, D2, MAX_TAIL_MULTIPLIER):
        continue

    print("Rule 2 ACC: body D0 2X lebih besar dari upper/lower tail D1 dan D2. otw send order!!!")

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

    send_discord_message(
        f"📢 *Setup Terdeteksi*\n"
        f"Symbol: *{symbol}*\n"
        f"Type: *{signal}*\n"
        f"Time: {D0['time']}\n"
        f"Entry: {entry}\n"
        f"SL: {sl}\n"
        f"TP: {tp}"
    )


    print("\n=== SETUP TERDETEKSI ===")
    print("Time:", D0["time"])
    print("Signal:", signal)
    print("Entry:", entry)
    print("HIDDEN SL:", sl)
    print("HIDDEN TP:", tp)

    cancel_all_pending(symbol)
    send_to_slave({
        "action": "CANCEL_PENDING",
        "symbol": symbol
    })
    clear_hidden_levels()
    send_order(order_type, entry, sl, tp)
