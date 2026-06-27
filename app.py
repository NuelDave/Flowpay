import os
import hmac
import hashlib
import requests
from flask import Flask, request, jsonify, render_template, redirect, url_for
from flask_cors import CORS
import sqlite3
import uuid
from datetime import datetime

app = Flask(__name__)
CORS(app)

# ── API Keys (set these as env vars before deploying) ──────────────────────────
PAYSTACK_SECRET_KEY  = os.environ.get("PAYSTACK_SECRET_KEY",  "sk_test_b2a2d4c3a3c25a3cd9ca3ddfd555bf382a4b4de9")
PAYSTACK_PUBLIC_KEY  = os.environ.get("PAYSTACK_PUBLIC_KEY",  "pk_test_894074e13163743d7cff12b241c9825afaa2c6a9")
NOWPAYMENTS_API_KEY  = os.environ.get("NOWPAYMENTS_API_KEY",  "SN64P17-BR1486Q-PW2BBGQ-BF4HA6A")
NOWPAYMENTS_IPN_SECRET = os.environ.get("NOWPAYMENTS_IPN_SECRET", "YXBW8QlFIDIv/iDpP4c7EmmMNIQrtNRd")
APP_BASE_URL         = os.environ.get("APP_BASE_URL", "https://flowpay-5t8q.onrender.com")

DB_PATH = "flowpay.db"

