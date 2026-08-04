"""
SQLAlchemy models for the Delfin finance application.
"""
from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Text, Index, event
from sqlalchemy.orm import relationship
from datetime import datetime
from backend.database import Base


class Account(Base):
    """Financial account (e.g., Monzo, Bank of Scotland)."""
    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False, index=True)
    type = Column(String)
    currency = Column(String, default="GBP", index=True)
    initial_balance = Column(Float, default=0.0)
    current_balance = Column(Float, default=0.0)
    is_active = Column(Integer, default=1, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    transactions = relationship("Transaction", back_populates="account")


class Category(Base):
    """Expense/income categories with hierarchical structure."""
    __tablename__ = "categories"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, index=True)
    parent = Column(String, index=True)
    type = Column(String, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    transactions = relationship("Transaction", back_populates="category")


class Payee(Base):
    """Payees/merchants (e.g., ASDA, Lidl, Amazon)."""
    __tablename__ = "payees"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False, index=True)
    most_common_category_id = Column(Integer, ForeignKey("categories.id"), nullable=True, index=True)
    most_common_location_id = Column(Integer, ForeignKey("locations.id"), nullable=True, index=True)
    most_common_project_id = Column(Integer, ForeignKey("projects.id"), nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    transactions = relationship("Transaction", back_populates="payee", foreign_keys="Transaction.payee_id")
    most_common_category = relationship("Category", foreign_keys=[most_common_category_id])
    most_common_location = relationship("Location", foreign_keys=[most_common_location_id])
    most_common_project = relationship("Project", foreign_keys=[most_common_project_id])


class Location(Base):
    """Geographical locations (e.g., Glasgow, Madrid)."""
    __tablename__ = "locations"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    transactions = relationship("Transaction", back_populates="location")


class Project(Base):
    """Projects for grouping transactions."""
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False, index=True)
    description = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    transactions = relationship("Transaction", back_populates="project")


class Transaction(Base):
    """Individual financial transactions."""
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True)
    date = Column(DateTime, nullable=False, index=True)
    amount = Column(Float, nullable=False)
    currency = Column(String, default="GBP", index=True)
    note = Column(Text)
    
    account_id = Column(Integer, ForeignKey("accounts.id"), index=True)
    category_id = Column(Integer, ForeignKey("categories.id"), index=True)
    payee_id = Column(Integer, ForeignKey("payees.id"), index=True)
    location_id = Column(Integer, ForeignKey("locations.id"), index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), index=True)

    # A split transaction — one purchase spread over several categories,
    # projects or notes — is stored as one row per line, all sharing the id of
    # the first line. Keeping the lines as ordinary transactions means balances,
    # filters and every category-based report stay correct without knowing
    # anything about splits. NULL on a plain single-line transaction.
    split_group_id = Column(Integer, nullable=True, index=True)

    account_balance_after = Column(Float, nullable=True, index=True)
    total_balance_after = Column(Float, nullable=True, index=True)
    
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    account = relationship("Account", back_populates="transactions")
    category = relationship("Category", back_populates="transactions")
    payee = relationship("Payee", back_populates="transactions")
    location = relationship("Location", back_populates="transactions")
    project = relationship("Project", back_populates="transactions")
    
    __table_args__ = (
        # Composite indexes for common query patterns
        Index('idx_transaction_account_date', 'account_id', 'date'),
        Index('idx_transaction_currency_date', 'currency', 'date'),
        Index('idx_transaction_date_amount', 'date', 'amount'),
        Index('idx_transaction_category_date', 'category_id', 'date'),
        Index('idx_transaction_payee_date', 'payee_id', 'date'),
        
        # Critical index for balance recalculation (account + date ASC + id ASC)
        Index('idx_transaction_account_date_id_asc', 'account_id', 'date', 'id'),
        
        # Index for location-based queries (transfers use location_id heavily)
        Index('idx_transaction_location_date', 'location_id', 'date'),
        
        # Covering index for the main transaction listing query
        # Helps with: ORDER BY date DESC, id DESC with filters
        Index('idx_transaction_date_desc_id_desc', 'date', 'id'),

        # Fetching the lines of a split, in entry order
        Index('idx_transaction_split_group', 'split_group_id', 'id'),
    )


