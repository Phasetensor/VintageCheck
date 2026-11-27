"""Types shared by the published row-oriented APIs."""
from datetime import datetime
from decimal import Decimal
from typing import Any, Union

Clock = Union[str, datetime]
Numeric = Union[str, int, float, Decimal]
Row = dict[str, Any]
