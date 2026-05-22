from zoneinfo import ZoneInfo

from pydantic import BaseModel, BeforeValidator
from pydantic.fields import FieldInfo, Field

from pydantic_dyn import DynClient, KeyType, SortKey, HashKey, DynField, DynamoModel as DynModel, DynClientOptions
from pydantic_dyn import _internal
from typing import List, Dict, Union, Optional, Type, Any, Callable, Tuple, ClassVar, Annotated
from typing import TypeVar
import pytest
from moto import mock_aws
import dataclasses
from xmodel.remote import XRemoteError
import datetime as dt
from dateutil.relativedelta import relativedelta

from pydantic_dyn.errors import DynamoConditionError
from pydantic_dyn.types import DynFieldInfo

T = TypeVar('T')
M = TypeVar('M')

simple_obj_hash_key = "my-hash1"
simple_obj_range_key = "my-range1"


def utc(timezone_name: str = 'utc') -> dt.datetime:
    """ Returns a datetime with current utc. """
    if timezone_name == 'utc':
        return dt.datetime.now(dt.timezone.utc)

    return dt.datetime.now(ZoneInfo(timezone_name))

# ----------------------
# ***** My Models ******


class SubObj(BaseModel):
    sub_name: str | None = Field(repr=True, default=None)
    queue: bool | None = None


class ItemWithRangeKeyClient(DynClient):
    last_paginate_params: dict | None = None

    @property
    def unit_test_was_last_consistent(self):
        params = self.last_paginate_params

        # Ensure we don't accidentally use the previous, previous result.
        self.last_paginate_params = None

        if not params:
            return False

        if params.get('ConsistentRead') is True:
            return True

        request_items = list(params.get('RequestItems', {}).values())
        if request_items and request_items[0].get('ConsistentRead') is True:
            return True
        return False

    def _paginate_all_items_generator(self, *args, **kwargs):
        self.last_paginate_params = kwargs['params']
        for v in super()._paginate_all_items_generator(*args, **kwargs):
            yield v


AlwaysConvertToStr = Annotated[str, BeforeValidator(lambda x: str(x))]


class ItemWithRangeKey(DynModel, name=None, validate_assignment=True, coerce_numbers_to_str=True):
    dyn_client: ClassVar[ItemWithRangeKeyClient]

    hash_field: HashKey[str]
    range_field: SortKey[str]
    name: AlwaysConvertToStr | None = None
    basic_bool: bool | None = None
    items: List[Dict[str, str]] | None = None

    do_not_compare: bool = False
    """ Not stored in dynamo, is set to True if assert_with should not assert values,
        This is used with objects that I want to directly delete with only keys and not other
        values.
    """

    def assert_with(self, other: "ItemWithRangeKey"):
        assert other is not self, "We should not be comparing exact same object, caching object?"

        if self.do_not_compare or other.do_not_compare:
            return
        assert self.name == other.name
        assert self.dy_id == other.dy_id
        assert self.basic_bool == other.basic_bool


class ItemWithRangeKeyForStr(ItemWithRangeKey, name="testItemWithRangeKey"):
    hash_field: HashKey[str]
    range_field: SortKey[str]


class ItemWithRangeKeyForInt(ItemWithRangeKey, name="testItemWithRangeWithIntKeys"):
    hash_field: HashKey[int]
    range_field: SortKey[int]


class ItemWithRangeKeyForDateTime(ItemWithRangeKey, name="testItemWithRangeWithIntKeys"):
    hash_field: HashKey[dt.datetime]
    range_field: SortKey[dt.datetime]


class ItemOnlyHash(DynModel, name="testItemOnlyHash"):
    hash_field_id: HashKey[str]
    basic_int: int | None = None
    dict_list: List[Dict[str, str]] | None = None
    basic_str: str | None = None
    child_item: ItemWithRangeKeyForStr | None = None
    sub_item: SubObj | None = None


# ------------------------
# ***** My Fixtures ******

def default_generate_hash_value(obj: 'ObjTestValues', field: FieldInfo, value: Any, x: int):
    # Could use field converter, but keeping it simple.
    return value + field.annotation(x)


def datetime_generate_hash_value(
        obj: 'ObjTestValues',
        field: DynField,
        value: dt.datetime,
        x: int
):
    # Could use field converter, but keeping it simple.
    return value + relativedelta(seconds=x)