class ExchangeRate(Base):
    """Historical exchange rates (GBP as base currency)."""
    __tablename__ = "exchange_rates"

    id = Column(Integer, primary_key=True, index=True)
    currency = Column(String, nullable=False, index=True)
    rate = Column(Float, nullable=False)
    date = Column(DateTime, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index('idx_exchange_rate_currency_date', 'currency', 'date'),
    )


class Budget(Base):
    """Monthly budget targets."""
    __tablename__ = "budgets"

    id = Column(Integer, primary_key=True, index=True)
    year_month = Column(String, unique=True, nullable=False, index=True)  # Format: "2025-01"
    amount = Column(Float, nullable=False)
    currency = Column(String, default="GBP")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class RecurringExpense(Base):
    """Recurring expenses (subscriptions, rent, etc.) with variable frequencies."""
    __tablename__ = "recurring_expenses"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, index=True)
    payee_id = Column(Integer, ForeignKey("payees.id"), nullable=True, index=True)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=True, index=True)
    amount = Column(Float, nullable=False)
    currency = Column(String, default="GBP")
    day_of_month = Column(Integer, nullable=True)  # Approximate day (1-31)
    frequency = Column(String, default="monthly")  # monthly, quarterly, biannual, annual
    start_month = Column(Integer, nullable=True)  # Month when it's charged (1-12), for non-monthly
    is_active = Column(Integer, default=1, index=True)  # 1=active, 0=inactive
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    payee = relationship("Payee")
    category = relationship("Category")
    amount_history = relationship("RecurringExpenseHistory", back_populates="recurring_expense", order_by="RecurringExpenseHistory.effective_from.desc()")


class RecurringExpenseHistory(Base):
    """Historical record of recurring expense amounts."""
    __tablename__ = "recurring_expense_history"

    id = Column(Integer, primary_key=True, index=True)
    recurring_expense_id = Column(Integer, ForeignKey("recurring_expenses.id"), nullable=False, index=True)
    amount = Column(Float, nullable=False)
    currency = Column(String, default="GBP")
    effective_from = Column(DateTime, nullable=False, index=True)  # When this amount became effective
    created_at = Column(DateTime, default=datetime.utcnow)

    recurring_expense = relationship("RecurringExpense", back_populates="amount_history")

    __table_args__ = (
        Index('idx_history_expense_date', 'recurring_expense_id', 'effective_from'),
    )


class RecurringExpensePayment(Base):
    """Manual payment overrides for recurring expenses per month."""
    __tablename__ = "recurring_expense_payments"

    id = Column(Integer, primary_key=True, index=True)
    recurring_expense_id = Column(Integer, ForeignKey("recurring_expenses.id"), nullable=False)
    year_month = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    recurring_expense = relationship("RecurringExpense")

    __table_args__ = (
        Index('idx_rec_payment_lookup', 'recurring_expense_id', 'year_month', unique=True),
    )


class PlannedExpense(Base):
    """One-time planned expenses for a specific month."""
    __tablename__ = "planned_expenses"

    id = Column(Integer, primary_key=True, index=True)
    year_month = Column(String, nullable=False, index=True)  # Format: "2025-01"
    name = Column(String, nullable=False)
    amount = Column(Float, nullable=False)
    currency = Column(String, default="GBP")
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=True, index=True)
    is_paid = Column(Integer, default=0, index=True)  # 0=pending, 1=paid
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    category = relationship("Category")


