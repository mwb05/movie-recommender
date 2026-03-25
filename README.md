# Alpaca Paper Trading Starter

## Movie Night Recommender

This workspace also includes a public-facing movie recommender app in [app.py](/Users/mattbiddle/Documents/Playground/app.py). It turns the Colab assignment into a browser app using Streamlit and TMDb.

### Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export TMDB_API_KEY=your_tmdb_api_key_here
streamlit run app.py
```

Then open the local URL Streamlit prints, usually `http://localhost:8501`.

### Deploy for free

The easiest free option is Streamlit Community Cloud:

1. Push this project to GitHub.
2. Sign in to [Streamlit Community Cloud](https://streamlit.io/cloud).
3. Create a new app and point it at this repo.
4. Set the entrypoint to `app.py`.
5. In the app settings, add a secret named `TMDB_API_KEY` with your TMDb API key.
6. Deploy.

That gives you a public Streamlit URL without buying a domain.

### Important note

Do not hardcode your TMDb key into `app.py` or commit it to GitHub. Keep it in:

- Streamlit secrets when deployed
- or an environment variable like `TMDB_API_KEY` when running locally

This project now includes two Python entry points:

- `alpaca_paper_client.py` for basic Alpaca account and manual order checks
- `event_day_strategy.py` for the TradingView event-day strategy
- `pending_events.json` holds the current day's event stocks until the next trading day buy window
- `event_day_state.json` holds only active bought positions waiting for exit
- `trade_ledger.csv` is created automatically to store completed trades and realized P/L

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Fill in your Alpaca paper API keys in `.env`.

## Event-Day Strategy Rules

- Day 0 is any stock on TradingView's US gainers page up at least `+20%`
- Day 0 is any stock on TradingView's US losers page down at least `-20%`
- Gainer signal: buy on trading day 1, sell on trading day 3
- Loser signal: buy on trading day 0, sell on trading day 4
- Orders use `time_in_force=cls`, which is Alpaca's market-on-close style submission

## Strategy Workflow

### 1. Record completed event days after the market closes

Run this after the close so the daily move is final:

```bash
python event_day_strategy.py scan
```

Optional:

```bash
python event_day_strategy.py scan --event-date 2026-03-20
```

This stores next-day buy candidates in `pending_events.json`.

### 2. Submit due orders near the close on future trading days

Run this near the close on each trading day:

```bash
python event_day_strategy.py execute
```

Optional:

```bash
python event_day_strategy.py execute --ignore-window
```

### 3. Inspect saved signals

```bash
python event_day_strategy.py status
```

### 4. Reconcile filled orders and write completed trades to the ledger

```bash
python event_day_strategy.py reconcile
```

Completed trades are appended to `trade_ledger.csv` with entry price, exit price, share count, and gross profit/loss.

### 5. Send a test email

```bash
python event_day_strategy.py test-email
```

### 6. Compare gainer versus loser trade performance

```bash
python event_day_strategy.py performance
```

## Environment Variables

- `ALPACA_API_KEY`
- `ALPACA_SECRET_KEY`
- `ALPACA_PAPER=true`
- `TRADE_NOTIONAL_USD=1000`
- `SUBMIT_MINUTES_BEFORE_CLOSE=10`
- `EXECUTION_WINDOW_MINUTES=15`
- `DRY_RUN=true`
- `EMAIL_ALERTS_ENABLED=false`
- `SMTP_HOST=smtp.gmail.com`
- `SMTP_PORT=587`
- `SMTP_USE_TLS=true`
- `SMTP_USERNAME=your_email@example.com`
- `SMTP_PASSWORD=your_app_password_here`
- `EMAIL_FROM=your_email@example.com`
- `EMAIL_TO=your_email@example.com`

## Important Notes

- `DRY_RUN=true` prevents real paper orders and only prints what would be submitted.
- For actual paper trading, switch `DRY_RUN=false`.
- Buy sizing uses approximately `$1,000` per trade by converting the latest trade price into a whole-share quantity just before order submission.
- Sell orders use the same recorded share quantity that was used for the buy.
- After a buy is submitted, that symbol is removed from `pending_events.json` and moved into `event_day_state.json` for exit tracking.
- After a completed trade is logged to `trade_ledger.csv`, it is removed from `event_day_state.json`.
- Realized P/L is only written after Alpaca reports both the buy and sell orders as filled.
- When email alerts are enabled, the strategy sends one summary email per `execute` run and one summary email per `reconcile` run, instead of one email per trade.
- Email alerts include account equity, buying power, day P/L, week P/L, month P/L, year P/L, and bot all-time realized P/L.
- `python event_day_strategy.py performance` summarizes realized gainer versus loser results from `trade_ledger.csv`.
- Because day-0 classification is based on the finished daily move, `scan` should run after the market close, while `execute` should run before later closes.
- A practical schedule is:

```text
3:45 PM ET to 3:50 PM ET: python event_day_strategy.py execute
4:05 PM ET: python event_day_strategy.py scan
```
