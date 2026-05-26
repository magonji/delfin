# Delfin

A personal finance PWA built with Python, FastAPI, and vanilla JavaScript. Import your Financisto data and track, analyse, and manage your finances through a modern web interface that works on desktop and mobile.

## Features

### Dashboard (`index.html`)

- **KPI cards**: Total balance, monthly income/expenses, savings rate — all converted to base currency (GBP) using historical exchange rates
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
- **Hierarchical categories**: Parent > subcategory selection with inline creation
- **Advanced filters**: Date range, account, category, text search. Collapsible on mobile
- **Bulk edit**: Select multiple transactions or transfers to change account, category, payee, or delete in batch
- **Infinite scroll**: Transactions load progressively as you scroll
- **Running balances**: Per-account and total portfolio balance shown on each row
- **Mobile detail panel**: Tap a transaction on mobile to expand hidden info (category, location, note, balances) and action buttons
- **Optimistic saves**: Modal closes instantly; balance recalculation and list refresh happen in the background

### Budget (`budget.html`)

- **Monthly budget**: Set a target and track spending against it with a progress bar
- **Recurring expenses**: Track fixed monthly costs (subscriptions, rent, etc.) with payment status
- **Planned expenses**: One-off upcoming expenses with target dates
- **Weekly breakdown**: Expenses grouped by week with expandable detail
- **Income tracking**: Monthly income summary with category breakdown
- **Budget history**: Month-by-month history of budget vs actual spending

### Loans & Credit Cards (`loans.html`)

- **Automatic detection**: Distinguishes credit cards (3+ unique payees) from traditional loans
- **Loan tracking**: Borrowed amount, repaid, interest, remaining balance, and estimated APR via XIRR calculation
- **Credit card progress bars**: Show ratio of current debt to historical maximum debt
- **Smart categorisation**: Interest and fees identified by category keywords
- **Lender detection**: Automatically identifies the lender from transaction payees
- **Transaction history**: Expandable per-account transaction list

### Tools (`tools.html`)

