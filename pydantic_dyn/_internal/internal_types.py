from typing import Type

from pydantic import BaseModel

_type_to_aws_type_map = {
    int: "N",
    float: "N",
    complex: "N",
    bool: "BOOL",
    str: "S",
    dict: "M",
    list: "L"
}

operator_alias_map = {
    "in": 'is_in',
    "exact": 'eq',
    "": 'eq',
    None: 'eq'
}
"""
Used to normalize some common operators (that we use in other systems) to the one used in Dynamo.
"""


def get_dynamo_type_from_python_type(some_type: Type) -> str:
    # If it's another pydantic model, then it's a dict-type.
    if issubclass(some_type, BaseModel):
        return _type_to_aws_type_map[dict]

    dyn_type = _type_to_aws_type_map.get(some_type)
    if dyn_type is not None:
        return dyn_type

    # todo: consider making the type we send to dynamo overridable
    #   default map is `get_dynamo_type_from_python_type`.
    #   generally, unless it's a basic type we default to `str`
    #   (example: datetime types use str).
    return _type_to_aws_type_map[str]
