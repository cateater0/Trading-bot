import os
import time
import requests
from datetime import datetime

# ─────────────────────────────────────────
#  INSTELLINGEN — vul dit in op Railway
# ─────────────────────────────────────────
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "JOUW_TOKEN_HIER")
CHAT_ID        = os.environ.get("CHAT_ID", "6467324755")
SUPABASE_URL   = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY   = os.environ.get("SUPABASE_KEY", "")
COINS          = ["kPEPE", "kBONK", "FARTCOIN"]
CHECK_INTERVAL = 30
HL_API         = "https://api.hyperliquid.xyz/info"

# ─────────────────────────────────────────
#  STATE
# ─────────────────────────────────────────
cash         = 100.0  # startkapitaal
position     = None
price_hist   = {c: [] for c in COINS}
wins         = 0
closed       = 0
start_val    = 100.0
daily_log    = []
last_daily   = datetime.now().date()
last_morning = None
last_status  = None
tick_count   = 0

# ─────────────────────────────────────────
#  TELEGRAM
# ─────────────────────────────────────────
def send(msg: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": CHAT_ID, "text": msg, "parse_mode": "Markdown"}, timeout=10)
    except Exception as e:
        print(f"Telegram fout: {e}")

# ─────────────────────────────────────────
#  SUPABASE (database voor dashboard)
# ─────────────────────────────────────────
def supabase_request(table, method="POST", payload=None):
    if not SUPABASE_URL or not SUPABASE_KEY:
        return None
    url = f"{SUPABASE_URL}/rest/v1/{table}"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=minimal"
    }
    try:
        if method == "POST":
            requests.post(url, headers=headers, json=payload, timeout=10)
    except Exception as e:
        print(f"Supabase fout: {e}")

def save_status():
    """Schrijf live status naar Supabase voor dashboard."""
    port_val = cash
    upnl = 0
    if position:
        p = price_hist[position["coin"]][-1] if price_hist[position["coin"]] else position["entry"]
        if position["side"] == "long":
            upnl = position["qty"] * p - position["cost"]
            port_val = cash + position["qty"] * p
        else:
            upnl = position["borrowed"] - position["qty"] * p
            port_val = cash + upnl

    coins_data = {}
    for coin in COINS:
        prices = price_hist[coin]
        if prices:
            entry = {"price": prices[-1], "history": prices[-50:]}
            if len(prices) >= 25:
                e9 = ema(prices, 9)
                e21 = ema(prices, 21)
                r = rsi(prices, 14)
                m = momentum(prices, 5)
                entry.update({
                    "ema9": e9, "ema21": e21, "rsi": r, "momentum": m,
                    "trend": "bull" if e9 and e21 and e9 > e21 else "bear"
                })
            coins_data[coin] = entry

    payload = {
        "portfolio_value": port_val,
        "cash": cash,
        "position_side": position["side"] if position else None,
        "position_coin": position["coin"] if position else None,
        "position_entry": position["entry"] if position else None,
        "position_qty": position["qty"] if position else None,
        "unrealized_pnl": upnl,
        "total_trades": closed,
        "wins": wins,
        "coins_data": coins_data
    }
    supabase_request("bot_status", "POST", payload)

def save_trade(trade_type, coin, side, price, qty, profit=None, profit_pct=None, reason=None):
    payload = {
        "trade_type": trade_type,
        "coin": coin,
        "side": side,
        "price": price,
        "quantity": qty,
        "profit": profit,
        "profit_pct": profit_pct,
        "reason": reason
    }
    supabase_request("bot_trades", "POST", payload)

# ─────────────────────────────────────────
#  INDICATOREN
# ─────────────────────────────────────────
def ema(prices, n):
    if len(prices) < n:
        return None
    k = 2 / (n + 1)
    e = sum(prices[:n]) / n
    for p in prices[n:]:
        e = p * k + e * (1 - k)
    return e

def rsi(prices, period=14):
    if len(prices) < period + 1:
        return None
    sl = prices[-(period + 1):]
    gains = losses = 0
    for i in range(1, len(sl)):
        d = sl[i] - sl[i - 1]
        if d > 0:
            gains += d
        else:
            losses += abs(d)
    ag, al = gains / period, losses / period
    if al == 0:
        return 100
    return 100 - (100 / (1 + ag / al))

def momentum(prices, period=5):
    if len(prices) < period + 1:
        return None
    return (prices[-1] - prices[-1 - period]) / prices[-1 - period] * 100