ValueGenerator = Callable[["ObjTestValues", DynField, Any, int], Any]


@dataclasses.dataclass
class ObjTestValues:
    model_cls: Type[ItemWithRangeKey]
    hash_value: Any
    range_value: Any
    value_generator: ValueGenerator = default_generate_hash_value

    def generate_hash_value(self, x: int):
        field = self.model_cls.model_fields.get('hash_field')
        return self.value_generator(self, field, self.hash_value, x)

    def generate_range_value(self, x: int):
        field = self.model_cls.model_fields.get('range_field')
        return self.value_generator(self, field, self.range_value, x)

    # def __post_init__(self):
    #     hash_type = type(self.hash_value)
    #     range_type = type(self.range_value)
    #     ok_types = {str, int, float, type(None)}
    #     if hash_type not in ok_types:
    #         type_adapter(str)


@dataclasses.dataclass
class ObjTestDef(ObjTestValues):
    get_via_id: Union[dict, str, ItemWithRangeKeyForStr, _internal.DynKey] = None


@pytest.fixture(params=[
    ObjTestValues(
        model_cls=ItemWithRangeKeyForStr,
        hash_value=simple_obj_hash_key,
        range_value=simple_obj_range_key
    ),
    ObjTestValues(
        model_cls=ItemWithRangeKeyForInt,
        hash_value=2,
        range_value=4
    ),
    ObjTestValues(
        model_cls=ItemWithRangeKeyForDateTime,
        hash_value=utc(),
        range_value=utc(),
        value_generator=datetime_generate_hash_value
    ),
    ObjTestValues(
        model_cls=ItemWithRangeKeyForDateTime,
        hash_value=utc('America/New_York'),
        range_value=utc('America/New_York'),
        value_generator=datetime_generate_hash_value
    ),
])
def simple_obj_values(request) -> ObjTestValues:
    """ Used as base-cases to start with, with various range/hash values/types. """
    return request.param


def compute_key_serialization_from_test_values(values: ObjTestValues) -> tuple[str, str]:
    hash_value = values.hash_value
    range_value = values.range_value
    cls = values.model_cls
    client = cls.dyn_client
    # if isinstance(hash_value, dt.datetime):
    hash_value = _internal.serialize_dyn_field(client, dyn_field=client.hash_key_info, value=hash_value)

    if info := client.sort_key_info:
        range_value = _internal.serialize_dyn_field(client, dyn_field=info, value=range_value)

    return hash_value, range_value


def compute_full_key_from_test_values(values: ObjTestValues) -> str:
    hash_value, range_value = compute_key_serialization_from_test_values(values)
    return f'{hash_value}||{range_value}'


def compute_dyn_key_from_test_values(values: ObjTestValues) -> DynKey:
    hash_value, range_value = compute_key_serialization_from_test_values(values)
    return _internal.DynKey(client=values.model_cls.dyn_client, hash_key=hash_value, range_key=range_value)


@pytest.fixture(params=[
    lambda v: {"hash_field": v.hash_value, "range_field": v.range_value},
    lambda v: compute_full_key_from_test_values(v),
    lambda v: v.model_cls(
            # We want this object to ONLY have the keys on it, not looking it up.
            hash_field=v.hash_value,
            range_field=v.range_value,
            do_not_compare=True
    ),
    lambda v: v.model_cls(hash_field=v.hash_value, range_field=v.range_value).dy_id,
    # lambda v: _internal.DynKey.via_obj(
    #     v.model_cls(hash_field=v.hash_value, range_field=v.range_value)
    # ),
    lambda v: compute_dyn_key_from_test_values(v)
])
def simple_obj_def(request, simple_obj_values) -> ObjTestDef:
    """ Takes base-case via `simple_obj_values` and adds a number of ways to lookup
        objects via id to it via a callable template parameterized value.
    """
    template: Callable[[ObjTestValues], Any] = request.param
    get_via_id = template(simple_obj_values)

    kwargs = {}
    for field in dataclasses.fields(ObjTestValues):
        kwargs[field.name] = getattr(simple_obj_values, field.name)

    return ObjTestDef(
        **kwargs,
        get_via_id=get_via_id,
    )


