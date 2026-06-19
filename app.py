import os
import logging
from datetime import datetime
from flask import Flask, request, jsonify
import anthropic
import requests

# ── LOGGING ───────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ── CONFIG ────────────────────────────────────────────────────────────────────
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]
BOT_TOKEN         = os.environ["BOT_TOKEN"]
CHAT_ID           = os.environ["CHAT_ID"]
PORT              = int(os.environ.get("PORT", 5000))

# ── CLIENTS ───────────────────────────────────────────────────────────────────
anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
app = Flask(__name__)

# ── MARKET DATA ───────────────────────────────────────────────────────────────

def get_live_gold_price() -> dict:
    """Fetch live gold price from multiple free sources."""
    # Try Metals.live (free, no key needed)
    try:
        r = requests.get("https://metals.live/api/spot", timeout=5)
        if r.status_code == 200:
            data = r.json()
            for item in data:
                if item.get("symbol") == "XAU":
                    price = item.get("price", 0)
                    change = item.get("change", 0)
                    change_pct = item.get("percentChange", 0)
                    return {
                        "price": round(price, 2),
                        "change": round(change, 2),
                        "change_pct": round(change_pct, 2),
                        "source": "live"
                    }
    except Exception as e:
        log.warning(f"metals.live failed: {e}")

    # Fallback: frankfurter (USD/XAU rates)
    try:
        r = requests.get("https://api.frankfurter.app/latest?from=XAU&to=USD", timeout=5)
        if r.status_code == 200:
            data = r.json()
            price = data["rates"]["USD"]
            return {
                "price": round(price, 2),
                "change": 0,
                "change_pct": 0,
                "source": "frankfurter"
            }
    except Exception as e:
        log.warning(f"frankfurter failed: {e}")

    return {"price": 0, "change": 0, "change_pct": 0, "source": "unavailable"}


def get_session_and_context() -> dict:
    """Return trading session, day info, and market context."""
    now  = datetime.utcnow()
    hour = now.hour
    dow  = now.strftime("%A")  # Monday, Tuesday etc
    date = now.strftime("%B %d, %Y")

    # Session
    if 7 <= hour < 9:
        session = "London Open 🇬🇧"
        session_note = "highest volatility window, institutional orders flooding in"
    elif 12 <= hour < 17:
        session = "New York Session 🇺🇸"
        session_note = "major liquidity period, US data drives price"
    elif 8 <= hour < 13:
        session = "London/NY Overlap ⚡"
        session_note = "peak volume window, strongest moves of the day occur here"
    elif 2 <= hour < 6:
        session = "Asian Session 🌏"
        session_note = "typically quieter, range-bound price action"
    else:
        session = "Off-Peak Hours"
        session_note = "lower liquidity, wider spreads possible"

    # Day context
    day_notes = {
        "Monday":    "start of week, market finding direction after weekend gaps",
        "Tuesday":   "mid-week momentum building, follow-through from Monday",
        "Wednesday": "mid-week, often choppy as traders await Thursday/Friday data",
        "Thursday":  "key data day, US jobless claims often impact gold",
        "Friday":    "end of week, traders closing positions — liquidity drops into close",
        "Saturday":  "weekend — markets closed, low volume",
        "Sunday":    "market opening, gap risk from weekend news"
    }

    # US market holiday check (major ones that affect gold)
    us_holidays = {
        "January 01":   "New Year's Day — US markets closed, thin liquidity",
        "July 04":      "US Independence Day — US markets closed, thin liquidity",
        "November 28":  "US Thanksgiving — US markets closed, thin liquidity",
        "December 25":  "Christmas Day — US markets closed, thin liquidity",
        "June 19":      "US Juneteenth — US markets closed, thin liquidity expected",
        "January 20":   "US MLK Day — US markets closed",
        "February 17":  "US Presidents Day — US markets closed",
        "May 26":       "US Memorial Day — US markets closed",
        "September 01": "US Labor Day — US markets closed",
        "November 11":  "US Veterans Day — US markets closed",
    }

    today_key = now.strftime("%B %d")
    holiday   = us_holidays.get(today_key, None)

    return {
        "session":      session,
        "session_note": session_note,
        "day":          dow,
        "day_note":     day_notes.get(dow, ""),
        "date":         date,
        "holiday":      holiday,
        "hour_utc":     hour
    }