# ─────────────────────────────────────────
#  STRATEGIE  (EMA 9/21 + RSI + Momentum)
# ─────────────────────────────────────────
def get_signal_details(coin):
    prices = price_hist[coin]
    if len(prices) < 25:
        return "flat", None, None, None, None, None

    e9       = ema(prices, 9)
    e21      = ema(prices, 21)
    e9_prev  = ema(prices[:-1], 9)
    e21_prev = ema(prices[:-1], 21)
    r        = rsi(prices, 14)
    mom      = momentum(prices, 5)

    if None in (e9, e21, e9_prev, e21_prev, r, mom):
        return "flat", e9, e21, r, mom, prices[-1]

    p = prices[-1]
    bull_trend = e9 > e21

    if position and position["coin"] == coin:
        if position["side"] == "long":
            pnl_pct = (p - position["entry"]) / position["entry"] * 100
        else:
            pnl_pct = (position["entry"] - p) / position["entry"] * 100
        if pnl_pct >= 4.0:
            return "close_tp", e9, e21, r, mom, p
        if pnl_pct <= -2.0:
            return "close_sl", e9, e21, r, mom, p

    if position:
        return "flat", e9, e21, r, mom, p

    if bull_trend and 45 < r < 72 and mom > 0.1:
        return "long", e9, e21, r, mom, p
    if not bull_trend and 28 < r < 55 and mom < -0.1:
        return "short", e9, e21, r, mom, p

    return "flat", e9, e21, r, mom, p

# ─────────────────────────────────────────
#  PRIJS OPHALEN
# ─────────────────────────────────────────
def fetch_prices():
    try:
        r = requests.post(HL_API, json={"type": "allMids"}, timeout=10)
        data = r.json()
        for coin in COINS:
            price = float(data.get(coin, 0))
            if price > 0:
                price_hist[coin].append(price)
                if len(price_hist[coin]) > 500:
                    price_hist[coin].pop(0)
        return True
    except Exception as e:
        print(f"Prijs fout: {e}")
        return False

# ─────────────────────────────────────────
#  TRADES UITVOEREN
# ─────────────────────────────────────────
def execute(coin, signal, e9, e21, r, mom, price):
    global cash, position, wins, closed, daily_log

    fee = 0.00035
    now = datetime.now().strftime("%H:%M:%S")

    if signal == "long" and position is None:
        qty  = (cash * 0.95) / price
        cost = qty * price * (1 + fee)
        position = {"coin": coin, "side": "long", "entry": price, "qty": qty, "cost": cost}
        cash -= cost
        msg = (
            f"*LONG GEOPEND* - {coin}\n"
            f"Tijd: {now}\n"
            f"Entry prijs: ${price:,.2f}\n"
            f"Hoeveelheid: {qty:.6f} {coin}\n"
            f"Take-profit: ${price*1.04:,.2f} (+4%)\n"
            f"Stop-loss: ${price*0.98:,.2f} (-2%)\n\n"
            f"Indicatoren:\n"
            f"EMA 9: ${e9:,.2f}\n"
            f"EMA 21: ${e21:,.2f}\n"
            f"RSI: {r:.1f}\n"
            f"Momentum: {mom:+.2f}%\n\n"
            f"Cash over: ${cash:,.2f}"
        )
        send(msg)
        daily_log.append(f"LONG {coin} @ ${price:,.0f}")
        save_trade("OPEN", coin, "long", price, qty)
        print(f"TRADE: LONG {coin} @ ${price:,.2f}")

    elif signal == "short" and position is None:
        qty      = (cash * 0.95) / price
        proceeds = qty * price * (1 - fee)
        position = {"coin": coin, "side": "short", "entry": price, "qty": qty, "borrowed": qty * price}
        cash    += proceeds
        msg = (
            f"*SHORT GEOPEND* - {coin}\n"
            f"Tijd: {now}\n"
            f"Entry prijs: ${price:,.2f}\n"
            f"Hoeveelheid: {qty:.6f} {coin}\n"
            f"Take-profit: ${price*0.96:,.2f} (-4%)\n"
            f"Stop-loss: ${price*1.02:,.2f} (+2%)\n\n"
            f"Indicatoren:\n"
            f"EMA 9: ${e9:,.2f}\n"
            f"EMA 21: ${e21:,.2f}\n"
            f"RSI: {r:.1f}\n"
            f"Momentum: {mom:+.2f}%\n\n"
            f"Cash over: ${cash:,.2f}"
        )
        send(msg)
        daily_log.append(f"SHORT {coin} @ ${price:,.0f}")
        save_trade("OPEN", coin, "short", price, qty)
        print(f"TRADE: SHORT {coin} @ ${price:,.2f}")

    elif signal in ("close_tp", "close_sl") and position and position["coin"] == coin:
        if position["side"] == "long":
            proceeds = position["qty"] * price * (1 - fee)
            profit   = proceeds - position["cost"]
            cash    += proceeds
        else:
            buy_back = position["qty"] * price * (1 + fee)
            profit   = position["borrowed"] - buy_back
            cash    -= buy_back
            cash    += position["borrowed"]

        if profit > 0:
            wins += 1
        closed += 1
        port_val = cash
        pnl_pct  = (profit / start_val) * 100
        reden    = "Take-profit" if signal == "close_tp" else "Stop-loss"
        emoji    = "WINST" if profit >= 0 else "VERLIES"

        msg = (
            f"*POSITIE GESLOTEN* - {coin}\n"
            f"Tijd: {now}\n"
            f"Reden: {reden}\n"
            f"Exit prijs: ${price:,.2f}\n"
            f"P&L: {'+'if profit>=0 else ''}${profit:,.2f} ({'+'if pnl_pct>=0 else ''}{pnl_pct:.2f}%) - {emoji}\n"
            f"Portfolio: ${port_val:,.2f}\n"
            f"Win rate: {wins}/{closed} ({wins/closed*100:.0f}%)"
        )
        send(msg)
        daily_log.append(f"SLUIT {coin} P&L: {'+'if profit>=0 else ''}${profit:,.2f}")
        save_trade("CLOSE", coin, position["side"], price, position["qty"], profit, pnl_pct, reden)
        position = None
        print(f"TRADE GESLOTEN: {coin} P&L ${profit:,.2f}")