@pytest.fixture()
def simple_obj(simple_obj_def) -> 'ItemWithRangeKey':
    # Use `simple_obj_2` to two objects into dynamo, helps ensure tests in general
    # don't get this one and only get the simple_obj, where relevant
    obj = simple_obj_def.model_cls(
        hash_field=simple_obj_def.hash_value,
        range_field=simple_obj_def.range_value
    )
    obj.name = "start-name"
    obj.basic_bool = True
    obj.dy_save()

    assert len(list(simple_obj_def.model_cls.dyn_client.get())) == 1, "Have incorrect count."
    return obj


@pytest.fixture()
def simple_obj_second(simple_obj_def) -> 'ItemWithRangeKey':
    hash_extra = simple_obj_def.generate_hash_value(2)
    range_extra = simple_obj_def.generate_range_value(3)

    obj2 = simple_obj_def.model_cls(
        hash_field=hash_extra,
        range_field=range_extra
    )
    obj2.name = range_extra
    obj2.basic_bool = True
    obj2.dy_save()

    assert len(list(simple_obj_def.model_cls.dyn_client.get())) == 2, "Have incorrect count."
    return obj2


@pytest.fixture()
def simple_obj_get_via_id(simple_obj, simple_obj_def) -> Optional[ItemWithRangeKey]:
    """ List of various ways to lookup simple object via `ItemWithRangeKey.api.get_via_id`. """
    get_via_id = simple_obj_def.get_via_id
    if isinstance(get_via_id, DynModel):
        obj = get_via_id
    else:
        obj = simple_obj_def.model_cls.dyn_client.get_first(get_via_id)

    assert obj, f"We were unable to retrieve object via {get_via_id}"
    return obj


@pytest.fixture()
def simple_obj_get_with_read_consistency(simple_obj, simple_obj_def) -> Optional[ItemWithRangeKey]:
    """ List of various ways to lookup simple object via `ItemWithRangeKey.api.get_via_id`. """
    get_via_id = simple_obj_def.get_via_id
    if isinstance(get_via_id, DynModel):
        obj = get_via_id
    else:
        obj = simple_obj_def.model_cls.dyn_client.get_first(get_via_id, consistent_read=True)
        params = obj.dyn_client.last_paginate_params
        assert (
                params.get('ConsistentRead') is True or
                list(params['RequestItems'].values())[0]['ConsistentRead'] is True
        )

    assert obj, f"We were unable to retrieve object via {get_via_id}"
    return obj


@pytest.fixture(autouse=True)
def mock_all_aws_fixture():
    with mock_aws() as mock:
        yield mock


# --------------------------
# ***** My Unit Tests ******

def test_basic_dyn_class():
    class ItemOnlyHash(DynModel, name="testItemOnlyHash"):
        hash_field_id: HashKey[str]
        basic_int: int

    fields = ItemOnlyHash.dyn_client.dyn_fields
    expected_fields = {
        'hash_field_id': DynFieldInfo(dy_name='hash_field_id', name='hash_field_id',
                                      names=['hash_field_id'], py_type=str, key_type=KeyType.hash),
        'basic_int': DynFieldInfo(dy_name='basic_int', name='basic_int', names=['basic_int'],
                                  py_type=int, key_type=None)
    }

    assert fields == expected_fields


# def test_basic_json_with_blank_data():
#     model = SubObj()
#     model.sub_name = "398221"
#
#     results = model.
#     # `model.queue` should no be in JSON, since it should default to `None` since it was unset.
#     assert results == {"sub_name": "398221"}, "Was `queue` value filtered out?"


def test_basic_delete(simple_obj, simple_obj_get_via_id, simple_obj_def):
    """Tests deleting data generated via simple_obj fixture via simple_obj_get_via_id fixture."""
    # Try to delete it and see if getting it again will return None now.
    simple_obj_get_via_id.dy_delete()
    value = simple_obj_def.model_cls.dyn_client.get_first(simple_obj.dy_id)
    assert value is None


