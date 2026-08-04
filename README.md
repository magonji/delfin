# Delfin

A personal finance PWA built with Python, FastAPI, and vanilla JavaScript. Import your Financisto data and track, analyse, and manage your finances through a modern web interface that works on desktop and mobile.

**[Delfin on the web →](https://magonji.github.io/delfin/)** — screenshots and a tour of what it does.

## Features

### Dashboard (`index.html`)

- **KPI cards**: Total balance, monthly income/expenses, savings rate — all converted to your display currency using historical exchange rates (display currency is configurable in Tools → Maintenance; defaults to your most-used currency)
- **Net Worth Evolution**: Interactive chart with configurable interval (daily/weekly/monthly) and time range (1 month to all time). Accounts can be excluded individually
- **Monthly Category Spend**: Doughnut chart with month navigation. View by top expenses, category, or subcategory
- **Balance by Account**: All accounts with balances in original and converted currencies
- **Monthly Trend**: Income vs expenses bar chart by year
- **Yearly Trend**: Annual income vs expenses comparison
- **Category Spending Trend**: Line chart tracking spending by category over time
- **Top Payees**: Ranked by total spending with configurable time range
- **Transaction Volume**: Monthly transaction count trend

### Transactions (`transactions.html`)

- **Quick entry**: Fast transaction input with payee autocomplete and automatic category/location suggestion
- **Save & New**: Batch entry mode — saves and immediately opens a new form, deferring balance recalculation until the final save
- **Transfers**: Create transfers between accounts with multi-currency support and automatic exchange rate display
- **Split transactions**: One purchase carved into several lines, each with its own amount, category, project and note — the weekly shop that was half groceries and half a screwdriver. The ledger shows it as a single entry with the total and the balance it left behind; the caret opens the breakdown. The lines must add up to the amount before it can be saved. Any transaction can be split later, and a split whose lines are removed down to one becomes an ordinary transaction again
- **Hierarchical categories**: Parent > subcategory selection with inline creation
- **Advanced filters**: Date range, account, category, text search. Collapsible on mobile
- **Bulk edit**: Select multiple transactions or transfers to change account, category, payee, or delete in batch
- **Infinite scroll**: Transactions load progressively as you scroll
- **Running balances**: Per-account and total portfolio balance shown on each row
- **Mobile detail panel**: Tap a transaction on mobile to expand hidden info (category, location, note, balances) and action buttons
- **Optimistic saves**: Modal closes instantly; balance recalculation and list refresh happen in the background
- **Built for a phone keypad**: amount fields open the numeric keypad, and because a mobile decimal pad has no minus key, the amount carries a **± button** that switches between expense and income. The figure turns green or red as you type

### Budget (`budget.html`)

The budget is built on two ideas: a month is **materialised**, not recomputed — what
March said is what March keeps saying — and anything that recurs less often than
monthly is **prorated**, so a £600 bill every six months is budgeted as £100 a
month rather than a £600 spike.

- **Definitions, not entries**: fixed expenses, expected income and planned expenses are templates that materialise into each month as lines. The line is the record of what was budgeted at the time
- **Effective-dated edits**: an edit is made *from the month you are looking at*. Change the rent in October and July to September keep the old figure — the definition is split in two at that month rather than rewritten, and each half owns its own stretch of history. Saving or deleting asks whether the change stops at that month or carries on from it
- **Kakeibo buckets**: spending is sorted into essentials, indulgences, culture and unexpected, mapped from your own categories. A parent category classifies everything under it unless a subcategory says otherwise
- **Month calendar**: what falls on each day, what has already been paid, and the pace you are running at against the target
- **Sinking funds**: prorated items accrue a monthly share from the month they start, even while the first real charge is still ahead. The money set aside is tracked per savings account
- **Working-day rules**: a bill can land on an exact day, or on the *n*th working day counted from the start or the end of the month — wages on the second-to-last working day, say
- **One-off items**: `0` in the repeat interval means it happens once, in the month of its date, and the repetition fields get out of the way
- **Budget history**: month-by-month budgeted against actual

### Loans & Credit Cards (`loans.html`)

- **Automatic detection**: Distinguishes credit cards (3+ unique payees) from traditional loans
- **Loan tracking**: Borrowed amount, repaid, interest, remaining balance, and estimated APR via XIRR calculation
- **Add loan**: Records the agreed terms — rate, duration, repayment type, how often interest is charged and instalments paid, and which day of the month they land on (a fixed day, or a working day counted from either end). Opens an account for the loan and books the drawdown as a transfer into the account the money was paid into; terms can also be attached to a loan that already exists
- **Daily interest**: Interest can accrue daily rather than monthly, as most mortgages do. The schedule then follows the real days of each period (ACT/365F), so a February instalment carries less interest than a March one and a leap year costs a day more, while the instalment itself stays level and the difference lands in the final payment
- **Odd first period**: Interest runs from the drawdown, not from the first payment date, and the two are rarely a whole period apart. The first payment date can be given outright, and the first instalment is charged for the time that actually elapsed — a fortnight, or nearly two months — instead of being rounded to a full period. A whole period is judged by the calendar, so a month is a month whether it has 28 days or 31
- **Amortisation schedule**: With terms in hand the schedule is computed exactly instead of estimated — constant instalment, interest only, or constant capital — and the card shows the instalment, the next payment, and how far the real balance is ahead of or behind schedule
- **Fees**: An arrangement fee is booked as its own charge — added to the debt when it is capitalised, taken out of the money received when it is paid at the outset. A standing administration fee is charged on its own rhythm, which needn't match the instalments', and appears as a column in the schedule. Neither touches the nominal rate, but both drive the **effective rate** (APR/TAE) shown beside it, which is the figure two offers can honestly be compared on. An early repayment charge is kept out of both — it prices one thing, settling the loan today, which the card shows
- **Editing**: Terms can be corrected or removed from the card. Both leave the account and its movements alone: deleting the terms returns the loan to being estimated from its transactions, exactly as it was tracked before
- **Credit card progress bars**: Show ratio of current debt to historical maximum debt
- **Smart categorisation**: Interest and fees identified by category keywords
- **Lender detection**: Automatically identifies the lender from transaction payees
- **Transaction history**: Expandable per-account transaction list

### Tools (`tools.html`)

- **Entity management**: Edit and merge categories, accounts, payees, locations, and projects. Includes one-click **detect & merge duplicate categories** (reassigns all references)
- **CSV import**: Import any bank statement CSV via a generic column-mapping step (delimiter, decimal, encoding, debit/credit), with reusable per-bank profiles, duplicate detection, and inline entity creation. A statement line that was several things at once can be **split** in the preview into lines with their own category, project and note; they must add up to the amount the bank charged, and the result is one split transaction rather than several separate ones
- **CSV export**: Export transactions with date, account, and category filters in standard or detailed format
- **Import Financisto**: Import a Financisto database — native `.backup` (gzipped) or CSV export — directly inside the app. Auto-detects the format, shows a pre-import **compatibility report** (so any data that can't be mapped is listed, never dropped silently), supports **merge** or **replace**, and always takes a safety backup first. Transfers become Delfin transfer pairs and **splits become Delfin splits**, keeping each sub-item's category, project and note under one entry
- **Export Financisto**: Export your entire database as a native `.backup` (restorable in Financisto) or Financisto CSV. Splits survive both formats: the `.backup` rebuilds Financisto's parent envelope plus its children, and the CSV writes a `SPLIT` total row followed by one sub-item row each, the same shape Financisto's own CSV export produces
- **Database backup**: Download a timestamped `.db` backup (a consistent, WAL-safe snapshot via SQLite's online backup API)
- **Restore database**: Restore from a `.db` backup (from the Backup tool or the daily backups). Validates the file, takes a safety backup of current data first, then swaps it in
- **Refresh**: Recalculate all balances, payee statistics, and exchange rates
- **Maintenance**: Configure the daily maintenance time (default 02:28) and backup retention (1 month → 2 years, or never), and trigger a maintenance run on demand

### Cross-cutting

- **PWA**: Installable on iOS/Android/desktop with service worker (network-first for HTML, stale-while-revalidate for assets)
- **Multi-currency**: every currency in the ECB reference-rate feed (30+) is selectable for accounts and as the display currency, with historical exchange rates. All conversions use the rate from the transaction date. Newly-added currencies are backfilled across history on the next rate update
- **Auto rate updates**: Exchange rates update automatically on server startup and on page load, and again as part of the nightly maintenance job (no manual button needed)
- **Cache with dirty flag**: Dashboard and loans cache data locally (14-day TTL). When transactions change, a `dirty_data` flag triggers cache invalidation on next page load
- **Safari compatibility**: `-webkit-appearance: none` on all form controls, custom SVG dropdown arrows, no input zoom on iOS
- **Long lists stay usable**: the payee picker filters as you type, and the category picker for planned expenses folds subcategories into their parent — ticking the parent covers everything under it
- **Responsive design**: Optimised layouts for desktop, tablet, and mobile. Sticky footer on all pages
- **FAB buttons**: Floating action buttons on every page for quick access to new transaction/transfer (navigates to transactions page with modal auto-open)

## Tech Stack

### Backend

- **FastAPI** with Uvicorn (ASGI)
- **SQLAlchemy** ORM with SQLite
- **Standard library only** for Financisto import/export (`gzip`, `csv`) — no extra parsing dependencies
- **ECB XML feed** for historical exchange rates (GBP base)

### Frontend

- **Vanilla JavaScript** — no frameworks
- **Chart.js v4** for all charts
- **HTML5 + CSS3** with CSS custom properties

## Project Structure

```
delfin/
├── backend/
│   ├── main.py                    # FastAPI app — all endpoints
│   ├── models.py                  # SQLAlchemy models
│   ├── schemas.py                 # Pydantic request/response schemas
│   ├── budget_engine.py           # Budget: materialisation, proration, versioning
│   ├── loan_engine.py             # Loans: amortisation schedule from the agreed terms
│   ├── database.py                # DB engine, session config, added-column migrations
│   ├── helpers.py                 # Balance recalculation, rate helpers
│   ├── update_exchange_rates.py   # ECB rate fetcher
│   ├── maintenance.py             # Nightly job (rates+balances+payees+backup) & scheduler
│   ├── backup.py                  # Off-disk DB backup (activity-detected, age-pruned)
│   ├── settings_store.py          # Maintenance settings (time + retention), JSON in data/
│   ├── security.py                # DB encryption key mgmt (DEK wrapped by password + recovery code)
│   └── integrations/              # Self-contained import/export modules
│       ├── report.py              # Compatibility report (transparent data-loss tracking)
│       └── financisto/            # Financisto .backup + CSV import/export
│           ├── backup_format.py   # .backup (de)serialisation (gzip + $ENTITY blocks)
│           ├── model.py           # Structural converters (nested-set ↔ parent, units, dates)
│           ├── importer.py        # Financisto → Delfin (.backup + CSV)
│           └── exporter.py        # Delfin → Financisto (.backup + CSV)
├── frontend/
│   ├── login.html                 # Login / first-run setup / recovery (served at /login.html)
│   ├── index.html                 # Dashboard
│   ├── transactions.html          # Transaction management
│   ├── budget.html                # Budget tracker
│   ├── loans.html                 # Loans & credit cards
│   ├── tools.html                 # Management tools (incl. Financisto import/export)
│   ├── sw.js                      # Service worker
│   ├── manifest.json              # PWA manifest
│   └── icons/                     # App icons (180, 192, 512)
├── data/
│   └── finance.db                 # SQLite database (gitignored)
├── docs/                          # The project website, served by GitHub Pages
│   ├── index.html
│   └── img/
├── .github/workflows/
│   └── docker-publish.yml         # CI: build arm64+amd64 image, push to ghcr.io
├── Dockerfile                     # Container image definition
├── docker-compose.yml             # One-command run with a persistent data volume
├── .dockerignore
├── requirements.txt
└── README.md
```

> **Self-contained by design.** Importing and exporting Financisto data is built
> into the app (Tools page) — there are no helper scripts to run. The previous
> external importer (`scripts/import_financisto_csv.py`), the now-redundant
> `initialise_database.py` / `update_database.py` / `update_exchange_rates.py`,
> the `clean_duplicate_categories.py` maintenance script, and the old
> `setup-pi.sh` installer have all been removed; their functionality lives in
> the app (rates update automatically on startup, balances recalculate after
> every import, payee statistics refresh from the Tools page, and duplicate
> categories are merged from Tools → Categories), and deployment is now handled
> by Docker (see [Run with Docker](#run-with-docker-recommended)).

## Getting Started

### Prerequisites

- Python 3.9+
- pip

### New Installation

```bash
# Clone and install
git clone https://github.com/magonji/delfin.git
cd delfin
pip install -r requirements.txt

# Start the server
uvicorn backend.main:app --reload --port 8422
```

Open `http://localhost:8422/app/index.html` in your browser, then go to
**Tools → Import Financisto** and select your Financisto `.backup` (or CSV
export). Delfin parses it in-app, shows a compatibility report, and imports it —
no scripts to run. Exchange rates fetch automatically on startup and balances
are recalculated as part of the import.

### Updating an Existing Installation

```bash
git pull
pip install -r requirements.txt
uvicorn backend.main:app --reload --port 8422
```

Tables are created/extended automatically by the SQLAlchemy models on startup,
and exchange rates update automatically — no manual migration step needed.

### Run with Docker (recommended)

The image is published to the GitHub Container Registry at
**`ghcr.io/magonji/delfin`** and rebuilt as a multi-arch image for **arm64** and
**amd64** on every push to `main` via GitHub Actions. Docker automatically pulls
the right one for your host.

**With Docker Compose** (easiest — persists the DB in `./data`):

```bash
git clone https://github.com/magonji/delfin.git
cd delfin
docker compose up -d
```

**Or pull and run the published image directly:**

```bash
docker run -d --name delfin --restart unless-stopped \
  -e TZ=Europe/Madrid \
  -p 8422:8422 \
  -v "$(pwd)/data:/app/data" \
  ghcr.io/magonji/delfin:latest
```

Open `http://<host>:8422/app/index.html`. The SQLite database lives in the
mounted `data/` volume, so it survives container restarts and image updates. If
`finance.db` doesn't exist yet, an empty one is created on first start — then use
**Tools → Import Financisto** to load your data.

**Update** to the latest image:

```bash
docker compose pull && docker compose up -d
```

**Build it yourself** (e.g. for a different architecture):

```bash
docker compose build                                    # local build via the Dockerfile
docker buildx build --platform linux/arm64,linux/amd64 -t delfin .   # multi-arch
```

> The published GHCR package may be private by default. Make it public from the
> repo's **Packages** page if you want to pull without authenticating, or run
> `docker login ghcr.io` with a personal access token (scope `read:packages`).

#### Nightly maintenance & off-disk backups

Because Delfin runs continuously, it does its housekeeping in **one daily job** at
a configurable time (default **02:28**, set in **Tools → Maintenance**):

1. refresh exchange rates,
2. recalculate balances (the data behind the dashboard graphs),
3. recalculate payee statistics,
4. **back up the database to a second disk** — but only if your data actually
   changed.

It's pure Python built into the app, so it works on any host (Linux/Windows/macOS)
— no cron or systemd. If the machine was off during the scheduled window, the job
runs a catch-up pass on the next start.

**What counts as "changed":** the backup is taken only on real user activity.
Exchange-rate refreshes, the `updated_at` bookkeeping the job itself writes, and
the rate-derived `total_balance_after` cache are **ignored** when deciding whether
to back up — so an idle day produces no backup even though rates were updated.

Snapshots use SQLite's online backup API (consistent and WAL-safe — not a raw
file copy). Old backups are pruned by age according to the **retention** you pick
in Tools (1 month / 3 / 6 months / 1 / 2 years / never). For real resilience,
point the backups at a **different physical disk** than the live DB.

| Variable | Default | Purpose |
|----------|---------|---------|
| `DELFIN_BACKUP_DIR` | `/app/backups` | Where snapshots are written (inside the container) |

(The maintenance time and retention are app settings — configured in the UI, not
env vars.)

Example: live DB on the SD card, backups on an external drive at `/srv/storage`:

```bash
mkdir -p ~/docker/delfin /srv/storage/backups/delfin
# Enable backups (the sentinel also proves the external disk is mounted —
# if it's missing, the backup is skipped instead of writing to the wrong disk):
touch /srv/storage/backups/delfin/.delfin-backup-enabled

docker run -d --name delfin --restart unless-stopped \
  -e TZ=Europe/Madrid \
  -p 8422:8422 \
  -v ~/docker/delfin:/app/data \
  -v /srv/storage/backups/delfin:/app/backups \
  ghcr.io/magonji/delfin:latest
```

Without the `.delfin-backup-enabled` sentinel in the backup directory, the
feature stays dormant — mounting the volume alone does nothing.

> **Timezone:** the maintenance time is wall-clock time in the container's
> timezone. Set `TZ` (e.g. `Europe/Madrid`) so 02:28 means 02:28 local, not UTC.

## API Overview

Full interactive docs at `http://localhost:8422/docs`.

| Area | Key Endpoints |
|------|--------------|
| **Accounts** | `GET /accounts`, `GET /accounts/with-balances`, `POST /accounts` |
| **Transactions** | `GET /transactions`, `POST /transactions`, `PUT /transactions/{id}`, `DELETE /transactions/{id}` |
| **Splits** | `GET /transactions/split/{group_id}`, `POST /transactions/split`, `PUT /transactions/split/{group_id}`, `DELETE /transactions/split/{group_id}` |
| **Transfers** | `GET /transactions/transfers`, `POST /transactions/transfers` |
| **Categories** | `GET /categories`, `POST /categories`, `PUT /categories/{id}` |
| **Payees** | `GET /payees`, `POST /payees`, `POST /payees/recalculate-all-stats` |
| **Budget** | `GET /budgets/{ym}/progress`, `POST /budgets`, `GET /budget/history`, `GET /budget/suggestions` |
| **Budget definitions** | `GET /budget/items`, `POST /budget/items`, `PUT /budget/items/{id}?effective_ym=&scope=`, `DELETE /budget/items/{id}?effective_ym=&scope=`, `PATCH /budget/lines/{id}` |
| **Kakeibo** | `GET /budget/buckets`, `PUT /budget/buckets` |
| **Loans** | `GET /loans/summary`, `GET /loans/details`, `POST /loans`, `POST /loans/preview`, `PUT /loans/{id}`, `DELETE /loans/{id}`, `GET /loans/{id}/schedule` |
| **Dashboard** | `GET /dashboard/summary`, `GET /networth-evolution`, `GET /balance-kpis` |
| **Exchange Rates** | `GET /exchange-rates/latest`, `GET /exchange-rates`, `POST /exchange-rates/update` |
| **Financisto** | `POST /tools/financisto/import` (mode=analyze\|merge\|replace), `GET /tools/financisto/export?format=backup\|csv` |
| **Admin** | `POST /admin/initialise-balances`, `POST /admin/recalculate-balances-for-accounts`, `POST /admin/backup-database` |

## Database

### Tables

- **accounts**: Bank accounts, wallets, credit cards. Tracks `currency`, `initial_balance`, `current_balance`, `is_active`
- **categories**: Hierarchical (parent + name). Types: expense, income
- **payees**: Merchants. Caches most common category/location/project for autocomplete
- **transactions**: Core table. Links to account, category, payee, location, project. Caches `account_balance_after` and `total_balance_after`. A split transaction is stored as one row per line, all sharing a `split_group_id` (the id of the first line) — so balances, filters and every category-based report stay correct without knowing that splits exist
- **exchange_rates**: Historical daily rates (GBP base) from ECB
- **budgets**: Monthly spending targets
- **budget_items**: Budget definitions — fixed expense, expected income or planned expense. Carries the recurrence (`interval_count`/`interval_unit`, `day_rule`), the months it applies to (`starts_ym`/`ends_ym`) and a `series_id` tying its versions together
- **budget_month_lines**: A definition materialised for one month — the auditable record of what was budgeted at the time. Frozen once the month closes
- **budget_item_accounts** / **budget_item_categories**: What a planned expense counts spending from
- **category_buckets**: Maps a category to a kakeibo bucket

Editing a definition from a given month never rewrites history: the row is capped
at the month before and a new one takes over from there, both sharing a
`series_id`. Past months keep pointing at the version that was in force, so a
line always reflects the figure it was budgeted with.

> `recurring_expenses` and `planned_expenses` predate `budget_items` and are kept
> so old data is not lost. Nothing on the budget page reads them any more.

### Balance Calculation

Balances are cached on each transaction for display performance:
- `account_balance_after` — running balance for the transaction's account
- `total_balance_after` — portfolio-wide running balance (all accounts, converted to the display currency using historical rates)

Recalculation is triggered automatically on create/edit/delete, but deferred to background for UI responsiveness.

## Security: encryption & login

The database is **encrypted at rest with SQLCipher (AES-256)** and the app
**requires a login**. On first run you set a password; the app encrypts the
existing `finance.db` and shows a **recovery code** (saved once).

How it works:
- A random 256-bit data key encrypts the DB. That key is stored **wrapped** in
  `data/.delfin_keyfile.json` — once with a key derived (Scrypt) from your
  password, once from the recovery code. Either unlocks it; changing the password
  only re-wraps the key (the DB is never re-encrypted, and backups stay valid).
- The key is the password, so the file is unreadable without it — **and so are
  the `.db` backups**, which are encrypted too.
- After any restart the app is **locked until someone logs in** (the key lives
  only in memory). The nightly maintenance/rate update waits for the first login.

**Keep these safe — there is no other way back in:**
- Your **password** and your **recovery code** (losing both = data unrecoverable).
- The **keyfile** (`data/.delfin_keyfile.json`): the encrypted `.db` is useless
  without it, so the off-disk backup folder keeps a copy of it for disaster
  recovery. The keyfile and the session secret are gitignored — never commit them.

> **Plain HTTP caveat:** over `http://` on the LAN the password/cookie travel
> unencrypted. For real protection put Delfin behind HTTPS (reverse proxy / Tailscale).

The login, first-run setup and "forgot password → recovery code" flows live on
the `/login.html` page; **Log out** is in the nav menu on every page.

## Licence

Released under the [MIT License](LICENSE) — use, modify, and distribute freely,
keeping the copyright notice. © 2025 Mario González Jiménez.

---

**Built with love by a dolphin for personal finance management**
