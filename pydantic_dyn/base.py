from annotationlib import get_annotations, Format
from typing import Self, Type, ClassVar, get_args

from pydantic import BaseModel
from pydantic.fields import FieldInfo
from xsentinels.default import DefaultType, Default

from . import _internal
from .client import DynClient
from .types import DynField, DynFieldInfo, Query

print(f"Mod Name: {__name__}")


class DynamoModel(BaseModel):
    dyn_client: ClassVar[DynClient[Self]]

    def dy_save(self, *, condition: Query | None = None):
        self.dyn_client.put(self, condition=condition)

    def dy_delete(self, *, condition: Query | None = None):
        self.dyn_client.delete(self, condition=condition)

    @property
    def dy_id(self):
        return self.dyn_client.id_for(self)

    @classmethod
    def __init_subclass__(
            cls, *,
            name: str | DefaultType = Default,
            table_name: str | DefaultType = Default,
            table_prefix: str | None | DefaultType = Default,
            consistent_reads: bool | DefaultType = Default,
            **kwargs
    ):
        super().__init_subclass__(**kwargs)

    @classmethod
    def __pydantic_init_subclass__(
            cls, *,
            name: str | DefaultType = Default,
            table_name: str | DefaultType = Default,
            table_prefix: str | None | DefaultType = Default,
            consistent_reads: bool | DefaultType = Default,
            **kwargs
    ):
        # TODO: may want an option to prevent inheriting the keys (so one can more easily redefine them).
        super().__pydantic_init_subclass__()

        my_annotations = get_annotations(cls, format=Format.FORWARDREF)

        # If user provided a client-type annotation, use that for our 'client' class;
        # We still make a subclass out of it because they may use this 'client' in a number of
        # different models (and so each model should have their own subclass).
        if client_override := my_annotations.get('dyn_client'):
            client_override = get_args(client_override)[0]
            client = _internal.get_or_create_client_for_model_type(client_override, cls, is_directly_used=True)
        else:
            client = DynClient[cls]

        cls.dyn_client = client.proxy()

        client.obj_type = cls
        client.table_prefix = table_prefix
        if consistent_reads is not Default:
            client._cls_consistent_reads = consistent_reads

        if not table_name:
            table_name = name

        if not table_name:
            model_name = cls.__name__
            client.name = f'{model_name[:1].lower()}{model_name[1:]}' if model_name else ''
        else:
            client.name = table_name

        # Collect all the dyn-fields from the immediate parent classes;
        # They should already be fully created from their own base classes, and so no need to dive/look further.
        dyn_infos: dict[str, DynFieldInfo] = {}
        for base in cls.__bases__:
            if base is DynamoModel:
                continue

            if not issubclass(base, DynamoModel):
                continue
            for field_name, v in base.dyn_client.dyn_fields.items():
                if field_name not in dyn_infos:
                    dyn_infos[field_name] = v.copy()

        dyn_fields: dict[str, DynField] = {}
        for field_name, field_value in cls.model_fields.items():
            field_value: FieldInfo

            others = _internal.find_annotated_metadata_for_iterative(field_value.annotation)
            other_annotated = [v for obj in others for v in obj['metadata']]

            for v in [*field_value.metadata, *other_annotated]:
                if isinstance(v, DynField):
                    # Merge from pre-existing DynField, if needed.
                    if current := dyn_fields.get(field_name):
                        current.merge(v)
                    else:
                        v = v.copy()
                        dyn_fields[field_name] = v
                        # Copy our type into field if it has nothing defined for it yet.
                        if v.py_type is Default:
                            v.py_type = field_value.annotation
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



