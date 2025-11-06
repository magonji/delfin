# 🐬 Delfin

A personal finance management app built with Python, FastAPI, and vanilla JavaScript. Easily import your Financisto data and track, analyse, and manage your finances through a sleek, modern web interface. Right from your computer!

## 📋 Features

### Dashboard

- 📊 **Visual Statistics**: View total transactions, accounts, categories, and balance at a glance
- 💱 **Multi-Currency Support**: All amounts automatically converted to your most common currency
- 📈 **Monthly Expenses by Category**: Interactive doughnut chart with month selector showing your top 20 spending categories
- 🔝 **Top 10 Expenses**: Table showing the largest individual expenses for the selected month
- 💳 **Balance by Account**: Detailed table displaying balances in original and converted currencies
- 📉 **Monthly Trend**: Line chart comparing income vs expenses over the last 12 months
- 🏪 **Top Payees**: Horizontal bar chart of your most frequent merchants

### Transaction Management

- ➕ **Quick Entry**: Fast transaction input with autocomplete for payees
- 🔄 **Transfer Transactions**: Create transfers between accounts with currency conversion support
- 🏷️ **Hierarchical Categories**: Parent-child category selection
- 📍 **Location & Project Tracking**: Organise transactions by location and project
- 🔍 **Advanced Filters**: Filter by date range, account, category, or search text
- ✏️ **Edit & Delete**: Modify or remove transactions directly from the interface
- ✏️ **Bulk Edit**: Select multiple transactions or transfers to edit them all at once
- 📊 **Transaction List**: View all your transactions with running balances per account and total balance

### Loans & Credit Cards

- 💳 **Automatic Detection**: Distinguishes credit cards (3+ unique payees) from traditional loans
- 📋 **Loan Tracking**: Monitor borrowed amount, repaid amount, interest, and remaining balance
- 💰 **Credit Card Management**: Separate tracking of charges, fees/interest, and payments
- 📊 **Smart Categorisation**: Interest and fees identified by category ("Intereses y comisiones")
- ✅ **Completion Tracking**: Loans show paid-off status with green indicators
- 📈 **Progress Bars**: Visual representation of repayment progress

### Tools & Management

- 📁 **Category Management**: Edit parent categories and subcategories
- 💳 **Account Management**: Edit account names and currencies
- 👤 **Payee Management**: Edit and merge payee names with smart suggestions
- 📍 **Location Management**: Organise transaction locations
- 📋 **Project Management**: Track projects across transactions
- 📥 **Bank Statement Import**: Import transactions from CSV bank statements (Bank of Scotland, PayPal)
- 📤 **Export to CSV**: Export your transactions with flexible filters
- 💾 **Database Backup**: Download timestamped backups of your complete database

### Currency Management

- 💱 **Automatic Currency Conversion**: All amounts displayed in your most common currency
- 🌍 **Live Exchange Rates**: Fetches current rates from exchangerate-api.com
- 📅 **Historical Rates**: Stores exchange rate history for accurate conversions
- 🔄 **Manual Updates**: Update exchange rates on demand
- 💰 **30+ Currencies Supported**: Including GBP, EUR, USD, JPY, and many more

## 🛠️ Technology Stack

### Backend

- **FastAPI**: Modern Python web framework for building APIs
- **SQLAlchemy**: SQL toolkit and ORM
- **SQLite**: Lightweight database
- **Pandas**: Data manipulation and CSV import
- **Requests**: HTTP library for fetching exchange rates
- **Uvicorn**: ASGI server

### Frontend

- **Vanilla JavaScript**: No frameworks, just pure JS
- **Chart.js**: Beautiful, responsive charts
- **HTML5 & CSS3**: Modern, gradient-based design

## 📁 Project Structure

```
delfin/
├── backend/
│   ├── __init__.py
│   ├── main.py                    # FastAPI application with all endpoints
│   ├── models.py                  # Database models (SQLAlchemy)
│   ├── schemas.py                 # Pydantic schemas for API validation
│   ├── database.py                # Database configuration
│   └── balance_calculator.py      # Balance calculation utilities
├── frontend/
│   ├── index.html                 # Dashboard page
│   ├── transactions.html          # Transaction management page
│   ├── loans.html                 # Loans & credit cards page
│   ├── tools.html                 # Management tools page
│   └── navbar.js                  # Navigation component
├── data/
│   └── finance.db                 # SQLite database (created automatically, gitignored)
├── create_tables.py               # Database table creation script
├── import_financisto_csv.py       # CSV import utility with interactive prompts
├── initialise_database.py         # Initial setup script (balances, rates, stats)
├── update_database.py             # Database schema updater
├── update_exchange_rates.py       # Exchange rate updater script
├── clean_duplicate_categories.py  # Category deduplication utility
├── .gitignore
└── README.md
```

