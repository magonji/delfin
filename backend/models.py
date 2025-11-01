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
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    transactions = relationship("Transaction", back_populates="payee")


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
    currency = Column(String, default="GBP", index=True)  # NEW INDEX: agrupación por moneda
    note = Column(Text)
    
    # Foreign keys - Todos con índices automáticos por las FK
    account_id = Column(Integer, ForeignKey("accounts.id"), index=True)
    category_id = Column(Integer, ForeignKey("categories.id"), index=True)
    payee_id = Column(Integer, ForeignKey("payees.id"), index=True)
    location_id = Column(Integer, ForeignKey("locations.id"), index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), index=True)
    
    created_at = Column(DateTime, default=datetime.utcnow, index=True)  # NEW INDEX: ordenación
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    account = relationship("Account", back_populates="transactions")
    category = relationship("Category", back_populates="transactions")
    payee = relationship("Payee", back_populates="transactions")
    location = relationship("Location", back_populates="transactions")
    project = relationship("Project", back_populates="transactions")
    
    # COMPOSITE INDICES - Críticos para rendimiento
    __table_args__ = (
        # Índice compuesto para consultas por cuenta ordenadas por fecha
        Index('idx_transaction_account_date', 'account_id', 'date'),
        
        # Índice compuesto para análisis de moneda por fecha
        Index('idx_transaction_currency_date', 'currency', 'date'),
        
        # Índice compuesto para consultas de fecha con suma de amounts
        Index('idx_transaction_date_amount', 'date', 'amount'),
        
        # Índice compuesto para filtros por categoría y fecha
        Index('idx_transaction_category_date', 'category_id', 'date'),
        
        # Índice compuesto para filtros por payee y fecha
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