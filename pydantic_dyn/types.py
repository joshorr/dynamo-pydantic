from typing import Union, Iterable
import datetime as dt
from uuid import UUID

from pydantic import BaseModel

QueryValue = Union[
    str,
    int,
    dt.date,
    None,
    UUID,
    Iterable[str | int | UUID | dt.date],
]

Query = dict[str, QueryValue]
""" A `Query` signifies that any combinations of fields/keys can be included.
"""

Key = Query | BaseModel
""" A `Key` signifies that we are looking for key info, ie: the hash/partition key and/or the range/sort key.
    Keys themselves could potentially be made up of many fields themselves (that are combined together).
    
    It can be a query with the basic key info as dict keys that represent fields:
    
    ```python
    {"hash_field_name": "hash-field-value", "range_field_name": "range-field-value"}
    
    ```
"""