## 🚀 Getting Started

### Prerequisites

- Python 3.11 or higher
- pip (Python package manager)
- Internet connection (for fetching exchange rates)

### Installation for New Users

If you're starting fresh with a Financisto CSV export:

#### 1. **Clone the repository**

```bash
git clone https://github.com/magonji/delfin.git
cd delfin
```

#### 2. **Install dependencies**

```bash
pip install fastapi uvicorn sqlalchemy python-multipart pandas questionary requests
```

#### 3. **Create the database tables**

```bash
python create_tables.py
```

This creates an empty SQLite database with all necessary tables.

#### 4. **Import your Financisto data**

```bash
python import_financisto_csv.py
```

The script will:
- Ask you to select the folder containing your CSV file
- Show you a list of CSV files in that folder
- Ask for confirmation before importing
- Display progress as it imports your transactions

Example output:
```
Where is your CSV file located? /Users/yourname/Downloads
Which CSV file would you like to import?
❯ financisto_export_2024.csv

Import 'financisto_export_2024.csv'?
This will add transactions to the database.
Yes

Reading CSV file: /Users/yourname/Downloads/financisto_export_2024.csv
Found 1523 transactions to import
Imported 100 transactions...
Imported 200 transactions...
...

✅ Import complete!
   Imported: 1523
   Skipped: 0
```

#### 5. **Initialise calculated data**

```bash
python initialise_database.py
```

This comprehensive script will:
- Fetch and store the latest exchange rates
- Calculate running balances for all transactions
- Compute most common category/location/project for each payee

This step is **essential** for the dashboard and features to work properly.

#### 6. **Start the server**

```bash
uvicorn backend.main:app --reload
```

The API server will start at `http://localhost:8000`

#### 7. **Open the frontend**

Open `frontend/index.html` in your web browser to start using Delfin!

Alternatively, visit `http://localhost:8000/docs` for the interactive API documentation.

---

### Installation for Existing Installations

If you're updating from an older version of Delfin:

#### 1. **Update dependencies**

```bash
pip install --upgrade fastapi uvicorn sqlalchemy python-multipart pandas questionary requests
```

#### 2. **Update database schema**

```bash
python update_database.py
```

This will:
- Create any new tables or columns
- Initialise balance calculations if needed
- Recalculate payee statistics

#### 3. **Update exchange rates**

```bash
python update_exchange_rates.py
```

#### 4. **Clean duplicates (optional)**

If you notice duplicate categories:

```bash
python clean_duplicate_categories.py
```

---

## 📊 Usage

### Dashboard

Navigate to `index.html` to view:

- Summary statistics with currency conversion
- Monthly expenses by category (with month selector)
- Top 10 individual expenses for the selected month
- Balance by account in both original and converted currencies
- Monthly income vs expenses trend
- Top merchants ranked by spending

### Managing Transactions

Navigate to `transactions.html` to:

- Add new transactions with the quick-entry form
- Create transfers between accounts (with different currencies)
- Filter existing transactions by multiple criteria
- Edit or delete transactions individually
- Bulk edit multiple transactions or transfers at once
- View complete transaction history with running balances

### Loans & Credit Cards

Navigate to `loans.html` to:

- View all your loans and credit cards automatically detected
- See detailed breakdown of charges, payments, and interest/fees
- Track repayment progress with visual indicators
- Expand to see complete transaction history for each loan/card
- Monitor active vs paid-off loans separately

### Tools & Management

Navigate to `tools.html` to:

- Manage categories, accounts, payees, locations, and projects
- Import transactions from bank statement CSVs
- Export your data to CSV format
- Create database backups
- Update exchange rates manually

---

## 🔌 API Endpoints

The FastAPI backend provides a RESTful API. Here are the main endpoints:

### Accounts

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/accounts` | GET | List all accounts (with `include_closed` parameter) |
| `/accounts/with-balances` | GET | Get accounts with current balances |
| `/accounts` | POST | Create new account |

### Categories

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/categories` | GET | List all categories |
| `/categories` | POST | Create new category |

### Payees

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/payees` | GET | List all payees with their most common associations |
| `/payees` | POST | Create new payee |
| `/payees/{id}/recalculate-stats` | POST | Recalculate statistics for specific payee |
| `/payees/recalculate-all-stats` | POST | Recalculate statistics for all payees |

### Locations & Projects

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/locations` | GET | List all locations |
| `/locations` | POST | Create new location |
| `/projects` | GET | List all projects |
| `/projects` | POST | Create new project |

