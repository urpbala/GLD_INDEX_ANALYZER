# Index Analyzer (Nifty 50 / Bank Nifty)

A web app you open from a link (works on a phone) plus two scheduled jobs (morning brief and after-close report) that send to your Telegram bot.
Educational research tool. Not financial advice.

## What is in this folder

| File | Purpose |
|---|---|
| `app.py` | The web app (tabs: Today, Research, Size, Log) |
| `index_core.py` | Shared code and the settings you edit (indices, lot sizes, risk rules, bias weights) |
| `index_research.py` | Tests whether the bias score says anything about what the index does next |
| `index_context.py` | GIFT Nifty, global markets, news notes and their historical impact on Nifty |
| `index_daily_report.py` | Builds the daily report, keeps the signal log, sends Telegram |
| `requirements.txt` | Python packages |
| `.github/workflows/daily_report.yml` | After-close report (15:50 IST) and signal log |
| `.github/workflows/premarket_report.yml` | Morning brief (08:30 IST) with global cues, news and GIFT Nifty |
| `.streamlit/config.toml` | Dark theme |
| `.gitignore` | Keeps secrets and temp files out of GitHub |

## Step 1 - Put the files on GitHub

1. github.com -> **New repository** -> name it `index-analyzer` -> Public or Private -> **Create repository**.
2. **Add file -> Upload files** and drag in everything from this folder, **including the `.github` and `.streamlit` folders**, then **Commit changes**.
   - If the page will not take the hidden folders, use **Add file -> Create new file**, type the path
     `.github/workflows/daily_report.yml` as the name, paste the file contents, and commit. Do the same for `.streamlit/config.toml`.

## Step 2 - Run the web app (Streamlit Community Cloud)

1. Go to **share.streamlit.io**, sign in with GitHub, click **Create app**.
2. Pick your repository, branch `main`, main file `app.py`.
3. Optional but useful: **Advanced settings -> Secrets**, paste (this enables the "Send this report to Telegram" button):
   ```
   TELEGRAM_BOT_TOKEN = "your-bot-token"
   TELEGRAM_CHAT_ID = "your-chat-id"
   ```
4. **Deploy**. You get a link like `https://yourname-index-analyzer.streamlit.app`. Open it on your phone and use
   **Add to Home screen** so it behaves like an app.

## Step 3 - Daily Telegram message (GitHub Actions)

1. **Create the bot:** in Telegram open **@BotFather** -> `/newbot` -> copy the token.
   **Find your chat id:** send any message to your new bot, then open
   `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser and read `"chat":{"id": ...}`.
2. In your GitHub repository: **Settings -> Secrets and variables -> Actions -> New repository secret**.
   Add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.
3. **Actions** tab -> run **Daily index report (after close)** and **Pre-market brief (morning)** once each with **Run workflow** to test them.
   (If GitHub shows a button saying workflows are disabled, click it to enable them.)
4. After that they run by themselves on weekdays at about 08:30 IST and 15:50 IST and save `index_context_log.csv` / `index_signal_log.csv` in the repo.
   GitHub's schedule can run late, sometimes by hours. For exact timing use Windows Task Scheduler instead.

Never put the token in any file in the repo. Keep it only in the two Secrets places above.
If a token was ever pasted somewhere public, make a new one in BotFather (`/revoke`).

## What the extra clues are (and are not)

- **Previous high / low (PH / PL)** for the day, week and month are shown for each index, with the last close compared against the
  range of the period before (closed above the prior high, below the prior low, or inside). The levels also feed the support /
  resistance list. A week or month counts as complete when the next business day falls in a new period (exchange holidays are not known).
- **GIFT Nifty**: read automatically from the public GIFT Nifty quote pages of **Angel One** and **Upstox** (the freshest of the two is used,
  with its "as of" time, and a stale warning if it is older than 18 hours). These are ordinary web pages, **not an official data feed**: the layout can change,
  a site can block cloud servers (GitHub / Streamlit), and their terms may restrict automated use. The job reads them about twice a day. Set `GIFT_WEB=0` to switch this off.
  If it cannot read a price it says so; then type it yourself:
  - In the app: *Global* tab -> type the price (and its change %) from your broker app or TradingView (`NSEIX: NIFTY1!`). A typed value always wins.
  - In the morning job: GitHub -> Actions -> *Pre-market brief* -> *Run workflow* -> fill in the GIFT price from your phone.
  - Or set `GIFT_YF_TICKER` / `GIFT_NIFTY_PRICE`, or write your own `load_gift_custom()` (e.g. from Angel One SmartAPI, which is the proper route if it carries GIFT Nifty).
  - Moneycontrol and Investing.com were **not** verified: no page could be opened to check, so they are not used.
  - GIFT is a futures price, so a small premium to the Nifty close is normal. Its change on its own close is the cleaner signal.
- **Global markets**: S&P 500, Nasdaq, Nikkei, Hang Seng, KOSPI, Shanghai, FTSE, DAX, dollar index, US 10-year yield, Brent, USD/INR and US VIX.
  Each move is scaled by that market's own recent volatility and combined into one score. The *Global* tab also has a research table that shows
  how much each market has actually mattered for Nifty (same-day and next-day correlation). US markets close after India, so the next-day column is the relevant one.
- **News**: recent headlines from Google News, scored by a crude keyword list and grouped into themes (crude, Fed, FII flows, RBI, ...).
  It is a clue only. Read the headlines, do not trade the label.
- The morning job logs its GIFT gap and global score and later fills in the real opening gap and the day's move, so the report shows its own hit rate over time.

## Good to know

- **Streamlit Cloud apps go to sleep** when nobody uses them. Open the link and press the wake-up button.
- **The log lives in the repo.** The cloud disk is wiped on restarts, so only the scheduled job writes the log
  (one row per index per date, no duplicates) and the app just reads it. The Log tab and the "track record" lines fill up over time.
- **Data comes from Yahoo Finance** (free): it can lag, be rate limited, or have gaps. The report warns when the latest candle looks old.
- **The first Research run takes up to a minute** (20 years of data). Results are cached for a day.
- **Verify lot sizes** in `index_core.py` (Nifty 65 is from your old script; Bank Nifty 30 is a placeholder).
- **The bias score is unvalidated.** Run the Research tab first. If it shows no reliable edge, treat the report as a market summary only.
  After the research supports it, set `BIAS_VALIDATED = True` in `index_core.py`.

## Run on your own computer (optional)

```
pip install -r requirements.txt
streamlit run app.py                       # the web app
python index_research.py                   # research report in the console and index_research_out/
python index_daily_report.py --dry-run     # print the daily report, send nothing
python index_daily_report.py --test-telegram
python index_daily_report.py --size 145 --index "NIFTY 50"
```
For local Telegram use, copy `env_example.txt` to `.env` (token and chat id) next to the scripts.
