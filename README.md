# 💰 Financisto Manager

A personal finance management system built with Python FastAPI and vanilla JavaScript. Import your Financisto data and manage your finances from your computer with a modern web interface.

![Dashboard](https://img.shields.io/badge/Status-Active-success)
![Python](https://img.shields.io/badge/Python-3.11+-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-green)

## 📋 Features

### Dashboard
- 📊 **Visual Statistics**: View total transactions, accounts, categories, and balance at a glance
- 📈 **Expenses by Category**: Doughnut chart showing your top 10 spending categories
- 💳 **Balance by Account**: Bar chart displaying balances across all accounts
- 📉 **Monthly Trend**: Line chart comparing income vs expenses over the last 12 months
- 🔝 **Top Payees**: Horizontal bar chart of your most frequent merchants

### Transaction Management
- ➕ **Quick Entry**: Fast transaction input with autocomplete
- 🏷️ **Hierarchical Categories**: Parent-child category selection
- 📍 **Location & Project Tracking**: Organise transactions by location and project
- 🔍 **Advanced Filters**: Filter by date range, account, category, or search text
- ✏️ **Edit & Delete**: Modify or remove transactions directly from the interface
- 📊 **Recent Transactions List**: View your last 50 transactions with full details

## 🛠️ Technology Stack

### Backend
- **FastAPI**: Modern Python web framework for building APIs
- **SQLAlchemy**: SQL toolkit and ORM
- **SQLite**: Lightweight database
- **Pandas**: Data manipulation and CSV import
- **Uvicorn**: ASGI server

### Frontend
- **Vanilla JavaScript**: No frameworks, just pure JS
- **Chart.js**: Beautiful, responsive charts
- **HTML5 & CSS3**: Modern, gradient-based design

## 📁 Project Structure

```
financisto-manager/
├── backend/
│   ├── __init__.py
│   ├── main.py                    # FastAPI application
│   ├── models.py                  # Database models
│   ├── schemas.py                 # Pydantic schemas
│   ├── database.py                # Database configuration
│   ├── create_tables.py           # Table creation script
│   └── import_financisto_csv.py   # CSV import utility
├── frontend/
│   ├── index.html                 # Dashboard page
│   ├── transactions.html          # Transaction management page
│   └── navbar.js                  # Navigation component
├── data/
│   └── finance.db                 # SQLite database (gitignored)
├── .gitignore
└── README.md
```

## 🚀 Getting Started

### Prerequisites

- Python 3.11 or higher
- pip (Python package manager)

### Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/magonji/financisto-manager.git
   cd financisto-manager
   ```

2. **Install dependencies**
   ```bash
   pip install fastapi uvicorn sqlalchemy python-multipart pandas questionary
   ```

3. **Create the database tables**
   ```bash
   python -m backend.create_tables
   ```

4. **Import your Financisto data (optional)**
   ```bash
   python -m backend.import_financisto_csv
   ```
   Follow the prompts to select your CSV file.

5. **Start the API server**
   ```bash
   python -m uvicorn backend.main:app --reload
   ```

6. **Open the frontend**
   - Open `frontend/index.html` in your web browser
   - Or navigate to `http://localhost:8000/docs` for the interactive API documentation

## 📊 Usage

### Dashboard
Navigate to `index.html` to view:
- Summary statistics of your finances
- Visual charts showing spending patterns
- Monthly trends and top merchants

### Managing Transactions
Navigate to `transactions.html` to:
- Add new transactions with the quick-entry form
- Filter existing transactions by multiple criteria
- Edit or delete transactions
- View recent transaction history

### API Endpoints

The FastAPI backend provides a RESTful API:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/accounts` | GET | List all accounts |
| `/accounts` | POST | Create new account |
| `/categories` | GET | List all categories |
| `/payees` | GET | List all payees |
| `/locations` | GET | List all locations |
| `/projects` | GET | List all projects |
| `/transactions` | GET | List transactions (with filters) |
| `/transactions` | POST | Create new transaction |
| `/transactions/{id}` | GET | Get specific transaction |
| `/transactions/{id}` | PUT | Update transaction |
| `/transactions/{id}` | DELETE | Delete transaction |
| `/dashboard/summary` | GET | Get dashboard statistics |

Full API documentation available at: `http://localhost:8000/docs`

## 📝 Database Schema

### Main Tables
- **accounts**: Bank accounts, cash, credit cards
- **categories**: Hierarchical expense/income categories
- **payees**: Merchants and payment recipients
- **locations**: Geographic locations
- **projects**: Project groupings for transactions
- **transactions**: Individual financial transactions

### Relationships
Transactions link to accounts, categories, payees, locations, and projects via foreign keys.

## 🔧 Development

### Running in Development Mode
```bash
python -m uvicorn backend.main:app --reload
```

The `--reload` flag enables auto-reload on code changes.

### Adding New Features

1. **Backend changes**: Edit files in `backend/`
2. **Frontend changes**: Edit `frontend/index.html` or `transactions.html`
3. **Database changes**: Update `backend/models.py` and recreate tables

## 🐛 Troubleshooting

### CORS Errors
If you see CORS errors in the browser console, ensure the CORS middleware is properly configured in `backend/main.py`.

### Database Issues
If you encounter database errors:
```bash
# Delete the database
del data\finance.db  # Windows
rm data/finance.db   # Mac/Linux

# Recreate tables
python -m backend.create_tables
```

### Import Errors
If CSV import fails:
- Ensure the CSV format matches Financisto export format
- Check for encoding issues (should be UTF-8)
- Verify all required columns are present

## 🔐 Security Notes

- The database file (`finance.db`) is gitignored to protect your financial data
- Never commit the `data/` folder to version control
- When deploying to production, add proper authentication
- Use environment variables for sensitive configuration

## 🚀 Future Enhancements

Potential features for future development:
- [ ] Budget tracking and alerts
- [ ] Recurring transaction templates
- [ ] Multi-currency support with conversion
- [ ] Export reports to PDF/Excel
- [ ] Mobile app (React Native)
- [ ] Cloud deployment (Railway/Render)
- [ ] Desktop app (Electron)
- [ ] User authentication
- [ ] Automated backups

## 📄 Licence

This project is for personal use. Feel free to fork and modify for your own needs.

## 🙏 Acknowledgements

- **Financisto**: Original Android app for personal finance
- **FastAPI**: For the excellent web framework
- **Chart.js**: For beautiful charts
- **SQLAlchemy**: For powerful ORM capabilities

## 📧 Contact

For questions or suggestions, open an issue on GitHub.

---

**Built with ❤️ for personal finance management**