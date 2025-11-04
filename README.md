# 🐬 Delfin

A personal finance management system built with Python FastAPI and vanilla JavaScript. Import your Financisto data and manage your finances from your computer with a modern web interface.

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

- ➕ **Quick Entry**: Fast transaction input with autocomplete
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
- 👤 **Payee Management**: Edit and merge payee names
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
│   ├── main.py                    # FastAPI application
│   ├── models.py                  # Database models (including ExchangeRate)
│   ├── schemas.py                 # Pydantic schemas
│   ├── database.py                # Database configuration
│   └── balance_calculator.py      # Balance calculation utilities
├── frontend/
│   ├── index.html                 # Dashboard page
│   ├── transactions.html          # Transaction management page
│   ├── loans.html                 # Loans & credit cards page
│   ├── tools.html                 # Management tools page
│   └── navbar.js                  # Navigation component
├── data/
│   └── finance.db                 # SQLite database (gitignored)
├── create_tables.py               # Table creation script
├── import_financisto_csv.py       # CSV import utility
├── update_exchange_rates.py       # Exchange rate updater script
├── update_database.py             # Database schema updater
├── migrate_add_balances.py        # Migration script for balance columns
├── clean_duplicate_categories.py  # Category deduplication utility
├── .gitignore
└── README.md
```

## 🚀 Getting Started

### Prerequisites

- Python 3.11 or higher
- pip (Python package manager)
- Internet connection (for fetching exchange rates)

### Installation

1. **Clone the repository**

   ```bash
   git clone https://github.com/magonji/delfin.git
   cd delfin
   ```

2. **Install dependencies**

   ```bash
   pip install fastapi uvicorn sqlalchemy python-multipart pandas questionary requests
   ```

3. **Create the database tables**

   ```bash
   python create_tables.py
   ```

4. **Import your Financisto data (optional)**

   ```bash
   python import_financisto_csv.py
   ```

   Follow the prompts to select your CSV file.

5. **Update exchange rates**

   ```bash
   python update_exchange_rates.py
   ```

   This will fetch the latest exchange rates for all currencies in your transactions.

6. **Start the API server**

   ```bash
   uvicorn backend.main:app --reload
   ```

7. **Open the frontend**
   - Open `frontend/index.html` in your web browser
   - Or navigate to `http://localhost:8000/docs` for the interactive API documentation

### First Time Setup with Existing Data

If you're setting up Delfin with existing data:

1. Complete steps 1-4 above to install and import your data
2. **Important**: Run the exchange rate updater:

   ```bash
   python update_exchange_rates.py
   ```

3. You should see output like:

   ```
   🔄 Updating exchange rates...
   📊 Currencies in use: GBP, EUR, USD
      ✅ Added GBP: 1.0
      ✅ Added EUR: 1.17
      ✅ Added USD: 1.27
   
   ✅ Successfully updated 3 exchange rates!
   ```

4. If you have duplicate categories, run:
   
   ```bash
   python clean_duplicate_categories.py
   ```

5. Initialise balance calculations:
   ```bash
   python migrate_add_balances.py
   ```
6. Start the server and enjoy your multi-currency dashboard!

### Updating Database Schema (For Existing Installations)

If you're upgrading from an older version:

```bash
python update_database.py
python migrate_add_balances.py
python update_exchange_rates.py
```

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

- Edit categories, accounts, payees, locations, and projects
- Import bank statements from CSV files
- Export transactions to CSV with custom filters
- Download database backups with timestamps

### Updating Exchange Rates

Exchange rates can be updated in two ways:

1. **Manual script execution**:

   ```bash
   python update_exchange_rates.py
   ```

2. **Via API** (from the dashboard or any HTTP client):

   ```bash
   curl -X POST http://localhost:8000/exchange-rates/update
   ```

**Recommendation**: Set up a daily cron job or scheduled task to keep rates current:

```bash
# Linux/Mac - Add to crontab (runs daily at 2 AM)
0 2 * * * cd /path/to/delfin && python update_exchange_rates.py

# Windows - Use Task Scheduler to run the script daily
```

## 🌐 API Endpoints

The FastAPI backend provides a RESTful API:

### Core Resources

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/accounts` | GET | List all accounts |
| `/accounts` | POST | Create new account |
| `/accounts/{id}` | PUT | Update account |
| `/categories` | GET | List all categories |
| `/categories` | POST | Create new category |
| `/categories/{id}` | PUT | Update category |
| `/payees` | GET | List all payees |
| `/payees` | POST | Create new payee |
| `/payees/{id}` | PUT | Update payee |
| `/locations` | GET | List all locations |
| `/locations` | POST | Create new location |
| `/locations/{id}` | PUT | Update location |
| `/projects` | GET | List all projects |
| `/projects` | POST | Create new project |
| `/projects/{id}` | PUT | Update project |

### Transactions

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/transactions` | GET | List transactions (with filters) |
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
| `/admin/backup-database` | POST | Create and download database backup |

