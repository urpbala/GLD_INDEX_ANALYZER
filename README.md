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
| `index_plan.py` | Turns the report into a trade-plan checklist (verdict, trigger, stop, targets, risk-fit check) |
| `index_daily_report.py` | Builds the daily report, keeps the signal log, sends Telegram |
| `requirements.txt` | Python packages |
| `packages.txt` | System packages for Streamlit Cloud (Chromium for the Zerodha GIFT reader) |
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
- **GIFT Nifty** is read in this order, stopping at the first fresh reading:
  1. a price you type (app) or set (`GIFT_NIFTY_PRICE`), 2. your own `load_gift_custom()`,
  3. the public GIFT Nifty pages of **Angel One** and **Upstox** (plain web requests, the freshest of the two),
  4. **Zerodha's public GIFT Nifty page** read with a headless browser (Selenium). No Zerodha account or API key is needed.
     The page's numbers are filled in by JavaScript after it loads, so a plain request cannot see them; the browser step waits for them.
     It only starts when steps 1-3 gave nothing fresh, because it is slower.
  - Every reading shows its source and "as of" time, and a stale warning if it is older than 18 hours. A value more than 8% from the Nifty close is rejected.
  - These are ordinary web pages, **not an official data feed**: layouts change, sites can block cloud servers, and their terms may restrict automated use.
    The job reads them about twice a day. Switch off parts with `GIFT_WEB=0` (Angel One / Upstox) and `GIFT_ZERODHA=0` (Zerodha browser).
  - If nothing could be read, the app and the Telegram message say why for each source (for example "HTTP 403" or "Chrome not found").
    Test it any time: `python index_context.py`. If the Zerodha page opens but no price is read, run `python index_context.py --dump-zerodha`
    and send me the text it prints, so the reader can be matched to the real page.
  - **Browser setup:** GitHub Actions already has Chrome (the workflows need nothing more). On Streamlit Cloud, `packages.txt`
    (chromium, chromium-driver) installs one; if you do not want this, delete `packages.txt` and the `selenium` line in `requirements.txt`.
  - In the app: *Global* tab -> type the price to override. In the morning job: Actions -> *Pre-market brief* -> *Run workflow* -> GIFT price.
  - Moneycontrol and Investing.com were **not** verified, so they are not used.
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

## Troubleshooting

- **"compute_tables" / "module not found" / "OLD version" message in the app**: some files in your repo come from different downloads
  (for example an old `index_research.py`) or one file was not uploaded. Upload ALL files from `index-analyzer.zip`, replacing the existing ones,
  then in Streamlit Cloud use the app menu -> **Reboot app**. The app now lists exactly which file is old or missing.
- **GIFT shows 0 / "not available"**: the 0 in the box is only the manual override. Open *GIFT reading details* in the Global tab (or look at the Telegram line) to see why each source failed. Type the price, or start the morning job by hand with the GIFT price.
- **Global cues unavailable**: Yahoo can rate limit cloud servers. Press Refresh data later.

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
