from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Text
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
    currency = Column(String, default="GBP")
    initial_balance = Column(Float, default=0.0)
    current_balance = Column(Float, default=0.0)
    is_active = Column(Integer, default=1)  # 1 = active, 0 = archived
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
    parent = Column(String)  # Parent category for hierarchy
    type = Column(String)  # "Expense" or "Income"
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
    currency = Column(String, default="GBP")
    note = Column(Text)
    
    # Foreign keys
    account_id = Column(Integer, ForeignKey("accounts.id"))
    category_id = Column(Integer, ForeignKey("categories.id"))
    payee_id = Column(Integer, ForeignKey("payees.id"))
    location_id = Column(Integer, ForeignKey("locations.id"))
    project_id = Column(Integer, ForeignKey("projects.id"))
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    account = relationship("Account", back_populates="transactions")
    category = relationship("Category", back_populates="transactions")
    payee = relationship("Payee", back_populates="transactions")
    location = relationship("Location", back_populates="transactions")
    project = relationship("Project", back_populates="transactions")

class ExchangeRate(Base):
    """
    Stores historical exchange rates for currency conversion.
    Rates are stored with GBP as the base currency.
    """
    __tablename__ = "exchange_rates"

    id = Column(Integer, primary_key=True, index=True)
    currency = Column(String, nullable=False, index=True)
    rate = Column(Float, nullable=False)  # Rate relative to base currency (GBP)
    date = Column(DateTime, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)