from pydantic import BaseModel
from datetime import datetime
from typing import Optional


class AccountBase(BaseModel):
    name: str
    type: Optional[str] = None
    currency: str = "GBP"
    initial_balance: float = 0.0


class AccountCreate(AccountBase):
    pass


class AccountResponse(AccountBase):
    id: int
    current_balance: float
    is_active: int
    created_at: datetime
    
    class Config:
        from_attributes = True


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


class PayeeBase(BaseModel):
    name: str


class PayeeCreate(PayeeBase):
    pass


class PayeeResponse(PayeeBase):
    id: int
    created_at: datetime
    
    class Config:
        from_attributes = True


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


class TransactionCreate(TransactionBase):
    pass


class TransactionResponse(TransactionBase):
    id: int
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True

class LocationBase(BaseModel):
    name: str


class LocationCreate(LocationBase):
    pass


class LocationResponse(LocationBase):
    id: int
    created_at: datetime
    
    class Config:
        from_attributes = True


class ProjectBase(BaseModel):
    name: str


class ProjectCreate(ProjectBase):
    pass


class ProjectResponse(ProjectBase):
    id: int
    created_at: datetime
    
    class Config:
        from_attributes = True

        
class TransactionWithDetails(TransactionResponse):
    """
    Transaction response with related entity names.
    """
    account_name: Optional[str] = None
    category_name: Optional[str] = None
    payee_name: Optional[str] = None
    location_name: Optional[str] = None
    project_name: Optional[str] = None