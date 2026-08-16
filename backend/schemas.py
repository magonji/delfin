"""
Pydantic schemas for request/response validation.
"""
from pydantic import BaseModel, field_validator, model_validator
from datetime import datetime
from typing import Optional, List


# --- Account schemas ---

# Financisto's vocabulary, which is what an imported database already speaks and
# what the exporter can hand back without flattening. PAYPAL is accepted but no
# longer offered: an imported account carrying it must stay editable, or renaming
# it would fail validation on its own current value.
ACCOUNT_TYPES = {
    "CASH", "BANK", "DEBIT_CARD", "CREDIT_CARD", "SAVINGS",
    "ASSET", "LIABILITY", "ELECTRONIC", "OTHER", "PAYPAL",
}


class AccountBase(BaseModel):
    name: str
    type: Optional[str] = None
    currency: str = "GBP"
    initial_balance: float = 0.0

    @field_validator('initial_balance')
    @classmethod
    def round_initial_balance(cls, v):
        return round(v, 2)


class AccountCreate(AccountBase):
    # Deliberately not on AccountBase: AccountResponse inherits from it, and a
    # database written before this vocabulary existed would then fail to *read*.
    # Only what comes in over the API is held to the list.
    @field_validator('type')
    @classmethod
    def known_account_type(cls, v):
        if v is None or v == "":
            return None
        canonical = v.strip().upper().replace(" ", "_")
        if canonical not in ACCOUNT_TYPES:
            raise ValueError(
                f"Unknown account type '{v}'. One of: {', '.join(sorted(ACCOUNT_TYPES))}"
            )
        return canonical


class AccountResponse(AccountBase):
    id: int
    current_balance: float
    is_active: int
    created_at: datetime
    
    class Config:
        from_attributes = True


# --- Category schemas ---

class CategoryBase(BaseModel):
    name: str
    parent: Optional[str] = None
    type: Optional[str] = None


class CategoryCreate(CategoryBase):
    pass


class CategoryResponse(CategoryBase):
    id: int
    created_at: datetime
    
    class Config:
        from_attributes = True


# --- Payee schemas ---

class PayeeBase(BaseModel):
    name: str


class PayeeCreate(PayeeBase):
    pass


class PayeeResponse(PayeeBase):
    id: int
    most_common_category_id: Optional[int] = None
    most_common_location_id: Optional[int] = None
    most_common_project_id: Optional[int] = None
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True


class PayeeCategoryStat(BaseModel):
    """One of a payee's most-used categories, with occurrence count."""
    category_id: Optional[int] = None
    name: Optional[str] = None
    parent: Optional[str] = None
    count: int = 0


class PayeeWithDetails(PayeeResponse):
    """Payee response with related entity names and usage statistics."""
    most_common_category_name: Optional[str] = None
    most_common_location_name: Optional[str] = None
    most_common_project_name: Optional[str] = None
    transaction_count: int = 0
    top_categories: List[PayeeCategoryStat] = []


# --- Location schemas ---

class LocationBase(BaseModel):
    name: str


class LocationCreate(LocationBase):
    pass


class LocationResponse(LocationBase):
    id: int
    created_at: datetime
    transaction_count: int = 0

    class Config:
        from_attributes = True


# --- Project schemas ---

class ProjectBase(BaseModel):
    name: str


class ProjectCreate(ProjectBase):
    pass


class ProjectResponse(ProjectBase):
    id: int
    created_at: datetime
    transaction_count: int = 0

    class Config:
        from_attributes = True


# --- Transaction schemas ---

class TransactionBase(BaseModel):
    date: datetime
    amount: float
    currency: str = "GBP"
    note: Optional[str] = None
    account_id: int
    category_id: Optional[int] = None
    payee_id: Optional[int] = None
    location_id: Optional[int] = None
    project_id: Optional[int] = None

    @field_validator('amount')
    @classmethod
    def round_amount(cls, v):
        return round(v, 2)


class TransactionCreate(TransactionBase):
    pass


class TransactionResponse(TransactionBase):
    id: int
    created_at: datetime
    updated_at: datetime
    account_balance_after: Optional[float] = None
    total_balance_after: Optional[float] = None
    # Set when this row is one line of a split; shared by all its sibling lines.
    # Deliberately absent from TransactionBase so an ordinary update of a single
    # line cannot detach it from its split by simply not mentioning it.
    split_group_id: Optional[int] = None

    class Config:
        from_attributes = True


