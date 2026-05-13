from functools import cache
from typing import Any

from pydantic import BaseModel, TypeAdapter
from pydantic_core import CoreSchema, SchemaValidator, SchemaSerializer

from pydantic_dyn.client import DynClient
from pydantic_dyn.types import DynFieldInfo


def find_field_schema(model: type[BaseModel], field_name: str) -> CoreSchema:
    schema: CoreSchema = model.__pydantic_core_schema__.copy()
    # we shallow copied, be careful not to mutate the original schema!

    assert schema["type"] in ["definitions", "model"]

    # find the field schema
    field_schema = schema["schema"]  # type: ignore
    while "fields" not in field_schema:
        field_schema = field_schema["schema"]  # type: ignore

    field_schema = field_schema["fields"][field_name]["schema"]  # type: ignore

    # if the original schema is a definition schema, replace the model schema with the field schema
    if schema["type"] == "definitions":
        schema["schema"] = field_schema
        return schema
    else:
        return field_schema


@cache
def validator(model: type[BaseModel], field_name: str) -> SchemaValidator:
    return SchemaValidator(find_field_schema(model, field_name))


@cache
def serializer(model: type[BaseModel], field_name: str) -> SchemaSerializer:
    return SchemaSerializer(find_field_schema(model, field_name))


@cache
def type_adapter(py_type: type) -> TypeAdapter:
    return TypeAdapter(py_type)


def serialize_model_field(model: type[BaseModel], field_name: str, value: Any) -> Any:
    return serializer(model, field_name).validate_python(value)


def serialize_dyn_field(client: DynClient, dyn_field: DynFieldInfo, value: Any) -> Any:
    obj_type = client.obj_type
    if dyn_field.name and issubclass(obj_type, BaseModel):
        return serialize_model_field(obj_type, dyn_field.name , value)

    return type_adapter(dyn_field.py_type).validate_python(value)