# def test_deleting_via_delete_objs_via_dynkey(simple_obj, simple_obj_get_via_id, simple_obj_def):
#     dyn_key = _internal.DynKey.via_obj(simple_obj)
#
#     # Try to delete it using only dyn-key see if getting it again will return None now.
#     simple_obj_def.model_cls.api.client.delete_objs([dyn_key])
#     assert simple_obj_def.model_cls.api.get_via_id(simple_obj.id) is None
#
#
# def test_deleting_via_delete_obj_via_dynkey(simple_obj, simple_obj_get_via_id, simple_obj_def):
#     dyn_key = _internal.DynKey.via_obj(simple_obj)
#
#     # Try to delete it using only dyn-key see if getting it again will return None now.
#     simple_obj_def.model_cls.api.client.delete_obj(dyn_key)
#     assert simple_obj_def.model_cls.api.get_via_id(simple_obj.id) is None


def test_getting_simple_obj(simple_obj, simple_obj_get_via_id):
    # See if all of the simple objects we get compare the same to original simple object.
    simple_obj.assert_with(simple_obj_get_via_id)


def test_getting_simple_obj_with_read_consistency(simple_obj, simple_obj_get_with_read_consistency):
    simple_obj.assert_with(simple_obj_get_with_read_consistency)


def test_getting_simple_obj(simple_obj, simple_obj_get_via_id):
    # See if all of the simple objects we get compare the same to original simple object.
    simple_obj.assert_with(simple_obj_get_via_id)


def test_getting_multiple_obj(simple_obj, simple_obj_second, simple_obj_def):
    model_cls = simple_obj_def.model_cls

    name1 = "my-name-1"
    name2 = "my-name-2"
    hash2 = simple_obj_def.generate_hash_value(10)
    range1 = simple_obj_def.generate_range_value(1)
    range2 = simple_obj_def.generate_range_value(2)
    some_other_range = simple_obj_def.generate_range_value(521)

    # Two more items, each with the same hash but different range-key.
    obj1 = model_cls(hash_field=hash2, range_field=range1, name=name1)
    obj2 = model_cls(hash_field=hash2, range_field=range2, name=name2)

    simple_obj_other = model_cls(hash_field=simple_obj.hash_field, range_field=some_other_range,
                                 name="simple_obj_other")
    simple_obj_other.dy_save()

    all_objs = [simple_obj, obj1, obj2, simple_obj_second, simple_obj_other]
    original_obj_map = {obj.dy_id: obj for obj in all_objs}

    # Bulk-insert multiple objects at the same time....
    model_cls.dyn_client.put([obj1, obj2])

    # Verify we can lookup all objects in table.
    objs = list(model_cls.dyn_client.get())
    assert len(objs) == len(all_objs)

    for obj in objs:
        obj.assert_with(original_obj_map[obj.dy_id])

    # lookup objects only for a specific hash....
    objs = list(model_cls.dyn_client.get(query={"hash_field": hash2}))
    original_obj_map = {obj.dy_id: obj for obj in [obj1, obj2]}
    assert len(objs) == len(original_obj_map.values())
    for obj in objs:
        obj.assert_with(original_obj_map[obj.dy_id])

    # See if we can query via list (which should use `in` operator by default)....
    objs = list(model_cls.dyn_client.get(
        query={"hash_field": hash2, "name": [name2, name1]}
    ))
    assert len(objs) == len(original_obj_map.values())
    for obj in objs:
        obj.assert_with(original_obj_map[obj.dy_id])

    # See if we can query via in operator explicitly
    objs = list(model_cls.dyn_client.get(
        query={"hash_field": hash2, "name__in": [name2, name1]}
    ))
    assert len(objs) == len(original_obj_map.values())
    for obj in objs:
        obj.assert_with(original_obj_map[obj.dy_id])

    # name is not a list in any of the table's items, so when used `exact` with list nothing
    # should come back (since they are all strings).
    objs = list(model_cls.dyn_client.get(
        query={"hash_field": hash2, "name__exact": [name2, name1]}
    ))
    assert len(objs) == 0

    # See if we can query by non-keys + hash at same time correctly.
    objs = list(model_cls.dyn_client.get(
        query={"hash_field": hash2, "name": name2}
    ))
    original_obj_map = {obj.dy_id: obj for obj in [obj2]}
    assert len(objs) == len(original_obj_map.values())
    for obj in objs:
        obj.assert_with(original_obj_map[obj.dy_id])

    # Lookup multiple objects via different hashes:
    objs = list(model_cls.dyn_client.get(
        query={"hash_field": [simple_obj.hash_field, hash2]}
    ))
    original_obj_map = {obj.dy_id: obj for obj in [simple_obj, simple_obj_other, obj1, obj2]}
    assert len(objs) == len(original_obj_map.values())
    for obj in objs:
        obj.assert_with(original_obj_map[obj.dy_id])

    # Single hash multiple range, but should only match one result.
    objs = list(model_cls.dyn_client.get(
        query={
            "hash_field": simple_obj_other.hash_field,
            "range_field": [simple_obj_other.range_field, hash2]
        }
    ))
    original_obj_map = {obj.dy_id: obj for obj in [simple_obj_other]}
    assert len(objs) == len(original_obj_map.values())
    for obj in objs:
        obj.assert_with(original_obj_map[obj.dy_id])

    # Single hash multiple range, but should only match two result.
    objs = list(model_cls.dyn_client.get(
        query={
            "hash_field": [simple_obj_other.hash_field, obj1.hash_field],
            "range_field": [simple_obj_other.range_field, obj1.range_field]
        }
    ))
    original_obj_map = {obj.dy_id: obj for obj in [obj1, simple_obj_other]}
    assert len(objs) == len(original_obj_map.values())
    for obj in objs:
        obj.assert_with(original_obj_map[obj.dy_id])

    range_result = list(model_cls.dyn_client.get({
        'hash_field': simple_obj_other.hash_field,
        'range_field__range': [simple_obj_other.range_field, simple_obj_other.range_field]
    }))

    single_obj_result = list(model_cls.dyn_client.get({
        'hash_field': simple_obj_other.hash_field,
        'range_field': simple_obj_other.range_field
    }))

    assert len(range_result) == 1
    assert len(single_obj_result) == 1
    range_result[0].assert_with(single_obj_result[0])