class TransactionWithDetails(TransactionResponse):
    """Transaction response with related entity names."""
    account_name: Optional[str] = None
    category_name: Optional[str] = None
    payee_name: Optional[str] = None
    location_name: Optional[str] = None
    project_name: Optional[str] = None


# --- Split transaction schemas ---

class SplitLineBase(BaseModel):
    """
    One line of a split transaction: its own slice of the amount, with its own
    category, project and note.
    """
    amount: float
    category_id: Optional[int] = None
    project_id: Optional[int] = None
    note: Optional[str] = None

    @field_validator('amount')
    @classmethod
    def round_amount(cls, v):
        return round(v, 2)


class SplitLineCreate(SplitLineBase):
    # The existing line this one replaces, when editing a split. Omitted for a
    # line being added; lines left out of the payload are deleted.
    id: Optional[int] = None


class SplitTransactionCreate(BaseModel):
    """
    A single purchase spread over several lines. Date, account, currency, payee
    and location belong to the purchase as a whole; amount, category, project
    and note belong to each line.
    """
    date: datetime
    account_id: int
    currency: str = "GBP"
    payee_id: Optional[int] = None
    location_id: Optional[int] = None
    lines: List[SplitLineCreate]

    @field_validator('lines')
    @classmethod
    def check_lines(cls, v):
        # Creating a split needs two lines (enforced by the endpoint); updating
        # one down to a single line is how a split is dissolved again.
        if not v:
            raise ValueError("A split transaction needs at least one line")
        return v


class SplitLineResponse(SplitLineBase):
    id: int
    category_name: Optional[str] = None
    category_parent: Optional[str] = None
    project_name: Optional[str] = None


class SplitTransactionResponse(BaseModel):
    """A split as one thing: the shared header, the total, and the lines."""
    split_group_id: int
    date: datetime
    account_id: Optional[int] = None
    account_name: Optional[str] = None
    currency: str = "GBP"
    payee_id: Optional[int] = None
    payee_name: Optional[str] = None
    location_id: Optional[int] = None
    location_name: Optional[str] = None
    amount: float                                    # the total of every line
    account_balance_after: Optional[float] = None    # after the last line
    total_balance_after: Optional[float] = None
    lines: List[SplitLineResponse] = []


# --- Transfer schema ---

class TransferCreate(BaseModel):
    """Schema for creating a transfer between two accounts."""
    date: datetime
    from_account_id: int
    to_account_id: int
    from_amount: float
    to_amount: Optional[float] = None
    note: Optional[str] = None

    @field_validator('from_amount', 'to_amount')
    @classmethod
    def round_amounts(cls, v):
        return round(v, 2) if v is not None else None


# --- Exchange rate schemas ---

class ExchangeRateBase(BaseModel):
    currency: str
    rate: float
    date: datetime

    @field_validator('rate')
    @classmethod
    def round_rate(cls, v):
        return round(v, 6)


class ExchangeRateCreate(ExchangeRateBase):
    pass


class ExchangeRateResponse(ExchangeRateBase):
    id: int
    created_at: datetime
    
    class Config:
        from_attributes = True


# --- Budget schemas ---

class BudgetBase(BaseModel):
    year_month: str  # Format: "2025-01"
    amount: float
    currency: str = "GBP"

    @field_validator('amount')
    @classmethod
    def round_amount(cls, v):
        return round(v, 2)


class BudgetCreate(BudgetBase):
    pass


class BudgetResponse(BudgetBase):
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# --- Recurring expense schemas ---

class RecurringExpenseBase(BaseModel):
    name: str
    payee_id: Optional[int] = None
    category_id: Optional[int] = None
    amount: float
    currency: str = "GBP"
    day_of_month: Optional[int] = None
    frequency: str = "monthly"  # monthly, quarterly, biannual, annual
    start_month: Optional[int] = None  # Month when charged (1-12), for non-monthly

    @field_validator('amount')
    @classmethod
    def round_amount(cls, v):
        return round(v, 2)


class RecurringExpenseCreate(RecurringExpenseBase):
    pass


