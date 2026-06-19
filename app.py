import os
import logging
import asyncio
import threading
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

# ── HELPERS ───────────────────────────────────────────────────────────────────

def get_session() -> str:
    """Return trading session based on UK time."""
    hour = datetime.utcnow().hour  # UTC; adjust +1 for BST if needed
    if 7 <= hour < 9:
        return "London Open 🇬🇧 — highest volatility window"
    elif 12 <= hour < 16:
        return "New York Session 🇺🇸 — major liquidity period"
    elif 2 <= hour < 6:
        return "Asian Session 🌏 — typically quieter, range-bound"
    else:
        return "London/NY Overlap ⚡ — peak volume period"


def generate_analysis(direction: str, pair: str, entry: float) -> str:
    """Call Anthropic API and return a 3-line signal analysis."""

    session = get_session()
    now     = datetime.utcnow().strftime("%A %H:%M UTC")

    prompt = f"""You are a professional gold and forex trading analyst for a signals service called Kevin's Gold Signals.

A new {direction} signal has just fired on {pair}.

Details:
- Direction: {direction}
- Entry price: {entry}
- Time: {now}
- Session: {session}

Write a SHORT 2-3 line analysis explaining WHY this signal makes sense RIGHT NOW.
Rules:
- Maximum 3 sentences
- Sound professional but easy to understand for retail traders
- Reference the session, price action, and momentum
- Do NOT use hashtags
- Do NOT say "I" or "we"
- End with one short motivational line like "Manage risk. Let it run. 💰"
- Vary your wording every time — never repeat the same phrases

Return ONLY the analysis text. Nothing else."""

    message = anthropic_client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=150,
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
        "entry":     2318.50
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

        # Generate AI analysis
        analysis = generate_analysis(direction, pair, entry)
        log.info(f"Analysis: {analysis}")

        # Format and send to Telegram
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
