from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .types import Item, Query, Key


class DynamoError(Exception):
    pass


class DynamoConditionError(DynamoError):
    def __init__(self, item: Item, condition: Query, original: Exception):
        self.condition = condition
        self.item = item
        self.original_boto_error = original
        super().__init__(f'DynamoConditionError; condition failed: {condition}')

    condition: Query
    item: Item | Key | str
    original_boto_error: Exception