class RecurringExpenseResponse(RecurringExpenseBase):
    id: int
    is_active: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class RecurringExpenseWithDetails(RecurringExpenseResponse):
    """Recurring expense with related entity names and payment status."""
    payee_name: Optional[str] = None
    category_name: Optional[str] = None
    paid_this_month: bool = False
    applies_this_month: bool = True  # Whether this expense applies to current month based on frequency


# --- Planned expense schemas ---

class PlannedExpenseBase(BaseModel):
    year_month: str  # Format: "2025-01"
    name: str
    amount: float
    currency: str = "GBP"
    category_id: Optional[int] = None

    @field_validator('amount')
    @classmethod
    def round_amount(cls, v):
        return round(v, 2)


class PlannedExpenseCreate(PlannedExpenseBase):
    pass


class PlannedExpenseResponse(PlannedExpenseBase):
    id: int
    is_paid: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class PlannedExpenseWithDetails(PlannedExpenseResponse):
    """Planned expense with category name."""
    category_name: Optional[str] = None


# --- Budget item schemas (the budgeting/auditing model) ---

class BudgetItemBase(BaseModel):
    """A budget definition: a fixed expense, an expected income or a planned expense."""
    kind: str                       # fixed | income | planned
    name: str
    amount: float = 0.0
    currency: Optional[str] = None  # defaults to the app's display currency
    is_estimated: bool = False

    first_date: Optional[datetime] = None
    interval_count: int = 1
    interval_unit: str = "month"    # once | day | week | month | year
    # Which day of the month it lands on (monthly and yearly rhythms only).
    day_rule: str = "exact"         # exact | working_from_start | working_from_end
    day_ordinal: Optional[int] = None

    payee_id: Optional[int] = None
    set_aside_account_id: Optional[int] = None
    account_ids: List[int] = []
    category_ids: List[int] = []
    starts_ym: Optional[str] = None  # defaults to the current month

    @field_validator('amount')
    @classmethod
    def round_amount(cls, v):
        return round(v or 0, 2)

    @field_validator('kind')
    @classmethod
    def check_kind(cls, v):
        if v not in ("fixed", "income", "planned"):
            raise ValueError("kind must be fixed, income or planned")
        return v

    @field_validator('interval_unit')
    @classmethod
    def check_unit(cls, v):
        if v not in ("once", "day", "week", "month", "year"):
            raise ValueError("interval_unit must be once, day, week, month or year")
        return v

    @field_validator('interval_count')
    @classmethod
    def check_count(cls, v):
        return max(1, int(v or 1))

    @field_validator('day_rule')
    @classmethod
    def check_day_rule(cls, v):
        if v not in ("exact", "working_from_start", "working_from_end"):
            raise ValueError("day_rule must be exact, working_from_start or working_from_end")
        return v


class BudgetItemCreate(BudgetItemBase):
    pass


class BudgetLineUpdate(BaseModel):
    """
    Correct a single month's line. `paid` set to None returns the line to
    automatic detection.
    """
    name: Optional[str] = None
    amount: Optional[float] = None
    paid: Optional[bool] = None
    clear_paid_override: bool = False

    @field_validator('amount')
    @classmethod
    def round_amount(cls, v):
        return round(v, 2) if v is not None else None


# --- Loan schemas ---

