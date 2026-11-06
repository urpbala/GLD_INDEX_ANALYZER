# Index Analyzer (Nifty 50 / Bank Nifty)

A web app you open from a link (works on a phone) plus a scheduled job that sends the daily report to your Telegram bot.
Educational research tool. Not financial advice.

## What is in this folder

| File | Purpose |
|---|---|
| `app.py` | The web app (tabs: Today, Research, Size, Log) |
| `index_core.py` | Shared code and the settings you edit (indices, lot sizes, risk rules, bias weights) |
| `index_research.py` | Tests whether the bias score says anything about what the index does next |
| `index_daily_report.py` | Builds the daily report, keeps the signal log, sends Telegram |
| `requirements.txt` | Python packages |
| `.github/workflows/daily_report.yml` | Runs the report every weekday and saves the log to the repo |
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
3. **Actions** tab -> **Daily index report** -> **Run workflow** to test it once.
   (If GitHub shows a button saying workflows are disabled, click it to enable them.)
4. After that it runs by itself on weekdays at about 15:50 IST and saves `index_signal_log.csv` in the repo.
   GitHub's schedule can run late, sometimes by hours. For exact timing use Windows Task Scheduler instead.

Never put the token in any file in the repo. Keep it only in the two Secrets places above.
If a token was ever pasted somewhere public, make a new one in BotFather (`/revoke`).

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