class BudgetItem(Base):
    """
    A budget definition: a fixed expense, an expected income, or a planned
    (budgetable) expense. These are templates — each month is materialised from
    them into ``BudgetMonthLine`` rows, which is what makes past months immutable.
    """
    __tablename__ = "budget_items"

    id = Column(Integer, primary_key=True, index=True)
    kind = Column(String, nullable=False, index=True)  # fixed | income | planned
    name = Column(String, nullable=False)
    amount = Column(Float, nullable=False, default=0.0)
    currency = Column(String, default="GBP")
    is_estimated = Column(Integer, default=0)  # 1 = amount is a guess (e.g. an MOT)

    # Recurrence: every `interval_count` `interval_unit`, starting at `first_date`.
    first_date = Column(DateTime, nullable=True)
    interval_count = Column(Integer, default=1)
    interval_unit = Column(String, default="month")  # once | day | week | month | year

    # Which day of the month it lands on, for monthly and yearly rhythms:
    # exact = the day of `first_date`; the others count working days (Mon–Fri)
    # from the start or the end of the month, e.g. wages on the second-to-last.
    day_rule = Column(String, default="exact")  # exact | working_from_start | working_from_end
    day_ordinal = Column(Integer, nullable=True)  # 1 = first/last working day

    # Used to detect whether the expense has been paid this month.
    payee_id = Column(Integer, ForeignKey("payees.id"), nullable=True, index=True)
    # Savings account a prorated expense sets money aside in, or the debt/savings
    # account a fixed transfer feeds.
    set_aside_account_id = Column(Integer, ForeignKey("accounts.id"), nullable=True, index=True)

    starts_ym = Column(String, nullable=False, index=True)  # first month it applies to
    ends_ym = Column(String, nullable=True, index=True)     # last month, NULL = open-ended
    is_active = Column(Integer, default=1, index=True)

    # Editing an item from a given month on splits it in two: the old row is
    # capped at the month before and a new row carries the new values from
    # there. Both keep the same series_id, so "this rent" stays one thing
    # across its versions. NULL on rows written before versioning existed,
    # which the backfill in database._ensure_columns fills with the row's id.
    series_id = Column(Integer, nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    payee = relationship("Payee")
    set_aside_account = relationship("Account")
    accounts = relationship("BudgetItemAccount", cascade="all, delete-orphan", back_populates="item")
    categories = relationship("BudgetItemCategory", cascade="all, delete-orphan", back_populates="item")

    __table_args__ = (
        Index('idx_budget_item_kind_active', 'kind', 'is_active'),
        Index('idx_budget_item_series', 'series_id', 'starts_ym'),
    )


class BudgetItemAccount(Base):
    """Accounts whose spending counts towards a planned expense."""
    __tablename__ = "budget_item_accounts"

    id = Column(Integer, primary_key=True, index=True)
    item_id = Column(Integer, ForeignKey("budget_items.id"), nullable=False, index=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False, index=True)

    item = relationship("BudgetItem", back_populates="accounts")
    account = relationship("Account")

    __table_args__ = (
        Index('idx_budget_item_account', 'item_id', 'account_id', unique=True),
    )


class BudgetItemCategory(Base):
    """Categories (or subcategories) whose spending counts towards a planned expense."""
    __tablename__ = "budget_item_categories"

    id = Column(Integer, primary_key=True, index=True)
    item_id = Column(Integer, ForeignKey("budget_items.id"), nullable=False, index=True)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=False, index=True)

    item = relationship("BudgetItem", back_populates="categories")
    category = relationship("Category")

    __table_args__ = (
        Index('idx_budget_item_category', 'item_id', 'category_id', unique=True),
    )


class BudgetMonthLine(Base):
    """
    One budget line materialised for one month — the auditable record of what was
    budgeted at the time. Lines of past months are frozen and never regenerated,
    so editing an item changes the present and the future but never the past.
    """
    __tablename__ = "budget_month_lines"

    id = Column(Integer, primary_key=True, index=True)
    year_month = Column(String, nullable=False, index=True)  # Format: "2026-07"
    # NULL once the defining item is deleted — the line still stands on its own.
    item_id = Column(Integer, ForeignKey("budget_items.id"), nullable=True, index=True)
    kind = Column(String, nullable=False, index=True)

    name = Column(String, nullable=False)
    amount = Column(Float, nullable=False, default=0.0)   # budgeted for this month
    full_amount = Column(Float, nullable=True)            # the charge itself, when prorated
    occurrences = Column(Integer, default=1)              # times it lands this month
    is_prorated = Column(Integer, default=0)
    period_months = Column(Float, nullable=True)          # for "£600 over 6 months"
    is_estimated = Column(Integer, default=0)
    currency = Column(String, default="GBP")

    payee_id = Column(Integer, ForeignKey("payees.id"), nullable=True, index=True)
    set_aside_account_id = Column(Integer, ForeignKey("accounts.id"), nullable=True, index=True)
    account_ids = Column(Text)   # JSON list, snapshot for planned expenses
    category_ids = Column(Text)  # JSON list, snapshot for planned expenses
    due_days = Column(Text)      # JSON list of days of the month it falls on

    # NULL = decide from transactions, 0 = forced pending, 1 = forced paid.
    paid_override = Column(Integer, nullable=True)
    is_frozen = Column(Integer, default=0, index=True)
    source = Column(String, default="template")  # template | manual
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    item = relationship("BudgetItem")
    payee = relationship("Payee")
    set_aside_account = relationship("Account")

    __table_args__ = (
        Index('idx_budget_line_month_item', 'year_month', 'item_id'),
        Index('idx_budget_line_month_kind', 'year_month', 'kind'),
    )


