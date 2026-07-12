import sys
from types import prepare_class
from typing import Any, Type


def get_caller_frame_info(depth: int = 2) -> tuple[str | None, bool]:
    """Used inside a function to check whether it was called globally.

    Args:
        depth: The depth to get the frame.

    Returns:
        A tuple contains `module_name` and `called_globally`.

    Raises:
        RuntimeError: If the function is not called inside a function.
    """
    try:
        previous_caller_frame = sys._getframe(depth)
    except ValueError as e:
        raise RuntimeError('This function must be used inside another function') from e
    except AttributeError:  # sys module does not have _getframe function, so there's nothing we can do about it
        return None, False
    frame_globals = previous_caller_frame.f_globals
    return frame_globals.get('__name__'), previous_caller_frame.f_locals is frame_globals


def create_generic_submodel(
        name: str, origin: Type[Any], generic_typevar: Type[Any], super_classes: list[Type[Any]] | None = None
) -> Type[Any]:
    """ Dynamically create a subclass of/for a Generic class.
        If `super_classes` is `None`, then we use `origin` as the single super classes;
        otherwise we use `super_classes`.  If `super_classes` is empty, we inherit from `origin`.
    """
    namespace: dict[str, Any] = {'__module__': origin.__module__}
    if super_classes is not None:
        bases = tuple(super_classes)
    else:
        bases = (origin,)

    if not bases:
        bases = (origin,)

    meta, ns, kwargs = prepare_class(name, bases)
    namespace.update(ns)
    created_model = meta(name, bases, namespace, _generic_typevar=generic_typevar, **kwargs)

    model_module, called_globally = get_caller_frame_info(depth=3)
    if called_globally:
        object_by_reference = None
        reference_name = name
        reference_module_globals = sys.modules[created_model.__module__].__dict__
        while object_by_reference is not created_model:
            object_by_reference = reference_module_globals.setdefault(reference_name, created_model)
            reference_name += '_'

    return created_model
