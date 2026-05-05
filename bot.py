import os
import time
import requests
from datetime import datetime

# ─────────────────────────────────────────
#  INSTELLINGEN — vul dit in op Railway
# ─────────────────────────────────────────
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "JOUW_TOKEN_HIER")
CHAT_ID        = os.environ.get("CHAT_ID", "6467324755")
COINS          = ["BTC", "ETH", "SOL"]   # welke coins de bot volgt
CHECK_INTERVAL = 30                        # seconden tussen checks
HL_API         = "https://api.hyperliquid.xyz/info"

# ─────────────────────────────────────────
#  STATE
# ─────────────────────────────────────────
cash       = 100.0
position   = None   # { coin, side, entry, qty, cost }
price_hist = {c: [] for c in COINS}
wins       = 0
closed     = 0
start_val  = 100.0
daily_log  = []
last_daily = datetime.now().date()

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
def get_signal(coin):
    prices = price_hist[coin]
    if len(prices) < 25:
        return "flat"

    e9      = ema(prices, 9)
    e21     = ema(prices, 21)
    e9_prev = ema(prices[:-1], 9)
    e21_prev= ema(prices[:-1], 21)
    r       = rsi(prices, 14)
    mom     = momentum(prices, 5)

    if None in (e9, e21, e9_prev, e21_prev, r, mom):
        return "flat"

    bull_trend = e9 > e21

    # Check stop-loss / take-profit voor open positie
    if position and position["coin"] == coin:
        p = prices[-1]
        if position["side"] == "long":
            pnl_pct = (p - position["entry"]) / position["entry"] * 100
        else:
            pnl_pct = (position["entry"] - p) / position["entry"] * 100
        if pnl_pct >= 4.0:
            return "close_tp"
        if pnl_pct <= -2.0:
            return "close_sl"

    if position:
        return "flat"   # al in een positie voor andere coin

    # Long signaal: bull trend + RSI niet overbought + positief momentum
    if bull_trend and 45 < r < 72 and mom > 0.1:
        return "long"

    # Short signaal: bear trend + RSI niet oversold + negatief momentum
    if not bull_trend and 28 < r < 55 and mom < -0.1:
        return "short"

    return "flat"

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
def execute(coin, signal):
    global cash, position, wins, closed, daily_log

    price = price_hist[coin][-1]
    fee   = 0.00035
    now   = datetime.now().strftime("%H:%M:%S")

    if signal == "long" and position is None:
        qty  = (cash * 0.95) / price
        cost = qty * price * (1 + fee)
        position = {"coin": coin, "side": "long", "entry": price, "qty": qty, "cost": cost}
        cash -= cost
        msg = (
            f"🟢 *LONG GEOPEND* — {coin}\n"
            f"⏰ {now}\n"
            f"💰 Entry prijs: ${price:,.2f}\n"
            f"📦 Hoeveelheid: {qty:.4f} {coin}\n"
            f"🎯 Take-profit: ${price*1.04:,.2f} (+4%)\n"
            f"🛑 Stop-loss: ${price*0.98:,.2f} (-2%)\n"
            f"💵 Cash over: ${cash:,.2f}"
        )
        send(msg)
        daily_log.append(f"LONG {coin} @ ${price:,.0f}")
        print(msg)

    elif signal == "short" and position is None:
        qty      = (cash * 0.95) / price
        proceeds = qty * price * (1 - fee)
        position = {"coin": coin, "side": "short", "entry": price, "qty": qty, "borrowed": qty * price}
        cash    += proceeds
        msg = (
            f"🔴 *SHORT GEOPEND* — {coin}\n"
            f"⏰ {now}\n"
            f"💰 Entry prijs: ${price:,.2f}\n"
            f"📦 Hoeveelheid: {qty:.4f} {coin}\n"
            f"🎯 Take-profit: ${price*0.96:,.2f} (-4%)\n"
            f"🛑 Stop-loss: ${price*1.02:,.2f} (+2%)\n"
            f"💵 Cash over: ${cash:,.2f}"
        )
        send(msg)
        daily_log.append(f"SHORT {coin} @ ${price:,.0f}")
        print(msg)

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
        pnl_pct  = (profit / 10000) * 100
        reden    = "✅ Take-profit" if signal == "close_tp" else "🛑 Stop-loss"
        emoji    = "📈" if profit >= 0 else "📉"

        msg = (
            f"{emoji} *POSITIE GESLOTEN* — {coin}\n"
            f"⏰ {now}\n"
            f"📌 {reden}\n"
            f"💰 Exit prijs: ${price:,.2f}\n"
            f"💵 P&L: {'+'if profit>=0 else ''}${profit:,.2f} ({'+'if pnl_pct>=0 else ''}{pnl_pct:.2f}%)\n"
            f"🏦 Portfolio: ${port_val:,.2f}\n"
            f"📊 Win rate: {wins}/{closed} ({wins/closed*100:.0f}%)"
        )
        send(msg)
        daily_log.append(f"SLUIT {coin} P&L: {'+'if profit>=0 else ''}${profit:,.0f}")
        position = None
        print(msg)

