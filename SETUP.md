# Setup Guide — Email-to-Dashboard Pipeline

End-to-end flow:

```
Gmail (Dhan PDFs) → gmail_fetcher.py → PDFs in Trades log/
                  → pdf_parser.py    → TradesFromPDFs.csv
                  → fetch_and_analyze.py → dashboard/public/enriched_trades.json
                  → npm run build    → dashboard/dist/
                  → gh-pages branch  → https://<your-username>.github.io/<repo>/
```

---

## 1. Local prerequisites (one-time)

```bash
# Python 3.11+
pip install -r requirements.txt

# Node.js 20+
cd dashboard && npm install && cd ..
```

## 2. Configure `.env`

Edit `.env` (gitignored — never committed). Fill in real values:

```
BROKER=yfinance

GMAIL_ADDRESS=you@gmail.com
GMAIL_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx
DHAN_SENDER=noreply@dhan.co
DHAN_SUBJECT_KEYWORD=trade

PDF_PASSWORD=YOUR_PAN_HERE
```

### Get a Gmail App Password

1. Enable 2-Factor Auth on your Google account.
2. Go to <https://myaccount.google.com/apppasswords>.
3. Create a new App Password (any name; e.g. "trade-analysis").
4. Paste the 16-char value into `GMAIL_APP_PASSWORD`. **Never share it. It is NOT your real password.**

## 3. Verify a single PDF locally

```bash
python pdf_parser.py --inspect "Trades log/<some-trade>.pdf"
```

This dumps the raw extracted text and the parsed orders. If parsing looks
wrong, share the raw text and I'll tighten the regexes.

## 4. Run the pipeline locally

```bash
python pipeline.py
```

This will: fetch new emails, parse PDFs, run analysis, build the dashboard.
Look in `dashboard/dist/` for the built site.

Useful flags:

```bash
python pipeline.py --skip-fetch              # only re-parse + re-build
python pipeline.py --since 2026-03-10        # only fetch from this date
python pipeline.py --skip-build              # data only, no React build
```

---

## 5. Push to GitHub (once you share the repo URL)

```bash
git init
git add .
git commit -m "Initial commit: email-to-dashboard pipeline"
git branch -M main
git remote add origin git@github.com:<USERNAME>/<REPO>.git
git push -u origin main
```

`.gitignore` already excludes:
- `.env`
- `Trades log/`, `*.pdf`, `TradesFromPDFs.csv`, `analysis_history/`, `candle_cache/`
- `dashboard/public/enriched_trades.json`
- `dashboard/node_modules/`, `dashboard/dist/`

So the public repo will contain only source code, never your trades.

## 6. Configure GitHub Pages + Secrets

In the GitHub web UI:

**A. Add Secrets** — `Settings → Secrets and variables → Actions → New repository secret`:

| Name                   | Value                                  |
| ---------------------- | -------------------------------------- |
| `GMAIL_ADDRESS`        | `you@gmail.com`                        |
| `GMAIL_APP_PASSWORD`   | `xxxx xxxx xxxx xxxx` (App Password)   |
| `PDF_PASSWORD`         | Your PAN (uppercase)                   |
| `DHAN_SENDER`          | (optional) override default sender     |
| `DHAN_SUBJECT_KEYWORD` | (optional) override default subject    |

**B. Enable Pages** — `Settings → Pages`:

- Source = `Deploy from a branch`
- Branch = `gh-pages` / folder = `/ (root)`

The first workflow run will create the `gh-pages` branch automatically.

## 7. Trigger the workflow

- **From desktop:** GitHub repo → Actions tab → "Sync Trades & Deploy Dashboard" → "Run workflow".
- **From phone:** GitHub mobile app → Actions → tap the workflow → "Run workflow".

Optional `since` input lets you re-pull only the last few days of emails.

After it finishes (1–2 min), the dashboard is live at:

```
https://<username>.github.io/<repo>/
```

---

## What gets deployed (and what doesn't)

| Content                         | In `main` branch (public) | On the live site |
| ------------------------------- | ------------------------- | ---------------- |
| Python / React source code      | yes (no secrets)          | no               |
| `.env`, secrets                 | no (gitignored)           | no               |
| Trade PDFs                      | no                        | no               |
| `TradesFromPDFs.csv`            | no                        | no               |
| `enriched_trades.json`          | no                        | yes (in `dist/`) |
| Built HTML / CSS / JS           | no (gitignored `dist/`)   | yes (`gh-pages`) |

`enriched_trades.json` is the only data file shipped to the live site — it
contains aggregated stats (P&L, MFE/MAE, candles, EMA) but no PII (no PAN,
name, account number, broker order IDs).

---

## Troubleshooting

- **`Authentication failed` from Gmail** → App Password is wrong/expired, or
  2FA is not enabled on the Google account.
- **`PDF_PASSWORD did not unlock <file>`** → PAN must be uppercase. Try
  variants in the `.env`.
- **`no orders parsed`** → run `python pdf_parser.py --inspect <file.pdf>`,
  inspect the raw text, and update the regexes in `pdf_parser.py`.
- **Dashboard 404 on GitHub Pages** → check that the workflow finished and
  the `gh-pages` branch exists. Pages source must be `gh-pages`.
- **Assets 404 under `/<repo>/`** → ensure `VITE_BASE` is `/<repo>/` (the
  workflow sets this automatically from `${{ github.event.repository.name }}`).
