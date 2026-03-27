# Alpaca Paper Trading Starter

## Movie Night Recommender

This workspace also includes a public-facing movie recommender app in [app.py](/Users/mattbiddle/Documents/Playground/app.py). It turns the Colab assignment into a browser app using Streamlit and TMDb.

### Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export TMDB_API_KEY=your_tmdb_api_key_here
export DATABASE_URL=your_postgres_connection_string_here
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
6. Add a secret named `DATABASE_URL` with your Postgres connection string.
7. Deploy.

That gives you a public Streamlit URL without buying a domain.

### Important note

Do not hardcode your TMDb key or Postgres connection string into `app.py` or commit them to GitHub. Keep them in:

- Streamlit secrets when deployed
- or environment variables like `TMDB_API_KEY` and `DATABASE_URL` when running locally

This project now includes two Python entry points:

- `alpaca_paper_client.py` for basic Alpaca account and manual order checks
- `intraday_event_strategy.py` for the report-aligned intraday event-day strategy
- `bot_config.json` for the two named bots that share one Alpaca paper account
- `pending_events.json` holds the current day's pre-market candidates waiting for the open
- `event_day_state.json` holds active intraday positions waiting for exit or reconciliation
- `trade_ledger.csv` is created automatically to store completed trades, realized P/L, bot attribution, and filter snapshots

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Fill in your Alpaca paper API keys in `.env`.

## Event-Day Strategy Rules

- Bot 1: buy gainers intraday on day 0
- Bot 2: short losers intraday on day 0
- Candidates come from Alpaca's own tradable stock universe plus batched snapshot checks
- Open entry happens after the first `1-2` minutes of trading
- Exit happens near `3:50 PM ET`
- No overnight holds

## Multi-Bot Setup

`bot_config.json` lets you run multiple strategy variants inside one Alpaca paper account. Each bot has:

- `name` for ledger attribution and client order IDs
- `direction` as `long` or `short`
- `source_list` as `gainers` or `losers`
- `trade_notional_usd` for per-bot sizing
- `max_portfolio_fraction` for tighter risk sizing
- `filters`

Supported filters currently include:

- `price_min` / `price_max`
- `premarket_move_min` / `premarket_move_max`
- `open_gap_min` / `open_gap_max`
- `prefer_open_gap_max`
- `avoid_open_gap_below`
- `require_shortable`

The default file includes two bots:

- `gainer_intraday_long`
- `loser_intraday_short`

## Strategy Workflow

### 1. Queue today's candidates before the open

Run this before the market open:

```bash
python intraday_event_strategy.py scan
```

Optional:

```bash
python intraday_event_strategy.py scan --trade-date 2026-03-25
```

This stores today's Alpaca-discovered premarket candidate list in `pending_events.json`.

### 2. Submit open entries shortly after the market opens

Run this around `9:31-9:45 AM ET`:

```bash
python intraday_event_strategy.py execute-open
```

Optional:

```bash
python intraday_event_strategy.py execute-open --ignore-window
```

### 3. Submit close exits near `3:50 PM ET`

```bash
python intraday_event_strategy.py execute-close
```

Optional:

```bash
python intraday_event_strategy.py execute-close --ignore-window
```

### 4. Inspect saved signals

```bash
python intraday_event_strategy.py status
```

### 5. Reconcile filled orders and write completed trades to the ledger

```bash
python intraday_event_strategy.py reconcile
```

Completed trades are appended to `trade_ledger.csv` with entry price, exit price, share count, and gross profit/loss.

### 6. Compare bot performance

```bash
python intraday_event_strategy.py performance
```

This summary now also includes per-bot realized performance.

## Environment Variables

- `ALPACA_API_KEY`
- `ALPACA_SECRET_KEY`
- `ALPACA_PAPER=true`
- `TRADE_NOTIONAL_USD=1000`
- `OPEN_DELAY_MINUTES=2`
- `OPEN_EXECUTION_WINDOW_MINUTES=15`
- `CLOSE_MINUTES_BEFORE_CLOSE=10`
- `CLOSE_EXECUTION_WINDOW_MINUTES=20`
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
- Both bots size by notional and convert the latest price into a whole-share quantity.
- The loser short bot also applies `max_portfolio_fraction` so you can cap short risk.
- After an open entry is submitted, that symbol is removed from `pending_events.json` and moved into `event_day_state.json` for same-day exit tracking.
- After a completed trade is logged to `trade_ledger.csv`, it is removed from `event_day_state.json`.
- Realized P/L is only written after Alpaca reports both the buy and sell orders as filled.
- `python intraday_event_strategy.py performance` summarizes realized results from `trade_ledger.csv`.
- The loser short bot relies on Alpaca asset metadata for shortability checks. Paper availability may still differ from live borrow conditions.
- `scan` now checks Alpaca's active tradable equity universe in batches and looks for premarket moves versus the previous close.
- `execute-open` re-checks the actual post-open gap before entering, because that is the filter the report cares about most.
- When email alerts are enabled, `reconcile` sends one end-of-day summary email with completed trades and updated performance.
- All scheduled commands skip non-trading days, so weekend runs exit cleanly without placing orders.
- A practical schedule is:

```text
9:20 AM ET: python intraday_event_strategy.py scan
9:32 AM ET: python intraday_event_strategy.py execute-open
3:50 PM ET: python intraday_event_strategy.py execute-close
4:05 PM ET: python intraday_event_strategy.py reconcile
```
