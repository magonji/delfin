from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Text, Index
from sqlalchemy.orm import relationship
from datetime import datetime
from backend.database import Base


class Account(Base):
    """
    Represents a financial account (e.g., Monzo, Bank of Scotland).
    """
    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False, index=True)
    type = Column(String)  # e.g., "Bank", "Cash", "Credit Card"
    currency = Column(String, default="GBP", index=True)  # NEW INDEX: filtrado por moneda
    initial_balance = Column(Float, default=0.0)
    current_balance = Column(Float, default=0.0)
    is_active = Column(Integer, default=1, index=True)  # NEW INDEX: filtrado activo/inactivo
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    transactions = relationship("Transaction", back_populates="account")


class Category(Base):
    """
    Represents expense/income categories with hierarchical structure.
    """
    __tablename__ = "categories"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, index=True)
    parent = Column(String, index=True)  # NEW INDEX: consultas jerárquicas
    type = Column(String, index=True)  # NEW INDEX: filtrado por tipo Income/Expense
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    transactions = relationship("Transaction", back_populates="category")


class Payee(Base):
    """
    Represents payees/merchants (e.g., ASDA, Lidl, Amazon).
    """
    __tablename__ = "payees"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False, index=True)
    
    # Most common associations (pre-calculated for performance)
    most_common_category_id = Column(Integer, ForeignKey("categories.id"), nullable=True, index=True)
    most_common_location_id = Column(Integer, ForeignKey("locations.id"), nullable=True, index=True)
    most_common_project_id = Column(Integer, ForeignKey("projects.id"), nullable=True, index=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    transactions = relationship("Transaction", back_populates="payee", foreign_keys="Transaction.payee_id")
    most_common_category = relationship("Category", foreign_keys=[most_common_category_id])
    most_common_location = relationship("Location", foreign_keys=[most_common_location_id])
    most_common_project = relationship("Project", foreign_keys=[most_common_project_id])


class Location(Base):
    """
    Represents geographical locations (e.g., Glasgow, Madrid).
    """
    __tablename__ = "locations"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    transactions = relationship("Transaction", back_populates="location")


class Project(Base):
    """
    Represents projects for grouping transactions (e.g., "Movimientos comunes").
    """
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, nullable=False, index=True)
    description = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    transactions = relationship("Transaction", back_populates="project")


class Transaction(Base):
    """
    Represents individual financial transactions.
    """
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True, index=True)
    date = Column(DateTime, nullable=False, index=True)
    amount = Column(Float, nullable=False)
    currency = Column(String, default="GBP", index=True)
    note = Column(Text)
    
    # Foreign keys
    account_id = Column(Integer, ForeignKey("accounts.id"), index=True)
    category_id = Column(Integer, ForeignKey("categories.id"), index=True)
    payee_id = Column(Integer, ForeignKey("payees.id"), index=True)
    location_id = Column(Integer, ForeignKey("locations.id"), index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), index=True)
    
    # Cached balance columns
    account_balance_after = Column(Float, nullable=True, index=True)  # Balance of specific account after transaction
    total_balance_after = Column(Float, nullable=True, index=True)    # Total balance (all accounts) after transaction
    
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    account = relationship("Account", back_populates="transactions")
    category = relationship("Category", back_populates="transactions")
    payee = relationship("Payee", back_populates="transactions")
    location = relationship("Location", back_populates="transactions")
    project = relationship("Project", back_populates="transactions")
    
    # Composite indices
    __table_args__ = (
        Index('idx_transaction_account_date', 'account_id', 'date'),
        Index('idx_transaction_currency_date', 'currency', 'date'),
        Index('idx_transaction_date_amount', 'date', 'amount'),
        Index('idx_transaction_category_date', 'category_id', 'date'),
        Index('idx_transaction_payee_date', 'payee_id', 'date'),
    )


class ExchangeRate(Base):
    """
    Stores historical exchange rates for currency conversion.
    Rates are stored with GBP as the base currency.
    """
    __tablename__ = "exchange_rates"

    id = Column(Integer, primary_key=True, index=True)
    currency = Column(String, nullable=False, index=True)
    rate = Column(Float, nullable=False)
    date = Column(DateTime, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # COMPOSITE INDEX - Crítico para get_latest_exchange_rates()
    __table_args__ = (
        # Índice compuesto para obtener la tasa más reciente por moneda
        Index('idx_exchange_rate_currency_date', 'currency', 'date'),
    )

# Event listeners para redondear cantidades monetarias antes de guardar
from sqlalchemy import event

@event.listens_for(Transaction, 'before_insert')
@event.listens_for(Transaction, 'before_update')
def round_transaction_money_amounts(mapper, connection, target):
    """
    Redondea cantidades monetarias a 2 decimales antes de guardar en la base de datos.
    Esto previene la acumulación de errores de punto flotante.
    """
    if target.amount is not None:
        target.amount = round(target.amount, 2)
    if target.account_balance_after is not None:
        target.account_balance_after = round(target.account_balance_after, 2)
    if target.total_balance_after is not None:
        target.total_balance_after = round(target.total_balance_after, 2)


@event.listens_for(Account, 'before_insert')
@event.listens_for(Account, 'before_update')
def round_account_money_amounts(mapper, connection, target):
    """
    Redondea balances de cuentas a 2 decimales antes de guardar.
    """
    if target.initial_balance is not None:
        target.initial_balance = round(target.initial_balance, 2)
    if target.current_balance is not None:
        target.current_balance = round(target.current_balance, 2)


@event.listens_for(ExchangeRate, 'before_insert')
@event.listens_for(ExchangeRate, 'before_update')
def round_exchange_rate(mapper, connection, target):
    """
    Redondea tasas de cambio a 6 decimales (más precisión para divisas).
    """
    if target.rate is not None:
        target.rate = round(target.rate, 6)