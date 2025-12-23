"""
Pydantic schemas for request/response validation.
"""
from pydantic import BaseModel, field_validator
from datetime import datetime
from typing import Optional


# --- Account schemas ---

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
    pass


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


class PayeeWithDetails(PayeeResponse):
    """Payee response with related entity names."""
    most_common_category_name: Optional[str] = None
    most_common_location_name: Optional[str] = None
    most_common_project_name: Optional[str] = None


# --- Location schemas ---

class LocationBase(BaseModel):
    name: str


class LocationCreate(LocationBase):
    pass


class LocationResponse(LocationBase):
    id: int
    created_at: datetime
    
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
    
    class Config:
        from_attributes = True


class TransactionWithDetails(TransactionResponse):
    """Transaction response with related entity names."""
    account_name: Optional[str] = None
    category_name: Optional[str] = None
    payee_name: Optional[str] = None
    location_name: Optional[str] = None
    project_name: Optional[str] = None


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


# --- Utility schemas ---

class DuplicateCheck(BaseModel):
    date: str
    amount: float
    account_id: int