class Loan(Base):
    """
    The agreed terms of a loan.

    Loans are still *detected* from the movements of an account — an account
    whose first transaction is negative, with few payees. This row adds what the
    movements cannot say: the rate, the term and the rhythm of the instalments.
    With it the amortisation is computed exactly instead of estimated by XIRR,
    and both figures are shown side by side. One row per account at most.
    """
    __tablename__ = "loans"

    id = Column(Integer, primary_key=True, index=True)
    # The debt account itself — the one whose balance is the outstanding capital.
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False, unique=True, index=True)
    name = Column(String, nullable=False)
    principal = Column(Float, nullable=False)
    currency = Column(String, default="GBP")
    annual_rate = Column(Float, nullable=False, default=0.0)  # nominal annual %, e.g. 3.75
    open_date = Column(DateTime, nullable=False)

    # What the loan cost to arrange — an arrangement, product or broker fee.
    # It is not interest, so it never enters the nominal rate, but it is money
    # the loan cost, so it does enter the effective rate.
    opening_fee = Column(Float, default=0.0)
    # upfront     = paid when the loan is drawn down, out of the money received
    # capitalised = added to the debt and amortised along with the capital
    fee_treatment = Column(String, default="upfront")

    # Interest starts on the drawdown, but the first instalment rarely falls a
    # whole period later — draw down mid-month and pay on the 1st and the first
    # period is a stub. NULL means "one payment period after the drawdown, on the
    # day the rule gives"; setting it says exactly when the first one is due.
    first_payment_date = Column(DateTime, nullable=True)

    # A standing charge for having the loan at all — an administration or account
    # fee, charged every ``recurring_fee_months`` from the opening date. It has
    # its own rhythm because it rarely shares the instalment's.
    recurring_fee = Column(Float, default=0.0)
    recurring_fee_months = Column(Integer, default=1)

    # Percentage of the capital outstanding, charged for settling early. It never
    # enters the schedule or the effective rate — both assume the loan runs to
    # term — and is only used to price settling the loan today.
    early_repayment_fee_pct = Column(Float, default=0.0)

    # Duration, from which the number of instalments follows.
    term_count = Column(Integer, nullable=False, default=1)
    term_unit = Column(String, default="year")  # month | year

    # french            = constant instalment, the usual repayment mortgage
    # interest_only     = interest each period, capital repaid at the end
    # constant_principal = constant capital, falling instalment
    repayment_type = Column(String, default="french")

    # How often interest is charged, and how often an instalment is paid, both in
    # months. They usually match; when they don't, interest accrued between
    # instalments is compounded into the balance until the next one.
    interest_months = Column(Integer, default=1)
    payment_months = Column(Integer, default=1)
    # Daily accrual is a different animal from a monthly charge, not a shorter
    # one: interest follows the actual days in each period, so a February
    # instalment carries less than a March one. ``interest_months`` is ignored
    # when this is "day".
    interest_unit = Column(String, default="month")  # month | day

    # Which day of the month the instalment lands on. Same rules as BudgetItem,
    # so a loan paid on the first working day is expressed the same way here.
    day_rule = Column(String, default="exact")  # exact | working_from_start | working_from_end
    day_ordinal = Column(Integer, nullable=True)   # 1 = first/last working day
    day_of_month = Column(Integer, nullable=True)  # only for day_rule == exact

    lender_payee_id = Column(Integer, ForeignKey("payees.id"), nullable=True, index=True)
    # Account the money was paid into when the loan was drawn down.
    disbursement_account_id = Column(Integer, ForeignKey("accounts.id"), nullable=True, index=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    account = relationship("Account", foreign_keys=[account_id])
    disbursement_account = relationship("Account", foreign_keys=[disbursement_account_id])
    lender = relationship("Payee")


class CategoryBucket(Base):
    """Maps one of the user's categories to a kakeibo bucket."""
    __tablename__ = "category_buckets"

    id = Column(Integer, primary_key=True, index=True)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=False, unique=True, index=True)
    # essentials | indulgences | culture | unexpected
    bucket = Column(String, nullable=False, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    category = relationship("Category")


# --- Event listeners to round monetary amounts before saving ---

@event.listens_for(Transaction, 'before_insert')
@event.listens_for(Transaction, 'before_update')
def round_transaction_amounts(mapper, connection, target):
    """Round monetary amounts to 2 decimal places."""
    if target.amount is not None:
        target.amount = round(target.amount, 2)
    if target.account_balance_after is not None:
        target.account_balance_after = round(target.account_balance_after, 2)
    if target.total_balance_after is not None:
        target.total_balance_after = round(target.total_balance_after, 2)


@event.listens_for(Account, 'before_insert')
@event.listens_for(Account, 'before_update')
def round_account_balances(mapper, connection, target):
    """Round account balances to 2 decimal places."""
    if target.initial_balance is not None:
        target.initial_balance = round(target.initial_balance, 2)
    if target.current_balance is not None:
        target.current_balance = round(target.current_balance, 2)


@event.listens_for(ExchangeRate, 'before_insert')
@event.listens_for(ExchangeRate, 'before_update')
def round_exchange_rate(mapper, connection, target):
    """Round exchange rates to 6 decimal places."""
    if target.rate is not None:
        target.rate = round(target.rate, 6)


@event.listens_for(Budget, 'before_insert')
@event.listens_for(Budget, 'before_update')
def round_budget_amount(mapper, connection, target):
    """Round budget amount to 2 decimal places."""
    if target.amount is not None:
        target.amount = round(target.amount, 2)


@event.listens_for(RecurringExpense, 'before_insert')
@event.listens_for(RecurringExpense, 'before_update')
def round_recurring_amount(mapper, connection, target):
    """Round recurring expense amount to 2 decimal places."""
    if target.amount is not None:
        target.amount = round(target.amount, 2)


@event.listens_for(RecurringExpenseHistory, 'before_insert')
def round_history_amount(mapper, connection, target):
    """Round history amount to 2 decimal places."""
    if target.amount is not None:
        target.amount = round(target.amount, 2)


@event.listens_for(PlannedExpense, 'before_insert')
@event.listens_for(PlannedExpense, 'before_update')
def round_planned_amount(mapper, connection, target):
    """Round planned expense amount to 2 decimal places."""
    if target.amount is not None:
        target.amount = round(target.amount, 2)


@event.listens_for(BudgetItem, 'before_insert')
@event.listens_for(BudgetItem, 'before_update')
def round_budget_item_amount(mapper, connection, target):
    """Round budget item amount to 2 decimal places."""
    if target.amount is not None:
        target.amount = round(target.amount, 2)


@event.listens_for(Loan, 'before_insert')
@event.listens_for(Loan, 'before_update')
def round_loan_principal(mapper, connection, target):
    """Round the borrowed capital and the fees to 2 decimal places."""
    if target.principal is not None:
        target.principal = round(target.principal, 2)
    if target.opening_fee is not None:
        target.opening_fee = round(target.opening_fee, 2)
    if target.recurring_fee is not None:
        target.recurring_fee = round(target.recurring_fee, 2)


@event.listens_for(BudgetMonthLine, 'before_insert')
@event.listens_for(BudgetMonthLine, 'before_update')
def round_budget_line_amounts(mapper, connection, target):
    """Round budget line amounts to 2 decimal places."""
    if target.amount is not None:
        target.amount = round(target.amount, 2)
    if target.full_amount is not None:
        target.full_amount = round(target.full_amount, 2)