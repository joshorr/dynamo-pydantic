import dataclasses
from dataclasses import dataclass
from enum import StrEnum, auto
from functools import cached_property
from typing import Union, Iterable, Annotated, TypeVar, Self, Type, Any
import datetime as dt
from uuid import UUID

from pydantic import BaseModel
from xsentinels import Default
from xsentinels.default import DefaultType

QueryValue = Union[
    str,
    int,
    dt.date,
    None,
    UUID,
    Iterable[str | int | UUID | dt.date],
]

DynParams = dict[str, Any]

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

Item = dict[str, QueryValue] | BaseModel


_T = TypeVar('_T')


class KeyType(StrEnum):
    hash = auto()
    sort = auto()


class DynField:
    """ Way to customize with dynamo-specific field options.
        Any found for the same field will be merged together.
        The final result will convert into a `DynFieldInfo` object.
        If the parent class has any DynFieldInfo's, they will also be merged into the final result.
    """
    def __init__(
            self, *,
            py_type: Type | DefaultType = Default,
            dy_type: str | None | DefaultType = Default,
            key_type: KeyType | None | DefaultType = Default
    ):
        self.key_type = key_type
        self.py_type = py_type
        if dy_type is not None:
            self.dy_type = dy_type
        elif py_type is not Default:
            from pydantic_dyn import _internal
            is_key = self.key_type is not None
            self.dy_type = _internal.get_dynamo_type_from_python_type(py_type, is_key_type=is_key)

    def copy(self) -> DynField:
        return DynField(py_type=self.py_type, dy_type=self.dy_type, key_type=self.key_type)

    def merge(self, other: DynField):
        # We go though all of are attrs, and set the ones that have a value.
        # Consider anything with the `Default` sentinal-value as not having any value.
        for k in ['key_type', 'py_type', 'dy_type']:
            v = getattr(other, k)
            if v is not Default:
                setattr(self, k, v)

        if other.py_type is not Default and self.py_type is not Default:
            from pydantic_dyn import _internal
            is_key = self.key_type is not None
            self.dy_type = _internal.get_dynamo_type_from_python_type(self.py_type, is_key_type=is_key)

    key_type: KeyType | None | DefaultType = Default
    py_type: Type | DefaultType = Default
    dy_type: str | DefaultType = Default

    def __str__(self):
        return f'<DynField key_type={self.key_type} py_type={self.py_type} dy_type={self.dy_type}>'

    def __repr__(self):
        return str(self)


@dataclasses.dataclass
class DynFieldInfo:
    """ Final dynamo-specific field info, after merging any found `DynField` and pydantic field info.
    """
    @classmethod
    def from_field(cls, dyn_field: DynField, name: str) -> DynFieldInfo:
        return DynFieldInfo(
            key_type=dyn_field.key_type or None,
            py_type=dyn_field.py_type or None,
            name=name,
            dy_name=name,
        )

    def __post_init__(self):
        if (v := self.dy_name) and not self.names and not self.name:
            self.name = v
            self.names = [v]

        if not self.names:
            if v := self.name:
                self.names = [v]
            elif v := self.dy_name:
                self.names = [v]
                self.name = v

        if not self.dy_name:
            if v := self.name:
                self.dy_name = v
            if (v := self.names) and len(v) == 1:
                self.dy_name = v[0]

        assert self.dy_name
        assert self.names

        if len(self.names) > 1:
            self.name = None

    def copy(self) -> DynFieldInfo:
        return dataclasses.replace(self)

    def merge_with_field(self, dyn_field: DynField):
        # We go though all of are attrs, and set the ones that have a value.
        # Consider anything with the `Default` sentinal-value as not having any value.
        for k in ['key_type', 'py_type', 'dy_type']:
            v = getattr(dyn_field, k)
            if v is not Default:
                setattr(self, k, v)

    dy_name: str = Default
    """ Dynamo-attribute name.
        Defaults to `self.name`, but could be different based on alias/more-than-one-field for a key; etc.
    """

    @cached_property
    def dy_type(self) -> str:
        from pydantic_dyn import _internal
        return _internal.get_dynamo_type_from_python_type(self.py_type)

    @property
    def from_multiple_fields(self) -> bool:
        return bool(len(self.names) > 1)

    name: str | None = None
    """ If there is only one field, we set that here. If there is more that one this is `None`, see `self.names`.
    """

    names: list[str] = None
    """ Regardless if this came from one or more fields, we list them here,
        So there will always be a list with at least one value in it.
    """

    py_type: Type = str
    key_type: KeyType | None = None


HashKey = Annotated[_T, DynField(key_type=KeyType.hash)]
SortKey = Annotated[_T, DynField(key_type=KeyType.sort)]
