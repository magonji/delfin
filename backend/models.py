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