# ─────────────────────────────────────────
#  STATUS UPDATE (elke 10 ticks = ~5 min)
# ─────────────────────────────────────────
def status_update():
    global last_status

    now   = datetime.now().strftime("%H:%M")
    lines = []

    for coin in COINS:
        prices = price_hist[coin]
        if len(prices) < 25:
            punten = len(prices)
            lines.append(f"{coin}: data verzamelen ({punten}/25 punten)")
            continue

        sig, e9, e21, r, mom, price = get_signal_details(coin)

        trend   = "Bull" if e9 and e21 and e9 > e21 else "Bear"
        rsi_str = f"{r:.0f}" if r else "-"
        mom_str = f"{mom:+.2f}%" if mom else "-"

        if position and position["coin"] == coin:
            p = prices[-1]
            if position["side"] == "long":
                upnl = (p - position["entry"]) / position["entry"] * 100
            else:
                upnl = (position["entry"] - p) / position["entry"] * 100
            pos_str = f"OPEN {position['side'].upper()} ({upnl:+.1f}%)"
            lines.append(
                f"*{coin}* ${price:,.0f} - {pos_str}\n"
                f"  Trend: {trend} | RSI: {rsi_str} | Mom: {mom_str}"
            )
        else:
            sig_str = "LONG signaal!" if sig == "long" else "SHORT signaal!" if sig == "short" else "Wacht"
            lines.append(
                f"*{coin}* ${price:,.0f} - {sig_str}\n"
                f"  Trend: {trend} | RSI: {rsi_str} | Mom: {mom_str}"
            )

    port_val = cash
    if position:
        p = price_hist[position["coin"]][-1] if price_hist[position["coin"]] else position["entry"]
        if position["side"] == "long":
            port_val = cash + position["qty"] * p
        else:
            port_val = cash + (position["borrowed"] - position["qty"] * p)

    total_pnl = port_val - start_val

    msg = (
        f"Status update - {now}\n\n"
        + "\n\n".join(lines) +
        f"\n\nPortfolio: ${port_val:,.2f} ({'+'if total_pnl>=0 else ''}${total_pnl:.2f})\n"
        f"Trades: {closed} | Win rate: {f'{wins/closed*100:.0f}%' if closed>0 else '-'}"
    )

    if msg != last_status:
        send(msg)
        last_status = msg

