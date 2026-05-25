from typing import Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from pydantic_dyn.obj_manager import DynObjManager


default_prefix_generator: Callable[DynObjManager] = None