def test_send_obj_with_related_child_and_sub_obj(simple_obj_values):
    o_with_range = ItemWithRangeKeyForStr(hash_field="my-hash3", range_field="my-range3", name="my-name3", hello=False)

    o_with_range.dy_save()

    o2 = ItemOnlyHash(hash_field_id="test-id3")
    o2.child_item = o_with_range

    # todo: Consider supporting setting a 'JSONDict' directly on sub-item field and having it
    #  automatically create the proper sub-item type [via `ModelAsSubJsonDict(json_dict)`]?
    o2.sub_item = SubObj(sub_name="my-sub-name", queue=True)
    # ----> FYI: could also do it by passing json-dict via first argument:
    # SubObj({'sub_name': "my-sub-name", 'queue': True})

    o2.dy_save()
    assert o2.sub_item.model_dump(mode='json') == {"sub_name": "my-sub-name", "queue": True}

    o_gotten = ItemOnlyHash.dyn_client.get_first("test-id3")
    original_json = o_gotten.sub_item.model_dump(mode='json')
    got_json = o_gotten.sub_item.model_dump(mode='json')

    # Ensure we got something and they are the same
    assert original_json == got_json

    # Ensure child-object can be lazily looked up, and data in it is correct.
    o_gotten.child_item.assert_with(o_with_range)


def test_pagination(simple_obj_values):
    model_cls = simple_obj_values.model_cls

    # Concatenating this much makes str type keys get large;
    # Makes it paginate a number of times.
    # Dynamo pagination is based on data size in each page of results.
    # A small number of objects with lots of data is much faster then tens of thousands of objects.
    lots_of_data = simple_obj_values.generate_range_value(0)
    if model_cls.dyn_client.hash_key_info.py_type is str:
        for x in range(15):
            lots_of_data += simple_obj_values.generate_range_value(x)

    with _internal.DynBatch():
        for x in range(40):
            obj = model_cls(hash_field=lots_of_data, range_field=simple_obj_values.generate_range_value(x))
            obj.dy_save()

    result = list(model_cls.dyn_client.get())
    assert len(result) == 40


# class MultipleHashes(DynModel, name="tableWithError1"):
#     """ This class has an intentional error, it has two hash fields. """
#     hash_field_1: str = HashField()
#     hash_field_2: str = HashField()
#
#
# class MultipleRanges(DynModel, name="tableWithError2"):
#     """ This class has an intentional error, it has two range fields. """
#     hash_field_1: HashKey[int]
#     range_field_1: int = RangeField()
#     range_field_2: str = RangeField()


