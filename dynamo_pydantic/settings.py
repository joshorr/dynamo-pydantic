from typing import Callable, TYPE_CHECKING

from xinject import Dependency
from xsentinels import Default
from xsentinels.default import DefaultType

if TYPE_CHECKING:
    from dynamo_pydantic.obj_manager import DynObjManager


class DynSettings(Dependency):
    def __init__(self, *, consistent_reads: bool | DefaultType = Default):
        self.consistent_reads = consistent_reads

    consistent_reads: bool | DefaultType = Default
    """ Way to change what the default consistent read value should be,
        If set it will be used over the default value for the model
        (but won't override True/False passed directly to client as method paramter to get/scan/etc.
    """

    default_prefix_generator: Callable[[DynObjManager], str] | None = None

    create_tables_if_needed: bool = False
    """ If set to `True`, will create tables lazily if needed on first get/put/delete/save/etc.
    """


dyn_settings = DynSettings.proxy()
""" Proxy to the current `DynSettings` currently used/injected at the current moment.
    Used this object use like you would use normal instance of DynSettings.
"""
