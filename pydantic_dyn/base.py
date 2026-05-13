from annotationlib import get_annotations, Format
from typing import Self, Type

from pydantic import BaseModel
from pydantic.fields import FieldInfo
from xsentinels.default import DefaultType, Default

from . import _internal
from .client import DynClient
from .types import DynField, DynFieldInfo


class DynamoModel(BaseModel):
    dyn_client: DynClient[Self]

    def __pydantic_init_subclass__(
            cls, *, name: str | None = None, table_prefix: str | None | DefaultType = Default, **kwargs
    ):
        # TODO: may want an option to prevent inheriting the keys (so one can more easily redefine them).
        super().__pydantic_init_subclass__()
        client = DynClient[cls].grab()
        cls.dyn_client = client

        client.obj_type = cls
        client.table_prefix = table_prefix

        if not name:
            model_name = cls.__name__
            client.name = f'{model_name[:1].lower()}{model_name[1:]}' if model_name else ''
        else:
            client.name = name

        my_annotations = get_annotations(cls, format=Format.FORWARDREF)

        # Collect all the dyn-fields from the immediate parent classes;
        # They should already be fully created from their own base classes, and so no need to dive/look further.
        dyn_infos: dict[str, DynFieldInfo] = {}
        for base in cls.__bases__:
            if not issubclass(base, DynamoModel):
                continue
            for field_name, v in base.dyn_client.dyn_fields.items():
                if field_name not in dyn_infos:
                    dyn_infos[field_name] = v.copy()

        dyn_fields: dict[str, DynField] = {}
        for field_name, field_value in cls.model_fields.items():
            field_value: FieldInfo
            for v in field_value.metadata:
                if isinstance(v, DynField):
                    # Merge from pre-existing DynField, if needed.
                    if current := dyn_fields.get(field_name):
                        current.merge(v)
                    else:
                        dyn_fields[field_name] = v.copy()
                    dyn_fields[field_name] = v
            if field_name not in dyn_fields:
                # We create it, since we have nothing to start with.
                dyn_fields[field_name] = DynField(py_type=field_value.annotation)

        for k, v in dyn_fields.items():
            dyn_info = dyn_infos.get(k)
            if dyn_info is None:
                dyn_infos[k] = DynFieldInfo.from_field(v, name=k)
                continue

            if k not in my_annotations:
                # We are inheriting the DynFieldInfo, and user did not override it on current subclass;
                # So just use the existing DynFieldInfo.
                continue

            # We have a DynField/Annotation override for current class, merge it with the inherited DynFieldInfo.
            dyn_info.merge_with_field(v)

        client.dyn_fields = dyn_infos


class ExampleModel(DynamoModel):
    doc_id: str