- **Entity management**: Edit and merge categories, accounts, payees, locations, and projects. Includes one-click **detect & merge duplicate categories** (reassigns all references)
- **CSV import**: Import bank statements (Bank of Scotland, PayPal, or custom format) with column mapping, duplicate detection, and inline entity creation
- **CSV export**: Export transactions with date, account, and category filters in standard or detailed format
- **Import Financisto**: Import a Financisto database — native `.backup` (gzipped) or CSV export — directly inside the app. Auto-detects the format, shows a pre-import **compatibility report** (so any data that can't be mapped is listed, never dropped silently), supports **merge** or **replace**, and always takes a safety backup first
- **Export Financisto**: Export your entire database as a native `.backup` (restorable in Financisto) or Financisto CSV
- **Database backup**: Download timestamped `.db` backup files
- **Refresh**: Recalculate all balances, payee statistics, and exchange rates

### Cross-cutting

- **PWA**: Installable on iOS/Android/desktop with service worker (network-first for HTML, stale-while-revalidate for assets)
- **Multi-currency**: 30+ currencies with historical ECB exchange rates. All conversions use the rate from the transaction date
- **Auto rate updates**: Exchange rates update automatically on server startup and daily thereafter (no manual button needed)
- **Cache with dirty flag**: Dashboard and loans cache data locally (14-day TTL). When transactions change, a `dirty_data` flag triggers cache invalidation on next page load
- **Safari compatibility**: `-webkit-appearance: none` on all form controls, custom SVG dropdown arrows, no input zoom on iOS
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
│   ├── database.py                # DB engine and session config
│   ├── helpers.py                 # Balance recalculation, rate helpers
│   ├── update_exchange_rates.py   # ECB rate fetcher
│   └── integrations/              # Self-contained import/export modules
│       ├── report.py              # Compatibility report (transparent data-loss tracking)
│       └── financisto/            # Financisto .backup + CSV import/export
│           ├── backup_format.py   # .backup (de)serialisation (gzip + $ENTITY blocks)
│           ├── model.py           # Structural converters (nested-set ↔ parent, units, dates)
│           ├── importer.py        # Financisto → Delfin (.backup + CSV)
│           └── exporter.py        # Delfin → Financisto (.backup + CSV)
├── frontend/
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
> by Docker (see [Run with Docker](#run-with-docker-recommended-for-raspberry-pi--always-on-hosting)).

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
uvicorn backend.main:app --reload
```

Open `http://localhost:8000/app/index.html` in your browser, then go to
**Tools → Import Financisto** and select your Financisto `.backup` (or CSV
export). Delfin parses it in-app, shows a compatibility report, and imports it —
no scripts to run. Exchange rates fetch automatically on startup and balances
are recalculated as part of the import.

### Updating an Existing Installation

```bash
git pull
pip install -r requirements.txt
uvicorn backend.main:app --reload
```

Tables are created/extended automatically by the SQLAlchemy models on startup,
and exchange rates update automatically — no manual migration step needed.

### Run with Docker (recommended for Raspberry Pi / always-on hosting)

The image is published to the GitHub Container Registry at
**`ghcr.io/magonji/delfin`** and rebuilt as a multi-arch image for **arm64**
(Raspberry Pi 64-bit) and **amd64** (Windows/Mac/Linux x86 servers) on every push
to `main` via GitHub Actions. Docker automatically pulls the right one for your host.

**With Docker Compose** (easiest — persists the DB in `./data`):

```bash
git clone https://github.com/magonji/delfin.git
cd delfin
docker compose up -d
```

**Or pull and run the published image directly:**

```bash
docker run -d --name delfin --restart unless-stopped \
  -p 8000:8000 \
  -v "$(pwd)/data:/app/data" \
  ghcr.io/magonji/delfin:latest
```

Open `http://<host>:8000/app/index.html`. The SQLite database lives in the
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

## API Overview

Full interactive docs at `http://localhost:8000/docs`.

| Area | Key Endpoints |
|------|--------------|
| **Accounts** | `GET /accounts`, `GET /accounts/with-balances`, `POST /accounts` |
| **Transactions** | `GET /transactions`, `POST /transactions`, `PUT /transactions/{id}`, `DELETE /transactions/{id}` |
| **Transfers** | `GET /transactions/transfers`, `POST /transactions/transfers` |
| **Categories** | `GET /categories`, `POST /categories`, `PUT /categories/{id}` |
| **Payees** | `GET /payees`, `POST /payees`, `POST /payees/recalculate-all-stats` |
| **Budget** | `GET /budget`, `POST /budget`, `GET /recurring-expenses`, `GET /planned-expenses` |
| **Loans** | `GET /loans/summary`, `GET /loans/details` |
| **Dashboard** | `GET /dashboard/summary`, `GET /networth-evolution`, `GET /balance-kpis` |
| **Exchange Rates** | `GET /exchange-rates/latest`, `GET /exchange-rates`, `POST /exchange-rates/update` |
| **Financisto** | `POST /tools/financisto/import` (mode=analyze\|merge\|replace), `GET /tools/financisto/export?format=backup\|csv` |
| **Admin** | `POST /admin/initialise-balances`, `POST /admin/recalculate-balances-for-accounts`, `POST /admin/backup-database` |

## Database

### Tables

- **accounts**: Bank accounts, wallets, credit cards. Tracks `currency`, `initial_balance`, `current_balance`, `is_active`
- **categories**: Hierarchical (parent + name). Types: expense, income
- **payees**: Merchants. Caches most common category/location/project for autocomplete
- **transactions**: Core table. Links to account, category, payee, location, project. Caches `account_balance_after` and `total_balance_after`
- **exchange_rates**: Historical daily rates (GBP base) from ECB
- **budgets**: Monthly spending targets
- **recurring_expenses**: Fixed monthly costs with payment tracking
- **planned_expenses**: One-off future expenses

### Balance Calculation

Balances are cached on each transaction for display performance:
- `account_balance_after` — running balance for the transaction's account
- `total_balance_after` — portfolio-wide running balance (all accounts, converted to GBP using historical rates)

Recalculation is triggered automatically on create/edit/delete, but deferred to background for UI responsiveness.

## Security Notes

- `data/finance.db` is gitignored — never commit your financial data
- No authentication by default — add it before exposing to a network
- Database backups contain all financial data — store securely

## Licence

Personal use. Fork and modify freely.

---

**Built with love by a dolphin for personal finance management**