# ─────────────────────────────────────────
#  DAGELIJKSE SAMENVATTING
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

    total_pnl = port_val - start_val
    trades_today = "\n".join(daily_log) if daily_log else "Geen trades vandaag"
    msg = (
        f"📋 *DAGELIJKSE SAMENVATTING*\n"
        f"📅 {datetime.now().strftime('%d-%m-%Y')}\n\n"
        f"💼 Portfolio: ${port_val:,.2f}\n"
        f"{'📈' if total_pnl>=0 else '📉'} Totaal P&L: {'+'if total_pnl>=0 else ''}${total_pnl:,.2f}\n"
        f"📊 Trades gesloten: {closed}\n"
        f"✅ Win rate: {f'{wins}/{closed} ({wins/closed*100:.0f}%)' if closed>0 else 'Nog geen'}\n\n"
        f"*Trades vandaag:*\n{trades_today}"
    )
    send(msg)
    daily_log = []
    last_daily = datetime.now().date()

# ─────────────────────────────────────────
#  HOOFDLOOP
# ─────────────────────────────────────────
def main():
    print("🤖 Hyperliquid Paper Trading Bot gestart")
    send(
        f"🤖 *Bot gestart!*\n"
        f"💵 Startkapitaal: ${cash:,.2f}\n"
        f"📈 Coins: {', '.join(COINS)}\n"
        f"⚙️ Strategie: EMA 9/21 + RSI 14 + Momentum\n"
        f"🎯 TP: +4% | SL: -2%\n"
        f"⏱ Check interval: {CHECK_INTERVAL}s\n\n"
        f"_Wacht op eerste signaal..._"
    )

    while True:
        try:
            now = datetime.now()

            # Dagelijkse samenvatting om 20:00
            if now.hour == 20 and now.minute == 0 and now.date() != last_daily:
                daily_summary()

            ok = fetch_prices()
            if ok:
                for coin in COINS:
                    sig = get_signal(coin)
                    if sig != "flat":
                        execute(coin, sig)

            # Status print in terminal
            port_val = cash
            if position:
                p = price_hist[position["coin"]][-1] if price_hist[position["coin"]] else position["entry"]
                if position["side"] == "long":
                    port_val = cash + position["qty"] * p
                else:
                    port_val = cash + (position["borrowed"] - position["qty"] * p)
            print(f"[{now.strftime('%H:%M:%S')}] Portfolio: ${port_val:,.2f} | Positie: {position['side'].upper()+' '+position['coin'] if position else 'Flat'} | Trades: {closed}")

        except Exception as e:
            print(f"Fout in hoofdloop: {e}")

        time.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    main()
