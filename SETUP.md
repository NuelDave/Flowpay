# FlowPay — Setup Guide

## Zero Platform Fees
FlowPay charges **nothing**. Users only pay:
- Paystack's interbank exchange rate (NGN → USD conversion)
- NOWPayments' network fee (blockchain transaction cost)

---

## Step 1: Get API Keys (both free)

### Paystack (deposits)
1. Go to https://paystack.com → Sign Up free
2. Verify your business email
3. Settings → API Keys → copy **Secret Key** and **Public Key**
4. Use `sk_test_...` / `pk_test_...` for testing first

### NOWPayments (crypto withdrawals)
1. Go to https://nowpayments.io → Sign Up free
2. Store Settings → API Keys → copy your **API Key**
3. Settings → IPN Settings → copy your **IPN Secret**

---

## Step 2: Run locally (Windows)

```
# 1. Install Python from python.org (check "Add to PATH")

# 2. Open Command Prompt in the flowpay folder:
#    Click the address bar → type cmd → Enter

# 3. Install deps
pip install -r requirements.txt

# 4. Set your keys (replace with real values)
set PAYSTACK_SECRET_KEY=sk_test_xxxx
set PAYSTACK_PUBLIC_KEY=pk_test_xxxx
set NOWPAYMENTS_API_KEY=xxxx
set APP_BASE_URL=http://localhost:5000

# 5. Run
python app.py

# 6. Open http://localhost:5000
```

---

## Step 3: Deploy free on Render

1. Push this folder to GitHub (github.com → New repo → upload files)
2. Go to render.com → New Web Service → connect your repo
3. Add these Environment Variables in Render dashboard:
   - `PAYSTACK_SECRET_KEY` = sk_live_xxxx  
   - `PAYSTACK_PUBLIC_KEY` = pk_live_xxxx  
   - `NOWPAYMENTS_API_KEY` = xxxx  
   - `NOWPAYMENTS_IPN_SECRET` = xxxx  
   - `APP_BASE_URL` = https://your-app-name.onrender.com
4. Click Deploy — live in ~2 minutes

---

## Step 4: Set up Webhooks

### Paystack
Dashboard → Settings → Webhooks → Add:
`https://your-app.onrender.com/webhook/paystack`

### NOWPayments
Dashboard → Store Settings → IPN Callback URL:
`https://your-app.onrender.com/webhook/nowpayments`

---

## How it works

```
User enters $100 → Paystack charges ₦138,000 at market rate → 
User's NGN balance credited → User chooses BTC →
NOWPayments sends BTC to wallet → 0 platform fee taken
```

## Database
SQLite (`flowpay.db`) stores all deposits and withdrawals.
On Render free tier, the DB resets on restart — upgrade to a 
free Supabase PostgreSQL for persistence.