# def test_multiple_keys_error():
#     """ See if we detect multiple hash/range fields in structure correctly.
#         System should lazily figure this out the first time we ask the the BaseModel's
#         `DynModel.api`.
#     """
#     with pytest.raises(XRemoteError):
#         MultipleHashes.api.get()
#     with pytest.raises(XRemoteError):
#         MultipleRanges.api.get()


def test_scan_raise_exception():
    with pytest.raises(NotImplementedError, match='There are no hash-keys'):
        ItemWithRangeKeyForStr.dyn_client.get({'name': 'hello'})


def test_scan_with_read_consistency_works(simple_obj, simple_obj_def):
    # Just see if a simple scan works without problems
    result = list(simple_obj_def.model_cls.dyn_client.get(consistent_read=True))
    assert len(result) == 1
    assert result[0].dyn_client.last_paginate_params['ConsistentRead'] is True


@pytest.mark.parametrize("test_input", [
    {'hash_field': "1", 'some_other_param': "3"},
    {'hash_field': "1", 'range_field': "2"},
    {'hash_field': "1"}
])
def test_see_if_read_consistency_used(test_input):
    client = ItemWithRangeKeyForStr.dyn_client

    list(client.get(test_input))
    assert not client.unit_test_was_last_consistent

    list(client.get(test_input, consistent_read=True))
    assert client.unit_test_was_last_consistent

    with DynClientOptions(consistent_reads=True):
        list(client.get(test_input))
        assert client.unit_test_was_last_consistent

    with DynClientOptions(consistent_reads=True):
        list(client.get(test_input, consistent_read=False))
        assert not client.unit_test_was_last_consistent

    # Next test the class arg `dyn_consistent_read` works correctly.
    class ItemWithRangeKeyForStrWithDefaultConsistent(ItemWithRangeKeyForStr, consistent_reads=True):
        # TODO: Figure out and fix why we can't inherit hash/range fields, we must define it again?
        hash_field: HashKey[str]
        range_field: SortKey[str]

    client = ItemWithRangeKeyForStrWithDefaultConsistent.dyn_client

    list(client.get(test_input))
    assert client.unit_test_was_last_consistent

    list(client.get(test_input, consistent_read=False))
    assert not client.unit_test_was_last_consistent

    with DynClientOptions(consistent_reads=False):
        list(client.get(test_input))
        assert not client.unit_test_was_last_consistent


def test_scan_fallback():
    ItemWithRangeKeyForStr(hash_field='hash', range_field='range', name='first').dy_save()
    ItemWithRangeKeyForStr(hash_field='other-h', range_field='other-r', name='second').dy_save()

    items = ItemWithRangeKeyForStr.dyn_client.get({'name': 'second'}, allow_scan=True)
    items = list(items)
    assert len(items) == 1
    assert items[0].hash_field == 'other-h'
    assert 'ConsistentRead' not in items[0].dyn_client.last_paginate_params
    assert 'ScanIndexForward' not in items[0].dyn_client.last_paginate_params


def test_conditional_delete():
    ItemWithRangeKeyForStr(hash_field='h1', range_field='r1', name='n1').dy_save()
    o = ItemWithRangeKeyForStr(hash_field='h2', range_field='r2', name='n2')
    o.dy_save()
    assert len(list(ItemWithRangeKeyForStr.dyn_client.get())) == 2

    # try to delete with a condition that fails.
    with pytest.raises(DynamoConditionError, match=r"condition failed: \{'name': 'n1'\}",
                       check=lambda e: e.condition == {'name': 'n1'}):
        o.dy_delete(condition={'name': 'n1'})

    assert len(list(ItemWithRangeKeyForStr.dyn_client.get())) == 2
    o.dy_delete(condition={'name': 'n2'})
    objs = list(ItemWithRangeKeyForStr.dyn_client.get())

    assert len(objs) == 1
    assert objs[0].hash_field == 'h1'
    assert objs[0].range_field == 'r1'