### Transactions

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/transactions` | GET | List transactions with filters |
| `/transactions` | POST | Create new transaction |
| `/transactions/{id}` | GET | Get specific transaction |
| `/transactions/{id}` | PUT | Update transaction |
| `/transactions/{id}` | DELETE | Delete transaction |
| `/transactions/transfer` | POST | Create transfer between accounts |
| `/transactions/transfers` | GET | List all transfers grouped |

### Exchange Rates

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/exchange-rates/latest` | GET | Get most recent exchange rates |
| `/exchange-rates/update` | POST | Manually trigger rate update |
| `/exchange-rates` | GET | Get historical exchange rates |

### Dashboard & Admin

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/dashboard/summary` | GET | Get dashboard statistics with currency conversion |
| `/admin/initialise-balances` | POST | Initialise balance calculations for all transactions |
| `/admin/recalculate-account-balances` | POST | Recalculate account balances |
| `/admin/backup-database` | POST | Create and download database backup |

Full API documentation available at: `http://localhost:8000/docs`

---

## 💱 Supported Currencies

The system supports 30+ currencies with automatic symbol detection:

- **Major**: GBP (£), EUR (€), USD ($), JPY (¥), CHF (Fr)
- **Americas**: CAD, BRL, MXN, ARS, CLP, COP, PEN
- **Asia-Pacific**: CNY, INR, AUD, NZD, SGD, HKD, KRW, THB, MYR
- **Europe**: SEK, NOK, DKK, PLN, RUB, TRY
- **Africa**: ZAR

