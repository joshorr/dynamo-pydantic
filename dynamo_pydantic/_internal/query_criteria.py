import dataclasses
from functools import cached_property
from linecache import cache
from typing import TYPE_CHECKING, Any, Set, Iterable

from moto import swf
from pydantic import BaseModel, TypeAdapter
# TODO: Remove this `xdynamo` dependency.
from xdynamo.common_types import between_operators
from xloop import xloop
from xsentinels.default import DefaultType, Default

from .. import _internal
from ..errors import DynamoError
from ..types import Key, Query, DynField, DynFieldInfo
from .internal_types import operator_alias_map

if TYPE_CHECKING:
    from pydantic_dyn.obj_manager import DynObjManager


@dataclasses.dataclass(frozen=True, eq=True)
class DynKey:
    client: DynObjManager = dataclasses.field(compare=False)

    # We only compare with `id`, this should represent our identity sufficiently.
    id: str = None
    hash_key: Any = dataclasses.field(default=None, compare=False)
    range_key: Any | None = (
        dataclasses.field(default=None, compare=False)
    )
    range_operator: str = dataclasses.field(default=None, compare=False)
    require_full_key: bool = dataclasses.field(default=True, compare=False)

    def __str__(self):
        return self.id or ''

    # # TODO: Figure out of this class-method is needed anymore.
    # @classmethod
    # def via_obj(cls, obj: 'DynModel') -> 'DynKey':
    #     structure = obj.api.structure
    #     hash_name = structure.dyn_hash_key_name
    #
    #     if not hash_name:
    #         raise DynamoError(
    #             f"While constructing {structure.model_cls}, found no hash-key field. "
    #             f"You must have at least one hash-key field."
    #         )
    #
    #     hash_value = getattr(obj, hash_name)
    #
    #     if hash_value is None:
    #         raise DynamoError(
    #             f"Unable to get DynKey due to `None` for dynamo hash-key ({hash_value}) "
    #             f"on object {obj}."
    #         )
    #
    #     range_name = structure.dyn_range_key_name
    #     range_value = None
    #     if range_name:
    #         range_value = getattr(obj, range_name)
    #         if range_value is None:
    #             raise DynamoError(
    #                 f"Unable to get DynKey due to `None` for dynamo range-key ({range_name}) "
    #                 f"on object {obj}."
    #             )
    #
    #     return DynKey(api=obj.api, hash_key=hash_value, range_key=range_value)

    def key_as_dict(self):
        client = self.client
        hash_field = client.hash_key_info
        range_field = client.sort_key_info

        # Append the keys for the items we want into what we will request.
        item_request = {hash_field.dy_name: self.hash_key}
        if range_field:
            item_request[range_field.dy_name] = self.range_key
        return item_request

    def __post_init__(self):
        client = self.client
        delimiter = client.full_key_deliminator
        sort_info = client.sort_key_info
        hash_info = client.hash_key_info
        need_range_key = bool(sort_info)
        if need_range_key:
            range_name = sort_info.dy_name

        hash_key = self.hash_key
        range_key = self.range_key

        _id = self.id
        if _id is not None and not isinstance(_id, str):
            # `self.id` must always be a string.
            # todo: Must check for standard converter method
            _id = str(_id)
            object.__setattr__(self, 'id', _id)

        require_full_key = self.require_full_key

        # First, figure out `self.id` if not provided.
        if not _id:
            if not hash_key:
                raise DynamoError(
                    f"Tried to create DynKey with no id ({_id}) or no hash key ({hash_key})."
                )

            if require_full_key and need_range_key and not range_key:
                raise DynamoError(
                    f"Tried to create DynKey with no id ({_id}) or no range key ({range_key})."
                )

            key_names = [(hash_info.dy_name, hash_key)]
            # Generate ID without delimiter to represent an entire hash-page (ie: any range value)
            if need_range_key and range_key is not None:
                key_names.append((range_name, range_key))

            keys = []
            for key_name, key_value in key_names:
                final_value = key_value
                if self.range_operator == 'between' and isinstance(key_value, list):
                    sub_v_result = []
                    for sub_v in key_value:
                        sub_v_result.append(str(sub_v))
                    # TODO: Is it really a plain-string deliminated with commas for the BETWEEN values for range-key?
                    #       Verify/Investigate this at some point
                    final_value = ",".join(sub_v_result)
                keys.append(final_value)

            _id = delimiter.join([str(x) for x in keys])
            object.__setattr__(self, 'id', _id)
        elif need_range_key and delimiter not in _id:
            raise DynamoError(
                f"Tried to create DynKey with an `id` ({_id}) "
                f"that did not have delimiter ({delimiter}) in it. "
                f"This means we are missing the range-key part for field ({range_name}) "
                f"in the `id` that was provided.  Trying providing the id like this: "
                f'"{_id}{delimiter}'
                r'{range-key-value-goes-here}".'  # <-- want to directly-output the `{` part.
            )

        # If we got provided a hash-key directly, no need to continue any farther.
        if hash_key:
            if require_full_key and need_range_key and not range_key:
                raise DynamoError(
                    f"Have hash_key ({hash_key}) without needed range_key while creating DynKey."
                )
            # We were provided the hash/range key already, as an optimization I don't use time
            # checking to see if they passed in the same values that they may have passed in `id`.
            return

        # They did not pass in hash_key, so we must parse the `id` they provided
        # and then set them on self.

        if not need_range_key:
            # If we don't need range key, there is no delimiter to look for.
            hash_key = _id
        else:
            split_id = _id.split(delimiter)
            if len(split_id) != 2:
                raise DynamoError(
                    f"For dynamo table ({client.table_name} / {client.obj_type}): Have id ({_id}) but delimiter "
                    f"({delimiter}) is either not present, or is in it more than once. "
                    f"'id' needs to contain exactly one hash and range key combined together "
                    f"with the delimiter, ie: 'hash-key-value{delimiter}range-key-value'. "
                    f"See xdynamo.dyn_connections documentation for more details on how "
                    f"this works."
                )

            hash_key = convert_to_key_type(client, client.hash_key_info, split_id[0])
            range_key = convert_to_key_type(client, client.sort_key_info, split_id[1])

        object.__setattr__(self, 'hash_key', hash_key)
        object.__setattr__(self, 'range_key', range_key)