def test_conditional_put():
    ItemWithRangeKeyForStr(hash_field='h1', range_field='r1', name='n1').dy_save()
    o = ItemWithRangeKeyForStr(hash_field='h2', range_field='r2', name='n2')
    o.dy_save()
    assert len(list(ItemWithRangeKeyForStr.dyn_client.get())) == 2

    # modify (so there is a change to send), see if it won't update due to condition.
    o.items = [{'a': 2}]
    with pytest.raises(DynamoConditionError, match=r"condition failed: \{'name': 'n1'\}",
                       check=lambda e: e.condition == {'name': 'n1'}):
        o.dyn_client.put(o, condition={'name': 'n1'})

    assert ItemWithRangeKeyForStr.dyn_client.get_first(o.dy_id).items is None

    o.items = [{'a': 3}]
    o.dy_save(condition={'name': 'n2'})

    assert ItemWithRangeKeyForStr.dyn_client.get_first(o.dy_id).items == [{'a': '3'}]


def test_reverse():
    ItemWithRangeKeyForStr(hash_field='hash', range_field='range-a', name='first').dy_save()
    ItemWithRangeKeyForStr(hash_field='hash', range_field='range-b', name='second').dy_save()

    items = ItemWithRangeKeyForStr.dyn_client.get({'hash_field': 'hash'})
    items = list(items)
    assert len(items) == 2
    assert items[0].range_field == 'range-a'
    assert 'ScanIndexForward' not in items[0].dyn_client.last_paginate_params

    rev_items = ItemWithRangeKeyForStr.dyn_client.get({'hash_field': 'hash'}, reverse=True)
    rev_items = list(rev_items)
    assert len(rev_items) == 2
    assert rev_items[0].range_field == 'range-b'
    assert 'ScanIndexForward' in rev_items[0].dyn_client.last_paginate_params

    item = ItemWithRangeKeyForStr.dyn_client.get({'hash_field': 'hash', 'range_field': 'range-a'}, reverse=True)
    item = list(item)
    assert len(item) == 1
    assert item[0].range_field == 'range-a'


def test_multiple_hash_fields():
    class MultiHashFieldModel(DynModel):
        hash_field1: HashKey[str]
        hash_field2: HashKey[str]

    o1 = MultiHashFieldModel(hash_field1='h1', hash_field2='h2')
    MultiHashFieldModel.dyn_client.put(o1)

    o1_via_get = list(MultiHashFieldModel.dyn_client.get())[0]
    assert o1.model_dump()
    assert o1_via_get.model_dump() == dict(hash_field1='h1', hash_field2='h2')
    assert o1.dy_id == 'h1--h2'


def test_multiple_hash_fields__w_sort():
    class MultiHashFieldModel(DynModel):
        hash_field1: HashKey[str]
        hash_field2: HashKey[str]
        sort_field1: SortKey[str]

    o1 = MultiHashFieldModel(hash_field1='h1', hash_field2='h2', sort_field1='s1')
    MultiHashFieldModel.dyn_client.put(o1)

    o1_via_get = list(MultiHashFieldModel.dyn_client.get())[0]
    assert o1.model_dump()
    assert o1_via_get.model_dump() == dict(hash_field1='h1', hash_field2='h2', sort_field1='s1')
    assert o1.dy_id == 'h1--h2||s1'


def test_multiple_hash_and_sort_fields():
    class MultiHashFieldModel(DynModel):
        hash_field1: HashKey[str]
        hash_field2: HashKey[str]
        sort_field1: SortKey[str]
        sort_field2: SortKey[str]

    o1 = MultiHashFieldModel(hash_field1='h1', hash_field2='h2', sort_field1='s1', sort_field2='s2')
    MultiHashFieldModel.dyn_client.put(o1)

    o1_via_get = list(MultiHashFieldModel.dyn_client.get())[0]
    assert o1.model_dump()
    assert o1_via_get.model_dump() == dict(hash_field1='h1', hash_field2='h2', sort_field1='s1', sort_field2='s2')
    assert o1.dy_id == 'h1--h2||s1--s2'


def test_single_hash_and_multiple_sort_fields():
    class MultiHashFieldModel(DynModel):
        hash_field1: HashKey[str]
        sort_field1: SortKey[str]
        sort_field2: SortKey[str]

    o1 = MultiHashFieldModel(hash_field1='h1', sort_field1='s1', sort_field2='s2')
    MultiHashFieldModel.dyn_client.put(o1)

    o1_via_get = list(MultiHashFieldModel.dyn_client.get())[0]
    assert o1.model_dump()
    assert o1_via_get.model_dump() == dict(hash_field1='h1', sort_field1='s1', sort_field2='s2')
    assert o1.dy_id == 'h1||s1--s2'
