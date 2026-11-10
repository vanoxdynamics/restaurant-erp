from decimal import Decimal
from typing import Annotated, Generic, List, Optional, TypeVar, Union

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, StringConstraints

Money = Annotated[Decimal, Field(ge=0, max_digits=10, decimal_places=2)]
PositiveMoney = Annotated[Decimal, Field(gt=0, max_digits=10, decimal_places=2)]
TaxRate = Annotated[Decimal, Field(ge=0, le=28, max_digits=5, decimal_places=2)]
# Quantities have up to 3 decimals (0.750 kg). Whole numbers are still sent and returned as plain JSON numbers (2, not "2.000"),
# so apps that only sell pieces see no change.
Quantity = Annotated[Decimal, Field(gt=0, le=9999, max_digits=7, decimal_places=3)]
QuantityOut = Annotated[Decimal, PlainSerializer(lambda v: int(v) if v == v.to_integral_value() else float(v),
                                                 return_type=Union[int, float], when_used="json")]
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
