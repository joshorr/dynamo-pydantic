from annotationlib import get_annotations, Format
from typing import Self, Type, ClassVar, get_args

from pydantic import BaseModel
from pydantic.fields import FieldInfo
from xsentinels.default import DefaultType, Default

from . import _internal
from .obj_manager import DynObjManager
from .types import DynField, DynFieldInfo, Query


class DynamoModel(BaseModel):
    dyn_objs: ClassVar[DynObjManager[Self]]

    def dyn_save(self, *, condition: Query | None = None):
        """ Convenience for `self.dyn_objs.put(self, condition=condition)`;
            Saves/Puts self into dynamodb table.

            See `dynamo_pydantic.obj_manager.DynObjManager.put` for more details.
        """
        self.dyn_objs.put(self, condition=condition)

    def dyn_delete(self, *, condition: Query | None = None):
        """ Convenience for `self.dyn_objs.delete(self, condition=condition)`;
            Deletes self from dynamodb table.

            See `dynamo_pydantic.obj_manager.DynObjManager.delete` for more details.
        """
        self.dyn_objs.delete(self, condition=condition)

    @property
    def dyn_id(self):
        """ Convenience for `self.dyn_objs.id_for(self)`.

            If we only have a hash key, we return that by its self as a string.
            Otherwise, we combine the hash and sort keys together into a single string.
            Uses the `DynamoModel.dyn_objs.full_key_deliminator` for the deliminator to use
            to separate the hash and sort keys (defaults to two-pipes: "||").

            In some cases, this may need to be parsed back into components, so use a delimiter
            that ideally would normally never be in either the hash or sort key.

            See `dynamo_pydantic.obj_manager.DynObjManager.id_for` for more details.
        """
        return self.dyn_objs.id_for(self)

    @classmethod
    def __init_subclass__(
            cls, *,
            dyn_name: str | DefaultType = Default,
            dyn_table_prefix: str | None | DefaultType = Default,
            dyn_consistent_reads: bool | DefaultType = Default,
            **kwargs
    ):
        # For now, we will deal with these extra class arguments/paramters in `__pydantic_init_subclass__`
        # when pydantic has more guarantees around the pydantic fields being read to examine, etc.
        # We are here to 'absorbe' these arguments so we don't get errors from superclass about
        # unused class arguments/paramters.
        super().__init_subclass__(**kwargs)

    @classmethod
    def __pydantic_init_subclass__(
            cls, *,
            dyn_name: str | DefaultType = Default,
            dyn_table_prefix: str | None | DefaultType = Default,
            dyn_consistent_reads: bool | DefaultType = Default,
            **kwargs
    ):
        # For now, we will deal with these extra class arguments/paramters here because
        #  pydantic has more guarantees around the pydantic fields being read to examine, etc;
        #  at this point in time.

        # TODO: may want an option to prevent inheriting the keys (so one can more easily redefine them).
        super().__pydantic_init_subclass__()

        my_annotations = get_annotations(cls, format=Format.FORWARDREF)

        # If user provided a client-type annotation, use that for our 'client' class;
        # We still make a subclass out of it because they may use this 'client' in a number of
        # different models (and so each model should have their own subclass).
        if client_override := my_annotations.get('dyn_objs'):
            client_override = get_args(client_override)[0]
            client = _internal.get_or_create_client_for_model_type(client_override, cls, is_directly_used=True)
        else:
            client = DynObjManager[cls]

        cls.dyn_objs = client.proxy()

        client.obj_type = cls
        client.table_prefix = dyn_table_prefix
        if dyn_consistent_reads is not Default:
            client._cls_consistent_reads = bool(dyn_consistent_reads)

        if not dyn_name:
            model_name = cls.__name__
            client.name = f'{model_name[:1].lower()}{model_name[1:]}' if model_name else ''
        else:
            client.name = str(dyn_name)

        # Collect all the dyn-fields from the immediate parent classes;
        # They should already be fully created from their own base classes, and so no need to dive/look further.
        dyn_infos: dict[str, DynFieldInfo] = {}
        for base in cls.__bases__:
            if base is DynamoModel:
                continue

            if not issubclass(base, DynamoModel):
                continue
            for field_name, v in base.dyn_objs.dyn_fields.items():
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



