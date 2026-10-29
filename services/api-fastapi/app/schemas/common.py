from decimal import Decimal
from typing import Annotated, Generic, List, Optional, TypeVar

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Money = Annotated[Decimal, Field(ge=0, max_digits=10, decimal_places=2)]
PositiveMoney = Annotated[Decimal, Field(gt=0, max_digits=10, decimal_places=2)]
TaxRate = Annotated[Decimal, Field(ge=0, le=28, max_digits=5, decimal_places=2)]
Quantity = Annotated[int, Field(ge=1, le=999)]
Pin = Annotated[str, Field(pattern=r"^\d{4,6}$")]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=500)]
Notes = Annotated[Optional[str], Field(default=None, max_length=500)]

T = TypeVar("T")


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel, Generic[T]):
    items: List[T]
    total: int
    limit: int
    offset: int