class QueryKey(BaseModel):
    values: list[str | int | float] | None
    operator: str | None
    dy_name: str


def convert_to_key_type(client: DynObjManager, key_field: DynFieldInfo, value: str | int | float) -> str | int | float:
    match key_field.dy_type:
        case 'S':
            return str(value)
        case 'N':
            if key_field.py_type is float:
                return float(value)
            return int(value)

    raise DynamoError(f'Unsupported py_type/dy_type for key field ({key_field}) for client ({client}).')


class QueryCriteria(dict):
    client: DynObjManager
    # key: Key | None = None
    condition: Query | None = None

    def copy(self) -> QueryCriteria:
        qc = QueryCriteria(self)
        qc.client = self.client
        qc.condition = self.condition
        return qc

    @classmethod
    def from_client__for_query(
            cls, client: DynObjManager, query: Query | QueryCriteria, *, condition: Query | None | DefaultType = Default
    ) -> QueryCriteria:
        return cls.from_client__for_key(client, query, condition=condition)  # noqa

    @classmethod
    def from_client__for_key(cls, client: DynObjManager, key: Key | str, *, condition: Query | None | DefaultType = Default) -> QueryCriteria:
        if isinstance(key, QueryCriteria):
            assert key.client is client
            if condition is Default:
                return key

            if condition == key.condition:
                return key

            obj = key.copy()
            obj.condition = condition
            return obj

        if isinstance(key, BaseModel):
            from pydantic_dyn.dynamo_model import DynamoModel
            if not isinstance(key, DynamoModel):
                # TODO: do a model-dump to get the keys post-conversion [instead of doing it lazily later without model]
                #   OR we could save a link to model on self for future ref in lazy property???....
                # For now...
                raise NotImplementedError("It's currently unsupported to use a basic Pydantic model as a lookup key.")

            # Get the key's "id" value (will be either `str` or `int`), and use that.
            key = key.dyn_id

        # TODO: 'str' should be the key(s), in a full-id/str-format.
        if isinstance(key, (str, DynKey)):
            hash_info = client.hash_key_info
            sort_info = client.sort_key_info
            has_sort_key = bool(sort_info)

            if has_sort_key:
                if isinstance(key, DynKey):
                    # TODO: Probably/May need to support only a 'hash-key' (for getting all items for hash key)?
                    values = [key.hash_key, key.range_key]
                else:
                    values = key.split(client.full_key_deliminator)

                if len(values) != 2:
                    raise DynamoError(f'While splitting [via "{client.full_key_deliminator}"] '
                                      f'full-key string into hash/sort key ("{key}"), '
                                      f'did not get exactly two values ({values}).')
                hash_key_value, sort_key_value = values
                # Convert them to an `int` if needed:
                hash_key_value = convert_to_key_type(client, hash_info, hash_key_value)
                sort_key_value = convert_to_key_type(client, sort_info, sort_key_value)
            else:
                hash_key_value = key
                sort_key_value = None

            obj = QueryCriteria()
            obj[hash_info.name or hash_info.dy_name] = hash_key_value
            obj.condition = condition or None
            obj.client = client

            # Pre-calculate dyn_values_map, so we don't re-convert the values;
            # plus we already know exactly what we want...
            obj.dyn_values_map = {hash_info.dy_name: {'eq': hash_key_value}}

            if has_sort_key:
                obj[sort_info.name or sort_info.dy_name] = sort_key_value
                obj.dyn_values_map[sort_info.dy_name] = {'eq': sort_key_value}

            return obj

        # TODO: Whatever type is the base-dynamo table class goes here.
        if isinstance(key, BaseModel):
            # TODO: do a model-dump to get the keys post-conversion [instead of doing it lazily later without model]
            #   OR we could save a link to model on self for future ref in lazy property???....

            raise NotImplementedError

        # TODO: Accommodate 'BaseModel' keys.
        obj = QueryCriteria(key)
        obj.client = client
        if condition is not Default:
            obj.condition = condition

        return obj

    @cached_property
    def dyn_values_map(self) -> dict[str, dict[str, Any]]:
        """ Returns a dict with dynamo attr-names as keys,
            with a sub-dict that maps dynamo-operator with final/formatted query-value.

        """
        # TODO: Look at serialization alias (include any alias generators) to get final version;
        #   default to `str`-types for now if no field found.
        #   (idea for future: expose `QueryCritera` and allow explict setting of type to use for a field/attr).
        processed_query = {}
        client = self.client
        dyn_fields = client.dyn_fields

        # TODO: **IN PROCESS**: Move this operators get into list of a dict-key item into a separate sub-dict,
        #   created lazily.  The `self` direct-dict items should be left un-modified.

        for (k, value) in self.items():
            operator = None
            name = k
            value_is_list = isinstance(value, list)
            if '__' in name and name not in client.attr_names:
                parts = k.split("__")
                name = '__'.join(parts[0:-1])
                if len(parts) > 1:
                    operator = parts[-1]

            # When the operator is not provided, we guess the best one to use
            if operator is None:
                # If it's a list, we do the 'in' operator by default.
                if value_is_list:
                    operator = "in"

            # Map alias operators to the standard one, otherwise keep current operator.
            operator = operator_alias_map.get(operator, operator)

            # Store value / operator in sub-dict...
            criterion = processed_query.setdefault(name, {})
            dyn_field = dyn_fields.get(name)
            if not dyn_field:
                dyn_field = DynFieldInfo(name=name, dy_name=name, py_type=str)

            # Serialize the values as-if they were dumped from the model
            # (but without using a real model instance).
            if value_is_list:
                value = [_internal.serialize_dyn_field(client, dyn_field, v) for v in value]
            else:
                value = _internal.serialize_dyn_field(client, dyn_field, value)
            criterion[operator] = value
        return processed_query

    @cached_property
    def key_as_dict(self):
        """ Assumes only thing in self are single hash/sort key; and it returns only that. """
        client = self.client
        hash_field = client.hash_key_info
        sort_field = client.sort_key_info

        # TODO: We may not have either of these keys in self; ie: filters for a `scan`.
        # TODO: the key in self may be `{hash_field}__in` or some such, see `dyn_attr_and_operators`?
        #   Only use this currently with `delete` with a list of items, so perhaps we are ok?

        item_request = {hash_field.dy_name: self._get_plain_value_for_key_dyn_field(hash_field)}
        if sort_field:
            item_request[sort_field.dy_name] = self._get_plain_value_for_key_dyn_field(sort_field)
        return item_request

    def _get_plain_value_for_key_dyn_field(self, dyn_field: DynFieldInfo):
        dyn_values = []
        for query_key in self._query_keys_for_dyn_field(dyn_field):
            # TODO: Format `dyn_values` better, and do a DynamoException instead.
            assert query_key.values, f'Must have a non-blank, non-None value for hash key; query ({self}) [1].'
            operator = query_key.operator
            assert operator == 'eq'
            assert len(query_key.values) == 1
            value = query_key.values[0]
            assert value
            dyn_values.append(query_key.values[0])

        assert dyn_values, f'Must have at least one value to query for hash/sort key; query ({self}).'

        # We put a double-dash between each element
        # (don't need to parse this later, just need to make it a unique key for any given set of elements)
        if len(dyn_values) == 1:
            return dyn_values[0]

        return '--'.join([str(v) for v in dyn_values])

    def _query_keys_for_dyn_field(self, dyn_field: DynFieldInfo) -> list[QueryKey]:
        dyn_values_map = self.dyn_values_map
        dyn_values = []
        if dyn_field.dy_name in dyn_values_map:
            obj = dyn_values_map[dyn_field.dy_name]
            for operator, value in obj.items():
                values = [*value] if isinstance(value, list) else [value]
                dyn_values.append(QueryKey(dy_name=dyn_field.dy_name, values=values, operator=operator))
        single_name_values = dyn_values

        dyn_values = []
        for dy_name, v in {k: dyn_values_map.get(k, Default) for k in dyn_field.names}.items():
            if dy_name == dyn_field.dy_name:
                # Skip the single-key, that case is already handled (above; ^).
                continue
            if not v:
                dyn_values.append(QueryKey(dy_name=dy_name, values=None, operator=None))
                continue

            for operator, value in v.items():
                values = [*value] if isinstance(value, list) else [value]
                dyn_values.append(QueryKey(dy_name=dy_name, values=values, operator=operator))

        if single_name_values and dyn_values:
            raise DynamoError(
                f'Currently unsupported to query simultaneously with both a composite key and a single-key, '
                f'for query ({self}).'
            )

        # We put a double-dash between each element
        # (don't need to parse this later, just need to make it a unique key for any given set of elements)
        return [*dyn_values, *single_name_values]

    @cached_property
    def contains_only_keys(self):
        """ Return True if we have a key, and no other non-keys; Otherwise False.
            This may mean that there is a sort-key, but no hash-key.

            TODO: Does this seem ok ^, or should this validate we have at least a hash-key?
        """
        client = self.client
        key_names = {*client.hash_key_dy_names, *(client.sort_key_dy_names or [])}
        range_key = client.sort_key_name
        if range_key:
            key_names.add(range_key)

        dyn_values_map = self.dyn_values_map
        have_key = False
        for name in dyn_values_map:
            if name not in key_names:
                return False
            have_key = True

        return have_key

    @cached_property
    def dyn_keys(self) -> Set[DynKey]:
        """ Generate a set of DynKey's. This way they are uniquified. """
        client = self.client
        hash_info = client.hash_key_info
        sort_info = client.sort_key_info
        dyn_keys = set()
        dyn_values_map = self.dyn_values_map

        hash_keys = self._query_keys_for_dyn_field(hash_info)
        sort_keys = self._query_keys_for_dyn_field(sort_info) if sort_info else None

        if hash_keys and sort_keys:
            hash_gen = self.generate_all_operator_values_for_query_keys(hash_keys)
            range_list = list(self.generate_all_operator_values_for_query_keys(sort_keys))

            # Go though every combination of hash + range keys....
            for hash_combo in hash_gen:
                range_gen = xloop(range_list)
                for range_combo in range_gen:
                    range_operator = range_combo[0]
                    if range_operator == 'is_in':
                        range_operator = 'eq'

                    if range_operator in between_operators:
                        next_range = next(range_gen, None)
                        if next_range is None:
                            raise DynamoError(
                                f"You must provide a second value for 'between' operator on range "
                                f"key ({sort_info.dy_name}), next value ({next_range[1]} had operator "
                                f"({next_range[0]})."
                            )

                        if next_range[0] not in between_operators:
                            raise DynamoError(
                                f"You must provide a second value for 'between' operator on range "
                                f"key ({sort_info.dy_name}), next value ({next_range[1]} had operator "
                                f"({next_range[0]})."
                            )
                        dyn_key = DynKey(
                            client=client,
                            hash_key=hash_combo[1],
                            range_key=[range_combo[1], next_range[1]],
                            range_operator='between'
                        )
                    else:
                        dyn_key = DynKey(
                            client=client,
                            hash_key=hash_combo[1],
                            range_key=range_combo[1],
                            range_operator=range_operator
                        )
                    dyn_keys.add(dyn_key)
        elif hash_keys:
            for operator, value in self.generate_all_operator_values_for_query_keys(hash_keys):
                dyn_key = DynKey(
                    client=client,
                    hash_key=value,
                    # We want a key that represents an entire hash-page if there is a range-key.
                    require_full_key=False
                )
                dyn_keys.add(dyn_key)

        # # TODO: I don't think the below is needed?
        # #     Because the `_query_keys_for_dyn_field` figures out the correct field to use for the 'key' for us.
        # if 'id' != range_key and 'id' in self:
        #     for operator, value in self.generate_all_operator_values_for_query_keys('id'):
        #         dyn_key = DynKey(
        #             api=api,
        #             id=value,
        #             range_operator=operator
        #         )
        #         dyn_keys.add(dyn_key)

        return dyn_keys

    def generate_all_operator_values_for_query_keys(self, query_keys: list[QueryKey]) -> Iterable[tuple[str, str | int | float]]:
        """
        Produces a generator that will go though all operator/value combinations for
        the query param `name`. Each yield will be a tuple with operator as first,
        and value as second item in tuple.

        Args:
            name: name in query to use

        Returns: Tuple, first is operator and second is value.
            If the value is a list, will yield each value in list as a separate item.

        """
        assert query_keys

        operator = query_keys[0].operator
        for qk in query_keys:
            if qk.operator != operator:
                raise DynamoError(f'You must use same operator for all composite-key values in query '
                                  f'({operator}) != {qk.operator}) for query-key ({qk}) for query ({self}).')
            if qk.values is None:
                raise DynamoError(f'YOu must have at least one value for all composite-key values in query ({self}).')

        if operator is None:
            operator = 'eq'

        name_to_qks: dict[str, QueryKey] = {}
        for qk in query_keys:
            cur_name = qk.dy_name
            assert cur_name not in name_to_qks
            name_to_qks[cur_name] = qk

        # Check to see if we are a composite key.
        if len(name_to_qks) == 1:
            for qk in query_keys:
                for v in qk.values:
                    assert qk.operator, 'We got a none-operator with non-composite key field.'
                    yield qk.operator, v

        dy_name_to_position = {k: i for i, k in enumerate(self.client.dyn_fields)}

        # Deal with composite key (ie: more than one field on model makes up either the hash or sort key);
        # We need to combine them together into a single value.
        names = list(name_to_qks.keys())
        names.sort(key=lambda x: dy_name_to_position[x])
        at_positions = {k: 0 for k in names}
        names_end_index = len(at_positions) - 1
        while True:
            qk_combination = []
            for name in names:
                i = at_positions[name]
                qk = name_to_qks[name]
                value = qk.values[i]
                assert value is not None
                qk_combination.append(str(value))

            composite_value = '--'.join(qk_combination)
            yield operator, composite_value

            for name in reversed(names):
                i = at_positions[name]
                qk = name_to_qks[name]
                i += 1
                if len(qk.values) <= i:
                    # We can't increment current position.
                    # Reset to zero and increment next position instead.
                    at_positions[name] = 0
                    continue
                # We can increment current position, do it!
                at_positions[name] = i
                break
            else:
                # We got to first position, and it ticked over;
                # that means we went though every possibility, finished!
                return