def generate_analysis(direction: str, pair: str, entry: float) -> str:
    """Call Anthropic API with real market data and return analysis."""

    # Get live data
    gold_data = get_live_gold_price()
    ctx       = get_session_and_context()

    live_price   = gold_data["price"]
    price_change = gold_data["change"]
    change_pct   = gold_data["change_pct"]

    # Build price context string
    if live_price > 0:
        direction_emoji = "📈" if price_change >= 0 else "📉"
        price_context = f"Live gold price: ${live_price} ({direction_emoji} {price_change:+.2f} / {change_pct:+.2f}% today)"
    else:
        price_context = f"Signal entry price: ${entry}"

    # Build holiday warning
    holiday_note = ""
    if ctx["holiday"]:
        holiday_note = f"\n⚠️ IMPORTANT: {ctx['holiday']} — factor this into the analysis, warn about thin liquidity."

    prompt = f"""You are a sharp, professional gold trading analyst for "Kevin's Gold Signals" — a premium Telegram signals service with 90+ active clients.

A {direction} signal has just fired on {pair}.

REAL MARKET DATA RIGHT NOW:
- {price_context}
- Session: {ctx['session']} — {ctx['session_note']}
- Day: {ctx['day']}, {ctx['date']} — {ctx['day_note']}
- Signal entry: ${entry}{holiday_note}

Write a 2-3 sentence analysis that:
1. References the ACTUAL current price and what it's doing TODAY
2. Mentions the session and why timing makes sense (or warns if it doesn't)
3. If there's a holiday or thin liquidity — mention it as a caution
4. Ends with ONE short punchy line (risk management, discipline, let it run etc)

Rules:
- Sound like a real trader, not a robot
- Maximum 3 sentences + 1 closing line
- No hashtags, no "I" or "we"
- Vary wording every time — never repeat phrases
- Be specific to TODAY's conditions, not generic

Return ONLY the analysis. Nothing else."""

    message = anthropic_client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=180,
        messages=[{"role": "user", "content": prompt}]
    )

    return message.content[0].text.strip()


def send_telegram(text: str) -> bool:
    """Send a message to the Telegram channel."""
    url  = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = {
        "chat_id":    CHAT_ID,
        "text":       text,
        "parse_mode": "HTML"
    }
    r = requests.post(url, json=data, timeout=10)
    if r.status_code == 200:
        log.info("Telegram message sent OK")
        return True
    else:
        log.error(f"Telegram error: {r.text}")
        return False


# ── ROUTES ────────────────────────────────────────────────────────────────────

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "kevin-ai-analyst running ✅"}), 200


@app.route("/analyse", methods=["POST"])
def analyse():
    """
    Webhook endpoint. Expects JSON:
    {
        "direction": "BUY",
        "pair":      "XAUUSD",
        "entry":     4155.50
    }
    """
    try:
        data = request.get_json(force=True)
        log.info(f"Received: {data}")

        direction = str(data.get("direction", "")).upper()
        pair      = str(data.get("pair", "XAUUSD")).upper()
        entry     = float(data.get("entry", 0))

        if direction not in ("BUY", "SELL"):
            return jsonify({"error": "direction must be BUY or SELL"}), 400

        if entry <= 0:
            return jsonify({"error": "entry price required"}), 400

        # Generate AI analysis with live data
        analysis = generate_analysis(direction, pair, entry)
        log.info(f"Analysis generated: {analysis}")

        # Format Telegram message
        emoji = "🟢" if direction == "BUY" else "🔴"
        message = (
            f"{emoji} <b>{direction} SIGNAL — {pair}</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"💡 <b>AI Analysis:</b>\n"
            f"{analysis}"
        )

        send_telegram(message)

        return jsonify({
            "status":   "sent",
            "analysis": analysis
        }), 200

    except Exception as e:
        log.error(f"Error: {e}")
        return jsonify({"error": str(e)}), 500


# ── MAIN ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False)
