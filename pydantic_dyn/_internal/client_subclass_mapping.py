import threading
from typing import Type, Any, TYPE_CHECKING, TypeVar

from .dynamic_subclass import create_generic_submodel

if TYPE_CHECKING:
    from pydantic_dyn.obj_manager import DynObjManager


T = TypeVar('T')
_model_type__to_client = {}
_directly_used__origin_to_use = {}
_lock = threading.Lock()


def get_client_for_model_type(origin: Type[DynObjManager], model_type: Type[T]) -> Type[DynObjManager[T]]:
    # We map the generict 'dict' version of the client to its self,
    # as there is only one subclass per-client/origin for it.
    mapping_key = model_type
    if model_type is dict:
        mapping_key = origin

    return _model_type__to_client.get(mapping_key)


def get_or_create_client_for_model_type(
        origin: Type[DynObjManager], model_type: Type[T], is_directly_used: bool = False
) -> Type[DynObjManager[T]]:
    """
    Args:
        origin:
        model_type:
        is_directly_used: If `True` means user added this as a type-annotation/hint
            directly on the class, and so we should take it at face-value and not search
            superclasses or use any pre-existing mapping.

    Returns: new subclass, from `origin`, and any `model_type` superclasses/parents
        (if `is_directly_used ==`` False`).

    """
    # We map the generict 'dict' version of the client to its self,
    # as there is only one subclass per-client/origin for it.
    mapping_key = model_type
    if model_type is dict:
        mapping_key = origin

    if not is_directly_used:
        if client_type := _model_type__to_client.get(mapping_key, None):
            return client_type

    with _lock:
        if not is_directly_used:
            if client_type := _model_type__to_client.get(mapping_key, None):
                return client_type

        other_parents = []
        if not is_directly_used:
            from pydantic_dyn.base import DynamoModel
            for parent_type in model_type.mro():
                if parent_type is model_type:
                    continue

                if parent_type is not DynamoModel and issubclass(parent_type, DynamoModel):
                    # Should always have a mapping for any previously-constructed/created subclass.
                    client_type = _model_type__to_client[parent_type]
                    if not getattr(client_type, '__dyn_auto_generated_plain_subclass', False):
                        other_parents.append(client_type)

        # Always add `origin` to the end
        if not other_parents:
            other_parents.append(origin)

        for client_type in other_parents:
            if v := _directly_used__origin_to_use.get(client_type):
                origin_to_use = v
                break
        else:
            origin_to_use = other_parents[0]

        name = f'{origin_to_use.__name__}[{model_type.__name__}]'
        client_type = create_generic_submodel(name, origin_to_use, model_type, other_parents)
        if is_directly_used:
            client_type.__dyn_auto_generated_plain_subclass = False
            _directly_used__origin_to_use[client_type] = origin_to_use
        _model_type__to_client[mapping_key] = client_type
        return client_type


def set_client_for_model_type(model_type: Type[T], client_type: Type[DynObjManager[T]]):
    with _lock:
        _model_type__to_client[model_type] = client_type