# ── Database ───────────────────────────────────────────────────────────────────
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS deposits (
            id TEXT PRIMARY KEY,
            email TEXT,
            name TEXT,
            amount_usd REAL,
            amount_ngn REAL,
            rate REAL,
            paystack_ref TEXT,
            status TEXT DEFAULT 'pending',
            created_at TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS withdrawals (
            id TEXT PRIMARY KEY,
            email TEXT,
            amount_usd REAL,
            coin TEXT,
            network TEXT,
            wallet_address TEXT,
            nowpayments_id TEXT,
            status TEXT DEFAULT 'pending',
            created_at TEXT
        )
    """)
    conn.commit()
    conn.close()

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# ── Exchange Rate ──────────────────────────────────────────────────────────────
def get_usd_ngn_rate():
    """Try to get live rate; fall back to current market rate."""
    try:
        r = requests.get("https://open.er-api.com/v6/latest/USD", timeout=4)
        data = r.json()
        ngn = data.get("rates", {}).get("NGN")
        if ngn and ngn > 100:
            return float(ngn)
    except Exception:
        pass
    return 1580.0   # fallback rate (June 2026)

# ── NOWPayments helpers ────────────────────────────────────────────────────────
def nowpayments_get_min_amount(currency):
    try:
        r = requests.get(
            f"https://api.nowpayments.io/v1/min-amount?currency_from=usd&currency_to={currency}",
            headers={"x-api-key": NOWPAYMENTS_API_KEY},
            timeout=8
        )
        return r.json().get("min_amount", 1)
    except Exception:
        return 1

def nowpayments_estimate(amount_usd, currency):
    try:
        r = requests.get(
            f"https://api.nowpayments.io/v1/estimate?amount={amount_usd}&currency_from=usd&currency_to={currency}",
            headers={"x-api-key": NOWPAYMENTS_API_KEY},
            timeout=8
        )
        return r.json()
    except Exception:
        return {}

# ── Routes ─────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html", paystack_public_key=PAYSTACK_PUBLIC_KEY)

# ── Deposit: initialise Paystack ───────────────────────────────────────────────
@app.route("/api/deposit/init", methods=["POST"])
def deposit_init():
    data = request.json
    name   = data.get("name", "").strip()
    email  = data.get("email", "").strip()
    amount = float(data.get("amount_usd", 0))

    if not name or not email or amount <= 0:
        return jsonify({"error": "Name, email and amount are required."}), 400

    rate      = get_usd_ngn_rate()
    amount_ngn = round(amount * rate * 100)  # Paystack expects kobo

    ref = f"FP-{uuid.uuid4().hex[:12].upper()}"

    # Save to DB
    conn = get_db()
    conn.execute(
        "INSERT INTO deposits VALUES (?,?,?,?,?,?,?,?,?)",
        (ref, email, name, amount, amount_ngn/100, rate, ref, "pending",
         datetime.utcnow().isoformat())
    )
    conn.commit()
    conn.close()

    # Initialise Paystack transaction
    payload = {
        "email": email,
        "amount": amount_ngn,
        "reference": ref,
        "currency": "NGN",
        "callback_url": f"{APP_BASE_URL}/deposit/callback",
        "metadata": {
            "name": name,
            "amount_usd": amount,
            "rate": rate
        }
    }
    try:
        r = requests.post(
            "https://api.paystack.co/transaction/initialize",
            headers={"Authorization": f"Bearer {PAYSTACK_SECRET_KEY}",
                     "Content-Type": "application/json"},
            json=payload,
            timeout=20
        )
        resp = r.json()
    except Exception as e:
        return jsonify({"error": f"Could not reach Paystack: {str(e)}"}), 500

    if not resp.get("status"):
        return jsonify({"error": resp.get("message", "Paystack error: " + str(resp))}), 500

    return jsonify({
        "authorization_url": resp["data"]["authorization_url"],
        "reference": ref,
        "rate": rate,
        "amount_ngn": amount_ngn / 100,
        "amount_usd": amount,
        "fee": 0
    })

# ── Deposit: Paystack callback ─────────────────────────────────────────────────
@app.route("/deposit/callback")
def deposit_callback():
    ref = request.args.get("reference", "")
    if not ref:
        return redirect("/?status=error")

    r = requests.get(
        f"https://api.paystack.co/transaction/verify/{ref}",
        headers={"Authorization": f"Bearer {PAYSTACK_SECRET_KEY}"},
        timeout=15
    )
    data = r.json().get("data", {})
    status = "success" if data.get("status") == "success" else "failed"

    conn = get_db()
    conn.execute("UPDATE deposits SET status=? WHERE id=?", (status, ref))
    conn.commit()
    conn.close()

    return redirect(f"/?status={status}&ref={ref}")

# ── Deposit: Paystack webhook ──────────────────────────────────────────────────
@app.route("/webhook/paystack", methods=["POST"])
def paystack_webhook():
    sig = request.headers.get("X-Paystack-Signature", "")
    body = request.get_data()
    expected = hmac.new(PAYSTACK_SECRET_KEY.encode(), body, hashlib.sha512).hexdigest()
    if sig != expected:
        return "", 401

    payload = request.json
    if payload.get("event") == "charge.success":
        ref = payload["data"]["reference"]
        conn = get_db()
        conn.execute("UPDATE deposits SET status='success' WHERE id=?", (ref,))
        conn.commit()
        conn.close()
    return "", 200

# ── Withdraw: estimate ─────────────────────────────────────────────────────────
@app.route("/api/withdraw/estimate", methods=["POST"])
def withdraw_estimate():
    data     = request.json
    amount   = float(data.get("amount_usd", 0))
    currency = data.get("currency", "btc").lower()

    if amount <= 0:
        return jsonify({"error": "Invalid amount"}), 400

    estimate = nowpayments_estimate(amount, currency)
    min_amt  = nowpayments_get_min_amount(currency)

    return jsonify({
        "amount_usd": amount,
        "currency": currency,
        "estimated_amount": estimate.get("estimated_amount", 0),
        "min_amount": min_amt,
        "fee": 0,          # zero platform fee
        "network_fee": "covered by NOWPayments"
    })

# ── Withdraw: create payout ────────────────────────────────────────────────────
@app.route("/api/withdraw/create", methods=["POST"])
def withdraw_create():
    data    = request.json
    email   = data.get("email", "").strip()
    amount  = float(data.get("amount_usd", 0))
    coin    = data.get("coin", "").lower()
    network = data.get("network", "")
    wallet  = data.get("wallet_address", "").strip()

    if not all([email, amount > 0, coin, wallet]):
        return jsonify({"error": "All fields are required."}), 400

    # ── Balance check: only allow withdrawal if user has a confirmed deposit ──
    conn = get_db()
    row = conn.execute(
        "SELECT COALESCE(SUM(amount_usd),0) FROM deposits WHERE email=? AND status='success'",
        (email,)
    ).fetchone()
    total_deposited = row[0] if row else 0

    withdrawn_row = conn.execute(
        "SELECT COALESCE(SUM(amount_usd),0) FROM withdrawals WHERE email=? AND status!='failed'",
        (email,)
    ).fetchone()
    total_withdrawn = withdrawn_row[0] if withdrawn_row else 0

    available = total_deposited - total_withdrawn
    if amount > available:
        conn.close()
        return jsonify({"error": f"Insufficient balance. Available: ${available:.2f}"}), 400

    # Create NOWPayments payout
    payout_payload = {
        "price_amount": amount,
        "price_currency": "usd",
        "pay_currency": coin,
        "payout_address": wallet,
        "payout_currency": coin,
        "ipn_callback_url": f"{APP_BASE_URL}/webhook/nowpayments",
        "order_id": f"WD-{uuid.uuid4().hex[:10].upper()}",
        "order_description": f"FlowPay withdrawal for {email}"
    }

    try:
        r = requests.post(
            "https://api.nowpayments.io/v1/payment",
            headers={"x-api-key": NOWPAYMENTS_API_KEY,
                     "Content-Type": "application/json"},
            json=payout_payload,
            timeout=20
        )
        resp = r.json()
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 500

    if "id" not in resp:
        conn.close()
        return jsonify({"error": resp.get("message", "NOWPayments error")}), 500

    wid = f"WD-{uuid.uuid4().hex[:10].upper()}"
    conn.execute(
        "INSERT INTO withdrawals VALUES (?,?,?,?,?,?,?,?,?)",
        (wid, email, amount, coin, network, wallet,
         str(resp["id"]), "pending", datetime.utcnow().isoformat())
    )
    conn.commit()
    conn.close()

    return jsonify({
        "status": "success",
        "withdrawal_id": wid,
        "nowpayments_id": resp["id"],
        "pay_address": resp.get("pay_address", wallet),
        "coin": coin.upper(),
        "amount_usd": amount
    })

# ── NOWPayments webhook ────────────────────────────────────────────────────────
@app.route("/webhook/nowpayments", methods=["POST"])
def nowpayments_webhook():
    payload = request.json
    now_id  = str(payload.get("payment_id", ""))
    status  = payload.get("payment_status", "")

    status_map = {
        "finished": "success",
        "failed":   "failed",
        "refunded": "failed",
        "partially_paid": "partial"
    }
    db_status = status_map.get(status, "pending")

    conn = get_db()
    conn.execute(
        "UPDATE withdrawals SET status=? WHERE nowpayments_id=?",
        (db_status, now_id)
    )
    conn.commit()
    conn.close()
    return "", 200

# ── Transaction history ────────────────────────────────────────────────────────
@app.route("/api/history/<email>")
def history(email):
    conn = get_db()
    deps = conn.execute(
        "SELECT * FROM deposits WHERE email=? ORDER BY created_at DESC LIMIT 20",
        (email,)
    ).fetchall()
    wds = conn.execute(
        "SELECT * FROM withdrawals WHERE email=? ORDER BY created_at DESC LIMIT 20",
        (email,)
    ).fetchall()
    conn.close()

    def row_to_dict(r):
        return dict(zip(r.keys(), tuple(r)))

    return jsonify({
        "deposits": [row_to_dict(d) for d in deps],
        "withdrawals": [row_to_dict(w) for w in wds]
    })

# ── Balance ────────────────────────────────────────────────────────────────────
@app.route("/api/balance/<email>")
def balance(email):
    conn = get_db()
    dep_row = conn.execute(
        "SELECT COALESCE(SUM(amount_usd),0) FROM deposits WHERE email=? AND status='success'",
        (email,)
    ).fetchone()
    wd_row = conn.execute(
        "SELECT COALESCE(SUM(amount_usd),0) FROM withdrawals WHERE email=? AND status!='failed'",
        (email,)
    ).fetchone()
    conn.close()
    deposited = dep_row[0] if dep_row else 0
    withdrawn = wd_row[0] if wd_row else 0
    return jsonify({"balance": round(deposited - withdrawn, 2)})

# Initialize DB on startup (works with both gunicorn and direct run)
init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