Exchange rates are fetched from [exchangerate-api.com](https://www.exchangerate-api.com/) which provides free access without requiring an API key.

---

## 📊 Database Schema

### Main Tables

- **accounts**: Bank accounts, cash, credit cards
  - Includes: `currency`, `initial_balance`, `current_balance`, `is_active`
  
- **categories**: Hierarchical expense/income categories
  - Includes: `name`, `parent`, `type`
  
- **payees**: Merchants and payment recipients
  - Includes: `name`, cached most common `category_id`, `location_id`, `project_id`
  
- **locations**: Geographic locations
  
- **projects**: Project groupings for transactions
  
- **transactions**: Individual financial transactions
  - Includes: `date`, `amount`, `currency`, `note`
  - Foreign keys to: `account`, `category`, `payee`, `location`, `project`
  - Cached fields: `account_balance_after`, `total_balance_after`
  
- **exchange_rates**: Historical exchange rate data
  - Includes: `currency`, `rate`, `date`

### Balance Tracking

Delfin uses cached balance calculations for optimal performance:

- **`account_balance_after`**: Balance of the specific account after each transaction
- **`total_balance_after`**: Total balance across all accounts (in base currency) after each transaction

These balances are:
- Automatically calculated when transactions are created
- Automatically recalculated when transactions are edited or deleted
- Displayed in the transaction list for easy tracking

### Payee Statistics

To speed up transaction entry, payees store their most commonly used associations:

- **`most_common_category_id`**: The category most frequently used with this payee
- **`most_common_location_id`**: The location most frequently used with this payee
- **`most_common_project_id`**: The project most frequently used with this payee

These are automatically suggested when creating new transactions.

### Performance Optimisations

The database uses several composite indices for fast queries:
- `(account_id, date)` for account-specific transaction lists
- `(currency, date)` for currency-filtered queries
- `(date, amount)` for expense analysis
- `(category_id, date)` for category-based reports
- `(payee_id, date)` for payee analysis

---

## 🔧 Development

### Running in Development Mode

```bash
uvicorn backend.main:app --reload
```

The `--reload` flag enables auto-reload on code changes.

### Adding New Features

1. **Backend changes**: Edit files in `backend/`
   - Add models in `models.py`
   - Add schemas in `schemas.py`
   - Add endpoints in `main.py`

2. **Frontend changes**: Edit HTML files in `frontend/`
   - Dashboard: `index.html`
   - Transactions: `transactions.html`
   - Loans: `loans.html`
   - Tools: `tools.html`

3. **Database changes**: 
   - Update `backend/models.py`
   - Run `python update_database.py` to create new tables/columns
   - Create a migration script if needed for existing data

### Testing Exchange Rate Updates

```bash
# Test the exchange rate fetching
python update_exchange_rates.py

# Check what rates are stored
sqlite3 data/finance.db "SELECT * FROM exchange_rates ORDER BY date DESC LIMIT 10;"
```

---

## 🛠 Troubleshooting

### CORS Errors

If you see CORS errors in the browser console, ensure the CORS middleware is properly configured in `backend/main.py`. The default configuration allows all origins for development.

### Database Issues

If you encounter database errors:

1. **Back up your database first!**
   ```bash
   # Mac/Linux
   cp data/finance.db data/finance_backup_$(date +%Y%m%d_%H%M%S).db
   
   # Windows
   copy data\finance.db data\finance_backup_%date:~-4,4%%date:~-10,2%%date:~-7,2%.db
   ```

2. **If database is corrupted:**
   ```bash
   # Mac/Linux
   rm data/finance.db
   
   # Windows
   del data\finance.db
   
   # Recreate and reimport
   python create_tables.py
   python import_financisto_csv.py
   python initialise_database.py
   ```

### Balance Calculation Issues

If balances seem incorrect:

**Option 1**: Use the API endpoint
```bash
curl -X POST http://localhost:8000/admin/initialise-balances
```

**Option 2**: Run the initialisation script
```bash
python initialise_database.py
```

### Exchange Rate Issues

**Problem**: "No exchange rates found" or conversion errors

**Solution**:
```bash
# Update exchange rates
python update_exchange_rates.py

# Verify rates were stored
sqlite3 data/finance.db "SELECT COUNT(*) FROM exchange_rates;"
```

**Problem**: API request fails

**Solution**: 
- Check your internet connection
- The free API has rate limits; wait a few minutes and try again
- If persistent, check [exchangerate-api.com status](https://www.exchangerate-api.com/)

### Duplicate Categories

If you see repeated categories in dropdowns:

```bash
python clean_duplicate_categories.py
```

This script will:
- Find all duplicate categories (same name + parent)
- Merge them by reassigning transactions to one category
- Delete the duplicates

### Import Errors

If CSV import fails:

- Ensure the CSV format matches Financisto export format
- Check for encoding issues (should be UTF-8)
- Verify all required columns are present
- Check the console output for specific error messages

### Payee Autocomplete Not Working

If payee suggestions aren't showing common associations:

```bash
# Recalculate statistics for all payees
curl -X POST http://localhost:8000/payees/recalculate-all-stats
```

---

## 🔒 Security Notes

- The database file (`finance.db`) is gitignored to protect your financial data
- **Never commit the `data/` folder to version control**
- When deploying to production, add proper authentication
- Use environment variables for sensitive configuration
- Exchange rate API calls don't require authentication but are rate-limited
- Database backups include all sensitive financial data - store them securely

---

## 🚀 Future Enhancements

Potential features for future development:

- [ ] Budget tracking and alerts
- [ ] Recurring transaction templates
- [ ] Export reports to PDF/Excel
- [ ] Cloud deployment (Railway/Render)
- [ ] Desktop app (Electron)
- [ ] User authentication
- [ ] Automated Google Drive backup
- [x] Multi-currency support with conversion ✅
- [x] Live exchange rate updates ✅
- [x] Cached balance calculations ✅
- [x] Bulk transaction editing ✅
- [x] Loan and credit card tracking ✅
- [x] Database backup functionality ✅
- [ ] Custom exchange rate entry (for historical accuracy)
- [ ] Investment portfolio tracking
- [ ] Bill reminders and notifications

---

## 📝 Changelog

### Version 3.1 (Current)
- ✨ Added `initialise_database.py` for streamlined first-time setup
- ✨ Improved `update_database.py` with automatic balance initialisation
- ✨ Added `clean_duplicate_categories.py` utility
- ✨ Payee statistics now include most common category/location/project
- 📚 Simplified README with clearer installation instructions
- 🔧 Better error handling in import scripts

### Version 3.0
- ✨ Added cached balance calculations for improved performance
- ✨ Implemented bulk editing for transactions and transfers
- ✨ New Loans & Credit Cards page with automatic detection
- ✨ Smart categorisation of charges vs fees/interest
- ✨ Database backup functionality with timestamps
- 🐬 Rebranded from "Financisto Manager" to "Delfin"
- 🎨 Enhanced UI with better transaction displays
- 📊 Running balance shown for each transaction
- 🔧 Multiple bug fixes and performance improvements

### Version 2.0
- ✨ Added multi-currency support with automatic conversion
- ✨ Integrated live exchange rate fetching from exchangerate-api.com
- ✨ New ExchangeRate model for historical rate storage
- 🎨 Redesigned "Balance by Account" as a detailed table
- 🎨 Monthly category expenses with interactive month selector
- 📊 Added "Top 10 Expenses" table for selected month
- 🔄 Support for transfers between different currency accounts
- 💱 All dashboard statistics now display in base currency
- 🌍 Support for 30+ currencies with proper symbols

### Version 1.0
- 🎉 Initial release
- ✅ Basic transaction management
- ✅ Dashboard with charts
- ✅ CSV import from Financisto
- ✅ SQLite database backend

---

## 📄 Licence

This project is for personal use. Feel free to fork and modify for your own needs.

---

## 🙏 Acknowledgements

- **Financisto**: Original Android app for personal finance
- **FastAPI**: For the excellent web framework
- **Chart.js**: For beautiful charts
- **SQLAlchemy**: For powerful ORM capabilities
- **exchangerate-api.com**: For providing free exchange rate data

---

## 📧 Contact

For questions or suggestions, open an issue on GitHub.

---

**Built with ❤️ by a dolphin for personal finance management** 🐬