# ─────────────────────────────────────────
#  OCHTEND UPDATE (09:00)
# ─────────────────────────────────────────
def morning_update():
    global last_morning

    port_val = cash
    if position:
        p = price_hist[position["coin"]][-1] if price_hist[position["coin"]] else position["entry"]
        if position["side"] == "long":
            port_val = cash + position["qty"] * p
        else:
            port_val = cash + (position["borrowed"] - position["qty"] * p)

    total_pnl = port_val - start_val
    pnl_pct   = (total_pnl / start_val) * 100
    pos_str   = f"{position['side'].upper()} {position['coin']} (entry ${position['entry']:,.2f})" if position else "Geen open positie"

    prijzen = []
    for coin in COINS:
        if price_hist[coin]:
            prijzen.append(f"{coin}: ${price_hist[coin][-1]:,.0f}")

    msg = (
        f"Goedemorgen! Dagelijkse update\n"
        f"{datetime.now().strftime('%d-%m-%Y')}\n\n"
        f"Portfolio: ${port_val:,.2f}\n"
        f"Totaal P&L: {'+'if total_pnl>=0 else ''}${total_pnl:,.2f} ({'+'if pnl_pct>=0 else ''}{pnl_pct:.1f}%)\n"
        f"Huidige positie: {pos_str}\n"
        f"Trades gesloten: {closed}\n"
        f"Win rate: {f'{wins/closed*100:.0f}%' if closed>0 else 'Nog geen'}\n\n"
        f"Huidige prijzen:\n" + "\n".join(prijzen) + "\n\n"
        f"Bot is actief en bewaakt de markt."
    )
    send(msg)
    last_morning = datetime.now().date()

# ─────────────────────────────────────────
#  DAGELIJKSE SAMENVATTING (20:00)
# ─────────────────────────────────────────
def daily_summary():
    global daily_log, last_daily

    port_val = cash
    if position:
        p = price_hist[position["coin"]][-1] if price_hist[position["coin"]] else position["entry"]
        if position["side"] == "long":
            port_val = cash + position["qty"] * p
        else:
            port_val = cash + (position["borrowed"] - position["qty"] * p)

    total_pnl    = port_val - start_val
    trades_today = "\n".join(daily_log) if daily_log else "Geen trades vandaag"

    msg = (
        f"Dagelijkse samenvatting\n"
        f"{datetime.now().strftime('%d-%m-%Y')}\n\n"
        f"Portfolio: ${port_val:,.2f}\n"
        f"Totaal P&L: {'+'if total_pnl>=0 else ''}${total_pnl:,.2f}\n"
        f"Trades gesloten: {closed}\n"
        f"Win rate: {f'{wins}/{closed} ({wins/closed*100:.0f}%)' if closed>0 else 'Nog geen'}\n\n"
        f"Trades vandaag:\n{trades_today}"
    )
    send(msg)
    daily_log  = []
    last_daily = datetime.now().date()

# ─────────────────────────────────────────
#  HOOFDLOOP
# ─────────────────────────────────────────
def main():
    global tick_count

    print("Bot gestart")
    send(
        f"Bot gestart!\n"
        f"Startkapitaal: ${cash:,.2f}\n"
        f"Coins: {', '.join(COINS)}\n"
        f"Strategie: EMA 9/21 + RSI 14 + Momentum\n"
        f"Take-profit: +4% | Stop-loss: -2%\n"
        f"Status update: elke 5 minuten\n"
        f"Ochtend update: 09:00\n"
        f"Avond samenvatting: 20:00\n\n"
        f"Wacht op eerste signaal..."
    )

    while True:
        try:
            now = datetime.now()

            if now.hour == 9 and now.minute == 0 and last_morning != now.date():
                morning_update()

            if now.hour == 20 and now.minute == 0 and last_daily != now.date():
                daily_summary()

            ok = fetch_prices()
            if ok:
                for coin in COINS:
                    sig, e9, e21, r, mom, price = get_signal_details(coin)
                    if sig != "flat" and price:
                        execute(coin, sig, e9, e21, r, mom, price)

                tick_count += 1
                if tick_count % 10 == 0:
                    status_update()

                # Schrijf live status naar Supabase voor dashboard
                save_status()

            port_val = cash
            if position:
                p = price_hist[position["coin"]][-1] if price_hist[position["coin"]] else position["entry"]
                if position["side"] == "long":
                    port_val = cash + position["qty"] * p
                else:
                    port_val = cash + (position["borrowed"] - position["qty"] * p)
            print(f"[{now.strftime('%H:%M:%S')}] Portfolio: ${port_val:,.2f} | Positie: {position['side'].upper()+' '+position['coin'] if position else 'Flat'} | Trades: {closed} | Tick: {tick_count}")

        except Exception as e:
            print(f"Fout in hoofdloop: {e}")

        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