class LoanTerms(BaseModel):
    """
    What was agreed: the figures an amortisation schedule follows from. Shared by
    creating a loan and editing one, so a term can never be valid on the way in
    and invalid on the way back.
    """
    name: str
    principal: float
    annual_rate: float = 0.0
    open_date: datetime
    # When the first instalment is due. None derives it from the rhythm and the
    # day rule; set it when the loan's first payment does not fall a whole
    # period after the drawdown.
    first_payment_date: Optional[datetime] = None
    opening_fee: float = 0.0
    fee_treatment: str = "upfront"   # upfront | capitalised
    recurring_fee: float = 0.0
    recurring_fee_months: int = 1
    early_repayment_fee_pct: float = 0.0
    term_count: int = 1
    term_unit: str = "year"          # month | year
    repayment_type: str = "french"   # french | interest_only | constant_principal
    interest_months: int = 1
    interest_unit: str = "month"     # month | day — "day" ignores interest_months
    payment_months: int = 1
    day_rule: str = "exact"          # exact | working_from_start | working_from_end
    day_ordinal: Optional[int] = None
    day_of_month: Optional[int] = None

    lender_payee_id: Optional[int] = None
    lender_name: Optional[str] = None       # creates the payee when it is new

    @field_validator('principal', 'opening_fee', 'recurring_fee')
    @classmethod
    def round_principal(cls, v):
        return round(v or 0, 2)

    @field_validator('recurring_fee_months')
    @classmethod
    def check_fee_frequency(cls, v):
        return max(1, int(v or 1))

    @field_validator('fee_treatment')
    @classmethod
    def check_fee_treatment(cls, v):
        if v not in ("upfront", "capitalised"):
            raise ValueError("fee_treatment must be upfront or capitalised")
        return v

    @field_validator('term_unit')
    @classmethod
    def check_term_unit(cls, v):
        if v not in ("month", "year"):
            raise ValueError("term_unit must be month or year")
        return v

    @field_validator('repayment_type')
    @classmethod
    def check_repayment_type(cls, v):
        if v not in ("french", "interest_only", "constant_principal"):
            raise ValueError("repayment_type must be french, interest_only or constant_principal")
        return v

    @field_validator('day_rule')
    @classmethod
    def check_day_rule(cls, v):
        if v not in ("exact", "working_from_start", "working_from_end"):
            raise ValueError("day_rule must be exact, working_from_start or working_from_end")
        return v

    @field_validator('interest_months', 'payment_months')
    @classmethod
    def check_frequency(cls, v):
        return max(1, int(v or 1))

    @field_validator('interest_unit')
    @classmethod
    def check_interest_unit(cls, v):
        if v not in ("month", "day"):
            raise ValueError("interest_unit must be month or day")
        return v

    @model_validator(mode='after')
    def check_first_payment(self):
        # Interest cannot be charged for a period that ends before it began.
        if self.first_payment_date and self.first_payment_date <= self.open_date:
            raise ValueError("first_payment_date must be after the opening date")
        return self

    @field_validator('term_count')
    @classmethod
    def check_term_count(cls, v):
        return max(1, int(v or 1))


class LoanCreate(LoanTerms):
    """
    Opening a loan. Either it gets an account of its own, or the terms are put on
    a debt account that already exists (``account_id``) — one of the loans that
    have been estimated from their movements up to now.
    """
    disbursement_account_id: Optional[int] = None
    # Existing debt account to attach these terms to. When absent a new account
    # is created, named after the loan.
    account_id: Optional[int] = None
    currency: Optional[str] = None  # defaults to the disbursement account's
    # Book the drawdown as a transfer from the loan account into the account the
    # money landed in. Off when attaching terms to an account that already has it.
    create_disbursement: bool = True


class LoanUpdate(LoanTerms):
    """
    Correcting the terms of a loan already recorded.

    Terms only: the account and the movements booked when the loan was opened are
    left alone. Rewriting a drawdown that has since been reconciled — or that the
    user has edited themselves — would destroy work to fix a typo, so a changed
    principal shows up as a gap between the real balance and the schedule instead,
    which is exactly the signal that page exists to give.
    """
    pass


class CategoryBucketEntry(BaseModel):
    category_id: int
    bucket: Optional[str] = None  # essentials | indulgences | culture | unexpected


class CategoryBucketUpdate(BaseModel):
    mappings: List[CategoryBucketEntry] = []


# --- Utility schemas ---

class DuplicateCheck(BaseModel):
    date: str
    amount: float
    account_id: int


# --- Maintenance settings ---

class MaintenanceSettingsUpdate(BaseModel):
    """Partial update for the nightly maintenance settings."""
    maintenance_time: Optional[str] = None   # HH:MM, 24h
    backup_retention: Optional[str] = None   # 1m | 3m | 6m | 1y | 2y | never
    display_currency: Optional[str] = None   # "auto" or a supported currency code


# --- Authentication ---

class SetupIn(BaseModel):
    password: str

class LoginIn(BaseModel):
    password: str

class ChangePasswordIn(BaseModel):
    old_password: str
    new_password: str

class RecoverIn(BaseModel):
    recovery_code: str
    new_password: str