Full API documentation available at: `http://localhost:8000/docs`

## 💱 Supported Currencies

The system supports 30+ currencies with automatic symbol detection:

- **Major**: GBP (£), EUR (€), USD ($), JPY (¥), CHF (Fr)
- **Americas**: CAD, BRL, MXN, ARS, CLP, COP, PEN
- **Asia-Pacific**: CNY, INR, AUD, NZD, SGD, HKD, KRW, THB, MYR
- **Europe**: SEK, NOK, DKK, PLN, RUB, TRY
- **Africa**: ZAR

Exchange rates are fetched from [exchangerate-api.com](https://www.exchangerate-api.com/) which provides free access without requiring an API key.

## 📊 Database Schema

### Main Tables

- **accounts**: Bank accounts, cash, credit cards (with currency)
- **categories**: Hierarchical expense/income categories
- **payees**: Merchants and payment recipients
- **locations**: Geographic locations
- **projects**: Project groupings for transactions
- **transactions**: Individual financial transactions (with currency, cached balances)
- **exchange_rates**: Historical exchange rate data

### Balance Tracking

- Transactions include `account_balance_after` and `total_balance_after` columns
- Balances are calculated and cached for performance
- Automatically recalculated when transactions are added, edited, or deleted

### Relationships

- Transactions link to accounts, categories, payees, locations, and projects via foreign keys
- Exchange rates are indexed by currency and date for efficient lookups
- The system automatically determines the base currency from transaction frequency

## 🔧 Development

### Running in Development Mode

```bash
uvicorn backend.main:app --reload
```

The `--reload` flag enables auto-reload on code changes.

### Adding New Features

1. **Backend changes**: Edit files in `backend/`
2. **Frontend changes**: Edit HTML files in `frontend/`
3. **Database changes**: 
   - Update `backend/models.py`
   - Create migration script if needed
   - Run migration

### Testing Exchange Rate Updates

```bash
# Test the exchange rate fetching
python update_exchange_rates.py

# Check what rates are stored
sqlite3 data/finance.db "SELECT * FROM exchange_rates ORDER BY date DESC LIMIT 10;"
```

## 🛠 Troubleshooting

### CORS Errors

If you see CORS errors in the browser console, ensure the CORS middleware is properly configured in `backend/main.py`.

### Database Issues

If you encounter database errors:
```bash
# Back up your database first!
python -c "import shutil; from datetime import datetime; shutil.copy('data/finance.db', f'data/finance_backup_{datetime.now().strftime(\"%Y%m%d_%H%M%S\")}.db')"

# Then recreate if necessary
rm data/finance.db   # Mac/Linux
del data\finance.db  # Windows

python create_tables.py
python import_financisto_csv.py
```

### Balance Calculation Issues

If balances seem incorrect:
```bash
# Reinitialise all balance calculations
curl -X POST http://localhost:8000/admin/initialise-balances
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

### Import Errors

If CSV import fails:

- Ensure the CSV format matches Financisto export format
- Check for encoding issues (should be UTF-8)
- Verify all required columns are present

## 🔒 Security Notes

- The database file (`finance.db`) is gitignored to protect your financial data
- Never commit the `data/` folder to version control
- When deploying to production, add proper authentication
- Use environment variables for sensitive configuration
- Exchange rate API calls don't require authentication but are rate-limited
- Database backups include all sensitive financial data - store them securely

## 🚀 Future Enhancements

Potential features for future development:

- [ ] Budget tracking and alerts
- [ ] Recurring transaction templates
- [ ] Export reports to PDF/Excel
- [ ] Mobile app (React Native)
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
- [ ] Cryptocurrency support
- [ ] Bill reminders and notifications


## 📝 Changelog

### Version 3.0 (Current)
- ✨ Added cached balance calculations for improved performance
- ✨ Implemented bulk editing for transactions and transfers
- ✨ New Loans & Credit Cards page with automatic detection
- ✨ Smart categorisation of charges vs fees/interest
- ✨ Database backup functionality with timestamps
- ✨ Category deduplication utility
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

## 📄 Licence

This project is for personal use. Feel free to fork and modify for your own needs.

## 🙏 Acknowledgements

- **Financisto**: Original Android app for personal finance
- **FastAPI**: For the excellent web framework
- **Chart.js**: For beautiful charts
- **SQLAlchemy**: For powerful ORM capabilities
- **exchangerate-api.com**: For providing free exchange rate data

## 📧 Contact

For questions or suggestions, open an issue on GitHub.

---

**Built with ❤️ by a dolphin for personal finance management** 🐬