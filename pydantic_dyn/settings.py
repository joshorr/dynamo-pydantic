from typing import Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from pydantic_dyn.client import DyObjManager


default_prefix_generator: Callable[DyObjManager] = None
