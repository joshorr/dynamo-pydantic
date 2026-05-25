from contextlib import ExitStack
from itertools import batched
from typing import (
    TYPE_CHECKING, TypeVar, Union, Sequence, Iterable, Optional, List, Dict, Any, Set, Generic, Type, Self, ClassVar,
    Iterator
)
from boto3.dynamodb import conditions
from boto3.dynamodb.table import BatchWriter
from mypy_boto3_dynamodb.service_resource import Table
from pydantic import BaseModel
from xbool import bool_value
from xinject import Dependency
from pydantic_dyn import settings

from logging import getLogger
from xboto.resource import dynamodb
from xsentinels.default import Default, DefaultType
from xloop import xloop

from .errors import DynamoError, DynamoConditionError
from .table import table_repo
from .types import Key, Query, KeyType, DynFieldInfo, DynParams
from . import _internal

if TYPE_CHECKING:
    from .base import DynamoModel


M = TypeVar('M', default=dict)
log = getLogger(__name__)


class DyObjManagerOptions(Dependency):
    def __init__(self, *, consistent_reads: bool | DefaultType = Default):
        self.consistent_reads = consistent_reads

    consistent_reads: bool | DefaultType = Default
    """ Way to change what the default consistent read value should be,
        If set it will be used over the default value for the model
        (but won't override True/False passed directly to client as method paramter to get/scan/etc.
    """


dy_client_options = DyObjManagerOptions.proxy()
""" Proxy to the current `DyObjManagerOptions` currently used/injected at the current moment.
    Used this object use like you would use normal instance of DyObjManagerOptions.
"""


class DyObjManager(Dependency, Generic[M]):
    """
    Skeleton/Placeholder Class for future work, see story and the other classes in
    this file for more details: https://app.clubhouse.io/xyngular/story/13989
    """

    # This is only here to give IDE's a more concrete-class to use for type/code completion.
    # The type-hint is not otherwise used. That var is valid/gettable on an instance.
    # See `xmodel.remote.client.RemoteClient.api` for more details.
    obj_type: Type[M] = dict
    """ This also works when using the generic type directly, ie: `DyObjManager[SomeModel].obj_type is SomeMode`;
        although type-checkers will flag it, but it still works.
        I set it at class-generic level (so both class and instances know about it).
    """

    full_key_deliminator = '||'
    """ What to use to separate the hash and sort keys when combined together into a string string,
        representing an entire/full key/id for the item in the dynamo table.
    """

    name: str
    table_prefix: str | None | DefaultType = Default
    """ Prefix for table name, prepended before `DyObjManager.name` when constructing the full table name.
    
        You can get the full table name via `DyObjManager.table_name`
        
        By default, this will consult with the `pydantic_dyn.settings.default_prefix_generator`
        for the prefix, whenever it's needed, if one is set there.
        
        Otherwise, if one is not set directly here, there is no prefix generator,
        or DyObjManager.table_prefix is `None`, prefix won't be used.
    """

    dyn_fields: dict[str, DynFieldInfo] = Default

    consistent_read: bool | DefaultType = Default

    def __init__(self, fields: list[DynFieldInfo] | DefaultType = Default):
        if fields is not Default:
            dyn_fields = {}
            for v in fields:
                if not v.name:
                    raise DynamoError(f'When providing fields directly to a ({type(self)}) instance, '
                                      f'must have a `name` assigned to it, currently blank or unassigned.')
                dyn_fields[v.name] = v
            self.dyn_fields = dyn_fields

    def __class_getitem__(cls, typevar_value: Type[Self]):  # noqa
        # This happens only for the type-hint/annotation it's self (to indicate it's the current subclass)
        # there is no need to use a special subclass with `Self`, so just return our plain/current `cls`.
        if typevar_value is Self:
            return cls

        if v := _internal.get_client_for_model_type(cls, typevar_value):
            return v

        return _internal.get_or_create_client_for_model_type(cls, typevar_value)

    def __init_subclass__(cls, _generic_typevar: Type[Any] | None = None, **kwargs):
        cls.obj_type = _generic_typevar  # type: ignore
        return super().__init_subclass__(**kwargs)

    @property
    def attr_names(self) -> frozenset[str]:
        names = {self.hash_key_info.name}
        if v := self.sort_key_name:
            names.add(v)
        return frozenset(names)

    @property
    def hash_key_name(self) -> str:
        if v := self._override_hash_key_name:
            return v
        hash_type = KeyType.hash
        names = [k for k, v in self.dyn_fields.items() if v.key_type is hash_type]
        if len(names) == 1:
            return names[0]

        # TODO: Consider an easy way to default the hash-key name to 'id',
        #  in cases where there is more than one hash-key field?
        return '.'.join(names)

    @property
    def sort_key_name(self) -> str | None:
        if v := self._override_sort_key_name:
            return v
        sort_type = KeyType.sort
        names = [k for k, v in self.dyn_fields.items() if v.key_type is sort_type]
        if len(names) == 1:
            return names[0]

        # TODO: Consider an easy way to default the sort-key name to 'id',
        #  in cases where there is more than one hash-key field?
        return '.'.join(names)

    _override_hash_key_name: str | DefaultType = Default
    _override_sort_key_name: str | None | DefaultType = Default

    @hash_key_name.setter
    def hash_key_name(self, value: str):
        self._override_hash_key_name = value

    @sort_key_name.setter
    def sort_key_name(self, value: str):
        self._override_sort_key_name = value

    def _generate_or_find_field_info_for(self, key_type: KeyType) -> DynFieldInfo | None:
        fields = {k: v for k, v in self.dyn_fields.items() if v.key_type is key_type}

        num_fields = len(fields)
        if num_fields <= 0:
            return None

        if num_fields == 1:
            return next(iter(fields.values()))

        names = [k for k in fields]
        dy_name = f"({key_type}) {'--'.join(names)}"

        # Return a composited field-info; we always use `str` type with a composite attr key.
        return DynFieldInfo(
            key_type=key_type,
            py_type=str,
            name=None,
            names=names,
            dy_name=dy_name
        )

    @property
    def hash_key_info(self) -> DynFieldInfo:
        result = self._generate_or_find_field_info_for(KeyType.hash)
        if result is None:
            raise DynamoError(f'DyObjManager ({self}) must have at least one `hash` (partition) key field.')
        return result

    @property
    def sort_key_info(self) -> DynFieldInfo | None:
        return self._generate_or_find_field_info_for(KeyType.sort)

    def _dy_names_for_dyn_field(self, dyn_field: DynFieldInfo) -> set[str]:
        if dyn_field.name is not None:
            return {dyn_field.dy_name}

        assert len(dyn_field.names) > 1
        dyn_fields = self.dyn_fields
        dy_names = set()
        for other_field_name in dyn_field.names:
            other_info = dyn_fields[other_field_name]
            assert other_info.name and len(other_info.names) == 1
            dy_names.add(other_info.dy_name)
        return dy_names

    @property
    def hash_key_dy_names(self) -> set[str]:
        hash_info = self.hash_key_info
        assert hash_info
        return self._dy_names_for_dyn_field(hash_info)

    @property
    def sort_key_dy_names(self) -> set[str] | None:
        if v := self.sort_key_info:
            return self._dy_names_for_dyn_field(v)
        return None

    @property
    def table(self) -> Table:
        """ Returns the boto3 table resource to use for our related DynModel.
            Don't cache or hang onto this, it's already properly cached for you via the current
            Context and so will work in every situation [unit-tests, config-changes, etc]...
        """
        return table_repo.table__for_client(self, create_if_needed=True)

    @property
    def table_name(self) -> str:
        """
        Fully qualified name of the table in Dynamo as a str.
        Format is: '{self.table_prefix}-{self.name}' or just `{self.name}` if no prefix configured.
        """
        prefix = self.table_prefix
        name = self.name
        if not name:
            raise DynamoError(f'DyObjManager ({self}) must have a `name` assigned to generate table_name with.')

        if v := settings.default_prefix_generator:
            prefix = v(self)

        if not prefix:
            return name

        return f'{prefix}-{name}'

    def id_for(self, obj: M) -> str:
        hash_key = self.hash_key_info
        sort_key = self.sort_key_info

        hash_value = _internal.serialize_dyn_field__from_model(self, hash_key, obj)
        if not sort_key:
            return hash_value

        sort_value = _internal.serialize_dyn_field__from_model(self, sort_key, obj)
        return f'{hash_value}{self.full_key_deliminator}{sort_value}'

    def key_for(self, obj: M) -> str:
        pass

    def delete_key(self, hash_key):
        pass

    def delete_id(self, hash_key):
        pass

    def delete(self, items: Iterable[str | M | Key] | str | M | Key, *, condition: Query | None = None):
        """
         Attempts to delete passed in object from dynamodb table.

         If you pass in a condition, it will be evaluated on the dynamodb-service side and the
         deletes will only happen if the condition is met.
         This can help prevent race conditions if used correctly.

         If there is a batch-writer currently in use, we will try to use that to batch the deletes.

         Keep in mind that if you pass in a `condition`, we can't use the batch write.
         We will instead send of a single-item delete request with the condition attached
         (bypassing any current batch-writer that may be in use).

         Args:
            items: Object to delete, it MUST have values for it's primary key(s)
                (no other values are required, the primary-key values are the only ones used).
            condition: Optional, if passed in we will send this as a `ConditionExpression`
                to dynamo.  You can pass in a Query, ie: the same structure that is used
                for `self.query()` and `self.scan()`.

                We will take the Query correctly format it for you into the `ConditionExpression`.

                If the condition is met, the item will be deleted.

                If the condition is NOT met, we will handle it for you by catch the exception,
                logging about it, setting error on response_state of object, and then returning.
                Conditions are generally used to help prevent race-conditions,
                ie: to prevent a deleted on purpose.

                Most of the time the intent is to ignore what the result is, it was either
                deleted or not;
                ie: it will eventually be handled in some way it if it was not deleted.

                # TODO: Adjust this doc-comment to account for condition/error handling changes.

                If you want to know when a conditional delete fails, you can look at
                deleted objects `obj.api.response_state`.

                It's `had_error` will be `True`, there will be some info in `errors`,
                but also a field error with name `_conditional_check` and code `failed`
                you can easily check for via:

                `....response_state.has_field_error('_conditional_check', 'failed')`

                or you can use `xyn_model_dynamo.const.CONDITIONAL_CHECK_FAILED_KEY` for
                the field key.
         """
        if isinstance(items, (str, dict, BaseModel)):
            all_items = [items]
        else:
            all_items = list(items)

        exceptions = []
        with ExitStack() as stack:
            # Conditionally use the batch-resource writer via the ExitStack.
            if len(all_items) > 1 and not condition:
                stack.enter_context(_internal.batching.DynBatchResource.grab().current_writer(create_if_none=True))

            for item in all_items:
                # Get the DynKey
                # If we don't get a dyn-key, check for that and raise nicer, higher-level error.
                crit = _internal.QueryCriteria.from_client__for_key(self, item, condition=condition)
                params = {'Key': crit.key_as_dict}

                # To keep things simple, I am using 'put' which replaces entire item,
                # so get all properties of item regardless if they changed or not.
                # todo: Check for primary key and raise a nicer, higher-level exception in that case.

                if not crit.condition:
                    resource = self._table_or_batch_writer()
                    resource.delete_item(**params)
                    continue

                # Add the conditional query to the dynamodb params dict...
                # (can't batch-delete things with conditions, so we do them one at a time instead).
                self._add_conditions_from_query(
                    query=crit.condition, params=params, filter_key='ConditionExpression', consistent_read=False
                )

                try:
                    self.table.delete_item(**params)
                except dynamodb.meta.client.exceptions.ConditionalCheckFailedException as e:
                    # TODO: Decide if we should log this and/or log item too???
                    log.info(
                        f"Didn't delete dynamo item ({item}) due to condition not met ({condition}), "
                        f"exception from dynamo ({e})."
                    )
                    exceptions.append(DynamoConditionError(item=item, condition=condition or {}, original=e))

        num_exceptions = len(exceptions)
        if num_exceptions > 1:
            raise ExceptionGroup("Group of dynamo conditional errors when deleting objects.", exceptions)
        elif num_exceptions == 1:
            raise exceptions[0].with_traceback(exceptions[0].__traceback__)

    def put(self, items: Iterable[M] | M, *, condition: Query | None = None):
        """
        Used to send any number of objects to Dynamo in as efficient a manner as possible.

        If possible, uses a batch-writer to put the items.
        It's WAY more efficient than doing it one at a time.

        If a condition is supplied, won't use a batch-writer as only one item can be put
        at a time with a condition.

        In the future at some point, will support transactions and at that point could put
        more items per-request at a time with condition(s).

        Args:
            items: Objects to send to dynamo.
            condition: Optional, if passed in we will send this as a `ConditionExpression`
                to dynamo.  You can pass in a Query, ie: the same structure that is used
                for `self.query()` and `self.scan()`.

                We will take the Query correctly format it for you into the `ConditionExpression`.

                If the condition is met, the item will be put into table.

                If the condition is NOT met, we will handle it for you by catch the exception,
                logging about it, setting error on response_state of object, and then returning.
                Conditions are generally used to help prevent race-conditions,
                ie: to prevent a put on purpose; so we don't raise an exception.

                Most of the time the intent is to ignore what the result is, it was either
                put into table or not;
                ie: it will eventually be handled in some way it if it was not put into table.

                If you want to know when a conditional delete fails, you can look at
                deleted objects `obj.api.response_state`.

                It's `had_error` will be `True`, there will be some info in `errors`,
                but also a field error with name `_conditional_check` and code `failed`
                you can easily check for via:

                `....response_state.has_field_error('_conditional_check', 'failed')`

                or you can use `xdynamo.const.CONDITIONAL_CHECK_FAILED_KEY` for
                the field key.

        """
        if not items:
            return

        # For now to simplify logic, convert to a list
        # (although we leave open the possibility of handling generators differently/more-efficently
        #  internally here in the future someday, if desirable).
        if isinstance(items, (dict, BaseModel)):
            items = [items]
        else:
            items = list(items)

        num_items = len(items)
        if num_items == 0:
            return

        if num_items == 1:
            self._put_item(item=items[0], condition=condition)
            return

        if condition:
            for i in items:
                self._put_item(item=i, condition=condition)
            return

        with _internal.batching.DynBatchResource.grab().current_writer(create_if_none=True):
            for i in items:
                self._put_item(item=i)

    def get_first(
            self,
            query: Query = None,
            *,
            # top: int = None,
            # fields: FieldNames = Default,
            allow_scan: bool = False,
            consistent_read: bool | DefaultType = Default,
            reverse: bool = False,
    ) -> M | None:
        """
            Calls `DyObjManager.get`, and only returns the first item, or `None` if there are no items.
            See `DyObjManager.get` reference documentation for more details on the parameters.
        """
        generator = self.get(query, allow_scan=allow_scan, consistent_read=consistent_read, reverse=reverse)
        return next(generator, None)

    def get(
            self,
            query: Query = None,
            *,
            # top: int = None,
            # fields: FieldNames = Default,
            allow_scan: bool = False,
            consistent_read: bool | DefaultType = Default,
            reverse: bool = False,
    ) -> Iterator[M]:
        """
        This is the standard/abstract interface method that all RestClient's support.

        The idea behind this method is to figure out how to route this query in the most
        efficient manner we can do it in. If you want to guarantee a specific type of query
        you can use `DyObjManager.query`, etc.

        Generally, this is the best, generally method to use since it can adapt your request
        to the most efficient way to query Dynamo. The `DynApi` will use this method generally
        when it's asked to get something (for example, when using `DynApi.get_via_id`).

        For more info on how you can query, see:

        - [Advanced Queries](#advanced-queries)
            - [Examples](#examples_1)

        Here is the general process this method goes though to determine how to query dynamo
        for a given query:

        1. If provided query is empty.
              - Will paginate though all items in table efficiently; multiple items
                will be returned per-request in parallel.
                You'll get back a generator that gives you an object at time, but behind
                the scenes we are getting a page at a time from dynamo.
        2. We will try to batch it en-mass in parallel if we can.
           We can do this if one of the following is true:
              - Query only contains 'id' key; this is because an 'id' should have full primary key;
                meaning it will contain both hash and range key (if needed).
                You can provide a list of strings or `DynKey` objects.
                   - See `DynModel.id` for more details on how to use `str` with an 'id'.
                   - See `DynKey` for more details on what a composite primary-key as a `str` is
                     like.
              - Table only has a hash-key (no range/sort key) and you provide nothing but
                hash-keys (ie: no other attributes in query)
        3. Next, we see if we can use a Dynamo query via `DyObjManager.query`.
              - This will use multiple queries if/as needed, but while minimizing the number of
                queries that are needed. Just depends on the query that is provided.
                More then one query is needed if there is more then one hash-key in the query,
                and there are other attributes you are filtering on
                (and so #2 above, batch-get, can't be used)
                Doing multiple queries is still far-faster then doing a scan.
                We return objects via the generator and only execute the second (or more)
                queries
              - In the future, this will automatically querying a Secondary Index if one is
                available.
                (Not Implemented Yet, we will add this when needed)
        4. Next, fallback to using a Global Index if one is available.
              - (Not Implemented Yet, we will do it when we need it)
        5. If that's not possible, in the future we fallback to scan operation.
            - (Not Implemented Yet; This isn't implemented currently and will raise NotImplemented.
              Scan operations can be very slow and so it's not something we really want to do
              normally... I did implement this if getting everything, but with filters/attributes
              to query on I decided to wait until we need it before implementing this currently).

        .. todo:: Implement querying via Secondary/Global indexes; Scanning with query values.
            see above for details.



        Args:
            query: Dict keys are the attribute/key names and values are what
                to filter on for that name.  Operators after double `__` work just like you
                would expect for our xyngular API's... here is an example:

                ```python
                { "some_attr_name__gte": 2 }
                ```

                In this case, we look for `some_attr_name` greater than or equal to integer `2`.

                You MUST provide at least one value for the hash key of the table for right now.
                In the future, we will support doing a table-scan to support queries without
                a hash-key. But for right now it's required.

                For more information with examples see [Advanced Queries](#advanced-queries).

            top: This is supposed to only return this mean records; currently not implemented.
            fields: This is supposed to only retrieve provided field names in returned objects;
                currently not implemented.
            allow_scan: Defaults to False, which means this will raise an exception if a scan
                is required to execute your get.  Set to True to allow a scan if needed
                (it will still do a query, batch-get, etc; if it can, it only does a scan
                if there is no other choice).

                If the query is blank or None will still do a scan regardless of what you pass
                (to return all items in the table).
            consistent_read: Defaults to Model.api.structure.dyn_consistent_read,
                which can be set via class arguments when DynModel subclass is defined.

                You can use this to override the model default. True means we use consistent
                reads, otherwise false.
            reverse: Defaults to False, which means sort order is ascending.
                Set to True to reverse the order, which will set the "ScanIndexForward"
                parameter to False in the query.
        Returns:

        """

        """ NOTES:
            - Use special dict sub-class to cache queries about the 'query' it's self.
                - This will be used internally has a helper/cacher for questions about the 'query'.
                - May also in future store a link back to a DyObjManager (for use in a future transaction class/obj/etc).
            - Accept a single dict/query or a list of dicts/queries.
            - Try to return in hash-order, but if that can't helped due to a scan, etc;
                see if we can use the internal dynamo continuation key as the 'cursor'
            - Can the generator have an prop/attr/function the outside world can use
                to get the current cursor for use in a future 'get' call?
            - Accept a 'limit'.
            
                
        """
        if reverse and (not query or allow_scan):
            log.warning(f'The `reverse` param has been set along with no query or allow_scan=True '
                        f'for ({self}). There is no way to Scan in reverse.')

        if not query:
            if reverse:
                raise DynamoError(f'The `reverse` param has been set along with no query or allow_scan=True '
                                  f'for ({self}). There is no way to Scan in reverse.')
            # If no query.... just get all items via a bulk-scan.
            return self._get_all_items(consistent_read=consistent_read)

        # todo: We want basic logic in here to decide on batch_get vs query
        #       vs [eventually] dyn_scan.
        #       We sort of implement basic logic here to determin
        #       the best way to query dynamo so we have that logic mostly centralized instead
        #       of scattered everywhere. The logic is cetnered around how we will mostly be using
        #       dynamo. The times where we have exceptions, the outside world can just directly
        #       call `dyn_scan/query` them selves and customize the dynamo call more.
        #       `get` is the thing that figured out the best to this to in the general
        #       case.
        #   Future Vision of logic flow [this is not fully implemented or final yet]:
        #       1. Only have id/hash/range keys
        #           A. Have more than one id/hash/range keys.
        #               I. We can use the batch method.
        #           B. Use query
        #       2. Have id/hash + other attributes
        #           A. More than one hash
        #               I. Use several query calls.
        #           B. Only one hash
        #               I. Use single query call.
        #       3. Have no hash key, just other attributes.
        #           A. Must use `scan`.
        #               I. This is just something we will do later, don't need `scan` at moment.
        #

        query = _internal.QueryCriteria.from_client__for_query(self, query)
        have_range_key = bool(self.sort_key_name)

        # Check to see if we only have key fields (without other filtering criteria);
        # if so we can do a batch-get, which is the fastest way to get a number of specific
        # values.  If we
        if query.contains_only_keys:
            dyn_keys = query.dyn_keys

            # If we have no range-key, they all dyn-gets support get-batch.
            # it's only the lack of range-key, or a non 'eq' operator for range-key
            # that would disqualify a specific DynKey.
            #
            # todo: Some of these keys could support batch-get, we might consider getting
            #   the ones that do via batch-get, get others via query?

            all_support_batch_get = True
            if have_range_key:
                for dyn_key in dyn_keys:
                    if not dyn_key.range_key:
                        all_support_batch_get = False
                        break

                    if dyn_key.range_operator and dyn_key.range_operator not in ('eq', 'is_in'):
                        all_support_batch_get = False
                        break

            # We have just DynKey's, so we can do a batch get (no other conditions/filters).
            # This will automatically batch a 100 at a time for us via a generator.
            # Dynamo will fetch these in parallel!
            # TODO: If we only have one key, use `get_item` instead of `batch_get_item`.
            if all_support_batch_get and dyn_keys:
                return self._batch_get(keys=dyn_keys, consistent_read=consistent_read, reverse=reverse)

        # If we have some sort of key(s) we can use (a hash key with an optional range key).
        if query.dyn_keys:
            # todo: Support `top` and `fields`.
            # todo: Support multiple hash-keys [one query per hash key].
            # todo: unless this table does not have a range key [no hash/range to tie].

            # We have a query that has the hash-key in it, that's good enough to use a query.
            return self.query(query=query, consistent_read=consistent_read, reverse=reverse)

        if allow_scan:
            if reverse:
                raise DynamoError(f'The `reverse` param has been set along with no query or allow_scan=True '
                                  f'for ({self}). There is no way to Scan in reverse.')
            return self.scan(query=query, consistent_read=consistent_read)

        # todo: Support 'scans' or always raise error? Scans are very expensive.
        # todo: Support Global + Secondary Indexes
        #  (secondary indexes are partially supported now, since they require the hash-key
        #   so we would currently do a query with a filter, and scan whole hash-key/page).
        raise NotImplementedError(
            "There are no hash-keys or id's in query, and I don't support auto routing to a scan "
            "when `allow_scan` is False, or using a global indexes at the moment. "
            "This is what you need to do without a hash-key/id. "
            "Scan operations are slow, so for being conservative to prevent accidentally doing "
            "one. For now you need to explicitly do them via self.scan or pass in True to the "
            "`allow_scan` parameter of this `get` method; unless your "
            "retrieving all records (ie: blank query). This may change in the future, but for "
            "now a scan operation requires an explicit opt-in."
            "\n"
            "TODO: Support Global Indexes - if there is a global index "
            "then we should use them if query is using the global-index hash-key and other"
            "attributes are in global-index as well."
        )

    def _batch_get(
            self,
            keys: Iterable[_internal.DynKey],
            *,
            consistent_read: bool | DefaultType = Default,
            reverse: bool = False,
            **params: DynParams
    ) -> Iterator[M]:
        """
        Will fetch keys in the largest batch it can at a time it can from Dynamo;
        Dynamo will fetch each page of values in parallel!

        We split up the keys into 100 increments at a time automatically right now
        For each unique hash key in the set of keys provided, Dynamo will parallel fetch
        the keys (if two keys have the same hash but different range key, dynamo will do
        them sequentially).

        In the future, we may attempt to fetch multiple 100 blocks of keys asynchronously.
        As the returned generator/iterable is gone through to increase the speed.
        We don't do that yet.

        Args:
            keys (Iterable[DynKey]): Keys to fetch.
                Can be of any size, a generator will be returned to minimize memory use.

                .. tip:: If you pass in a `set`, we will be slightly more efficient.
                    We need to ensure the results are uniquified, if you pass a set we can skip
                    doing it.

            consistent_read: Defaults to Model.api.structure.dyn_consistent_read,
                which can be set via class arguments when DynModel subclass is defined.

                You can use this to override the model default. True means we use consistent
                reads, otherwise false.
            reverse: Defaults to False, which means sort order is ascending.
                Set to True to reverse the order, which will set the "ScanIndexForward"
                parameter to False in the query.
            **params: An optional set of extra parameters to include in request to Dynamo,
                if so desired.

        Returns: An Iterable/Generator that will efficiently paginate though the results for you.

        """
        hash_info = self.hash_key_info
        sort_info = self.sort_key_info
        have_range = bool(sort_info)
        table_name = self.table_name

        base_params = {**params}
        consistent_read = self._resolve_consistent_reads(consistent_read)

        if not keys:
            return []

        def batch_pagination_generator(items):
            if not items:
                return xloop()

            # We want to merge our items with anything that could already be there...
            copy_params = base_params.copy()
            req_items_param = copy_params.setdefault('RequestItems', {})
            table_items = req_items_param.setdefault(table_name, {})
            if consistent_read:
                table_items['ConsistentRead'] = True
            if reverse:
                params['ScanIndexForward'] = False
            table_keys = table_items.setdefault('Keys', [])
            table_keys.extend(items)

            # batch_get_item is only available on dynamo-resource, not table-resource.
            return self._paginate_all_items_generator(
                method='batch_get_item',
                params=copy_params,
                use_table=False
            )

        # Go though all the keys and grab them 100 at a time from Dynamo.
        # Dynamo only supports a max of 100 keys at a time when doing a 'batch_get_item'.
        items_requested = []

        uniquified_keys = keys
        # We MUST uniquify them, otherwise if there is a duplicate-key in the list dynamo will produce an error.
        if not isinstance(keys, set):
            uniquified_keys = set(uniquified_keys)
        uniquified_keys = list(uniquified_keys)

        for batch in batched(uniquified_keys, 100):
            key_dics = [key.key_as_dict() for key in batch]
            for x in batch_pagination_generator(key_dics):
                yield x

    def _parse_keys_from_query(self, query: Query) -> Optional[List[DynKey]]:
        query = _ProcessedQuery.process_query(query, api=self.api)

    def _parse_id(
            self, _id: Union[str, Iterable[str]]
    ) -> List[DynKey]:
        keys = []
        if not _id:
            return keys

        api = self.api
        for current_id in xloop(_id, iterate_dicts=True):
            keys.append(DynKey(api=api, id=current_id))

        if not keys:
            return keys

        return keys

    _consistent_reads: bool | DefaultType = Default
    _cls_consistent_reads: bool

    @property
    def consistent_reads(self) -> bool | DefaultType:
        return self._consistent_reads

    @consistent_reads.setter
    def consistent_reads(self, value: bool | DefaultType):
        self._consistent_reads = value

    def _resolve_consistent_reads(self, consistent_read: bool | DefaultType = Default):
        if consistent_read is not Default:
            return consistent_read

        injected_value = dy_client_options.consistent_reads
        if injected_value is not Default:
            return injected_value

        self_value = self.consistent_reads
        if self_value is not Default:
            return self_value

        try:
            # Get setting on nearest parent class (or on own class)
            return self._cls_consistent_reads
        except AttributeError:
            pass

        return False

    def query(
            self,
            query: Query | _internal.QueryCriteria,
            *,
            consistent_read: bool | DefaultType = Default,
            reverse: bool = False,
    ) -> Iterator[M]:
        """
        Forces `DyObjManager` to use a query. If you want a way for client to automatically
        figure out the best way to execute your query, use one of these instead:

        - `DynApi.get`
        - `DyObjManager.get`

        For more info see:

        - [Advanced Queries](#advanced-queries)
            - [Examples](#examples_1)

        For a quick summary on how to provide query,
        see 'query' argument doc (just a few lines down).
        But I would highly recommend looking at [Advanced Queries](#advanced-queries) for
        more details with examples!

        Args:
            api (DynApi): BaseApi object to use, this is how we know the table name, model class,
                etc.
            query (Query): You can give a simple dict here, modeled after how the standard rest-api
                query dict's work. Dict keys are the attribute/key names and values are what
                to filter on for that name.  Operators after double `__` work just like you
                would expect for our xyngular API's... here is an example:

                ```python
                { "some_attr_name__gte": 2 }
                ```

                In this case, we look for `some_attr_name` greater than or equal to integer `2`.

                You MUST provide at least one value for the hash key of the table when using
                `query` or boto3/dynamo will raise an exception.

                This is an easy way to fill out `KeyConditionExpression` and/or `FilterExpression`.
                This method can figure out which attribute goes with which one and construct
                both expressions as needed.

                For more information with examples see [Advanced Queries](#advanced-queries).

            consistent_read: Defaults to Model.api.structure.dyn_consistent_read,
                which can be set via class arguments when DynModel subclass is defined.

                You can use this to override the model default. True means we use consistent
                reads, otherwise false.

            reverse: Defaults to False, which means sort order is ascending.
                Set to True to reverse the order, which will set the "ScanIndexForward"
                parameter to False in the query.
        Yields:
            M: (DynModel subclass instances) - The next object we got from dynamo.
                This method returns a generator that will eventually go through all the results
                from dynamo in a memory-efficient manner.
        """

        # todo: support in/lists as values....
        # todo: support `id`.

        query = _internal.QueryCriteria.from_client__for_query(client=self, query=query)

        # # TODO: Look though this list and curate it; I think 1 + 2 may already happen now;
        # #     just need to do 3 sometime.
        # 1. If we have 'id', iterate though that and get DynKey's out of them
        # 2. Look at hash/range keys and try to match them up if they are lists into DynKey's
        # 3. Considering auto-finding out if we have a list of keys and can just do batch-get

        # Query for each dyn-key we find.
        keys = query.dyn_keys
        if not keys:
            raise DynamoError(
                "query got called with a query that had no valid DynKey's in it. "
                "This means we could not find any part(s) of the primary key we could use to do "
                "a query on (a Dynamo query requires at least a hash-key). "
                ""
                "If you have no conditions and just want to "
                "simply retrieve every item in the table use `DyObjManager.get` with no parameters. "
                ""
                "If you do have conditions/filters in query you need to do a `DyObjManager.dyn_scan` "
                "and have Dynamo scan the entire table. This will allow dynamo to evaluate your "
                "conditions on every item in the table."
            )

        for dyn_key in keys:
            # TODO: May bring back support for `params = {**dynamo_pardynamo_params}` eventually; for now remove.
            params = {}
            self._add_conditions_from_query(
                query=query,
                params=params,
                dyn_key=dyn_key,
                consistent_read=consistent_read,
                reverse=reverse,
            )

            for value in self._paginate_all_items_generator(method='query', params=params):
                yield value

    def scan(
            self, query: Query | None = None, *, consistent_read: bool | DefaultType = Default
    ) -> Iterator[M]:
        """ Scans entire table (vs doing a `DyObjManager.query`, which is much more efficient).
            Looks at every item in the table, evaluating `query` to filter which ones to return.
            The scanning/filtering happens on the server-side.

            If provided query is empty, will return all items in the table.
        """
        params = {}
        self._add_conditions_from_query(
            query=query,
            params=params,
            consistent_read=consistent_read,
        )
        return self._paginate_all_items_generator(method='scan', params=params)

    def _add_conditions_from_query(
            self,
            query: Query | None,
            params: DynParams,
            dyn_key: _internal.DynKey = None,
            filter_key: str = 'FilterExpression',
            consistent_read: bool | DefaultType = Default,
            reverse: bool = False,
    ):
        if self._resolve_consistent_reads(consistent_read):
            params['ConsistentRead'] = True

        if reverse:
            params['ScanIndexForward'] = False

        if not query and not dyn_key:
            return

        if query:
            query = _internal.QueryCriteria.from_client__for_query(self, query)

        key_names: Set[str] = set()
        hash_key_info = self.hash_key_info

        if dyn_key:
            key_names.add('id')
            key_names.add(hash_key_info.dy_name)
            if (info := self.sort_key_info) and (name := info.dy_name):
                key_names.add(name)

        def add_criterion(cond_list, condition_base, name, operator, value):
            # It just so happens the basic Django filter operators are generally named the same
            # as the ones in the boto3 dynamo library. So we grab the condition/operator
            # via the same names. _ProcessedQuery will normalize the names for us.

            # exists/not_exists don't require a 'value' parameter,
            # so we need to interpret/parse query value ourselves and do the right thing.
            pre_lookup_operator = operator
            operator_needs_param = True
            if operator == 'exists':
                operator_needs_param = False
                if not bool_value(value):
                    # False value, so swap to the inverse/opposite operator.
                    operator = 'not_exists'
            elif operator == 'not_exists':
                operator_needs_param = False
                if not bool_value(value):
                    # False value, so swap to the inverse/opposite operator.
                    operator = 'exists'
            elif operator == 'range':
                operator = 'between'

            # Construct condition by allocating base, grabbing operator and assigning value.
            op_name = operator
            operator = getattr(condition_base(name), operator, None)
            condition = None
            if operator:
                # TODO: See if we should assert/validate `value` is a list here when using `between`?
                if operator_needs_param and (not isinstance(value, list) or op_name != 'between'):
                        value = [value]

                operator_params = value if operator_needs_param else []
                condition = operator(*operator_params)

            # If we found a condition operator, use it.
            # Otherwise, we construct and raise a helpful error message.
            if operator is not None:
                cond_list.append(condition)
                return

            # Get all available conditions/operators from boto3 class so we can list them
            # in the exception message.
            available = [
                f for f in dir(condition_base)
                if callable(getattr(condition_base, f)) and not f.startswith("__")
            ]
            supplemental_msg = ""
            if condition_base is conditions.Key:
                supplemental_msg = (
                    f"Attr ({name}) is part of primary key, there are reduced "
                    f"operators available for keys when using a query. "
                    f"We could route this to a 'scan' operation, that would work.... "
                    f"Right now we don't automatically route this to a 'scan' operation "
                    f"because that's much slower and you probably really are wanting "
                    f"to do a query. You can use `dyn_scan` your self directly if that's "
                    f"what you really want to do. Or we could implement an Option/Flag to "
                    f"auto-route to a scan operation when needed."
                )

            raise DynamoError(
                f"Using unknown boto3/dynamo operator ({pre_lookup_operator}), "
                f"for query on attr ({name}); "
                f"the available ones are ({available}). "
                f"{supplemental_msg}"
            )

        filters = []
        keys = []

        for (name, criterion) in query.dyn_values_map.items():
            # TODO: If there are other filters beyond what we use for the RANGE-KEY query,
            #   we should not skip them and still add them as regular attribute filters.
            #   ___
            #   Update: You can't add regular filters for the hash/range, but I could query for them
            #   and then filter the results further myself here on the Python side.
            if name in key_names:
                # This is handled later...
                continue
            for (operator, value) in criterion.items():
                add_criterion(
                    cond_list=filters,
                    condition_base=conditions.Attr,
                    name=name,
                    operator=operator,
                    value=value
                )

        # Add the dyn-key conditions if needed...
        if dyn_key:
            add_criterion(
                cond_list=keys,
                condition_base=conditions.Key,
                name=hash_key_info.dy_name,
                operator='eq',
                value=dyn_key.hash_key
            )

            range_key = dyn_key.range_key
            if range_key:
                sort_key_info = self.sort_key_info
                assert sort_key_info
                operator = dyn_key.range_operator or 'eq'
                # If we have an 'in' operator, we translate that to 'eq' for this purpose.
                # We should be called with separate values if there is more than one dyn_key,
                # and so are 'simulating' the `is_in` operator aspect.
                if operator == 'is_in':
                    operator = 'eq'
                add_criterion(
                    cond_list=keys,
                    condition_base=conditions.Key,
                    name=sort_key_info.dy_name,
                    operator=operator,
                    value=range_key
                )

        params_to_mod = (('KeyConditionExpression', keys), (filter_key, filters))
        for (param_key, exp_list) in params_to_mod:
            for key in exp_list:
                exp = params.get(param_key)
                exp = exp & key if exp is not None else key
                params[param_key] = exp

    # TODO: *** STOPPED HERE (Wed) ***

    def _table_or_batch_writer(self) -> Union[BatchWriter, Table]:
        """
        Gets either a table or a batch-writer. So you should only call methods that are
        supported by a BatchWriter on this [since it could be one].
        All BatchWriter methods are also supported by a dynamodb Table resource so you'll be safe
        as long as you limit calls to what BatchWriter supports.
        """
        batch_writer = _internal.batching.DynBatchResource.grab().current_writer()
        if batch_writer:
            return batch_writer.batch_writer(client=self)

        return self.table

    def _inject_key_value_into_dump(self, key_info: DynFieldInfo, dump: dict):
        if key_info.dy_name in dump:
            return

        dyn_fields = self.dyn_fields
        if name := key_info.name:
            dump[key_info.dy_name] = dump[dyn_fields[name].dy_name]

        values = [dump[dyn_fields[k].dy_name] for k in key_info.names]
        dump[key_info.dy_name] = "--".join(values)

    # todo: BaseModel objects are capable of letting us know if something actually changed or not.
    #       At some point take advantage of that.
    #       This would allow us to prevent putting an unchanged item into dynamo [saves cost].
    def _put_item(self, item: M, condition: Query = None):
        """
        Put item into dynamo-table. WON'T use any current batch-writer if a condition is supplied.
        If a condition is supplied, we always use the table and execute put immediately.

        Object will only be sent if there are any changes in object
        (compared to what was originally retrieved from table).

        If there is a change, entire object with all current attributes will be sent
        (a put fully replaces the item in table, it's not a PATCH).

        Condition will be sent with put if provided, it will be checked against anything
        that is currently in table before it's replaced in a transaction-safe way.

        Args:
            item: Item to put into dynamo table; if condition not supplied we check for a current
                batch-writer resource and use that if there is one.
            condition: Conditional query; query is sent to dynamodb, and it will check condition
                against any existing item in a transaction-safe way. If condition checks out
                then the put is done.  Otherwise, it won't be.
                If it is not, we add a field-error to object to indicate conditional check failed:

                `item.api.response_state.add_field_error('_conditional_check', 'failed')`
                or you can use `xyn_model_dynamo.const.CONDITIONAL_CHECK_FAILED_KEY` for
                the field key.
        """
        # # Check to see if there is anything I actually need to send.
        # if not item.api.json(only_include_changes=True, log_output=True, include_removals=True):
        #     log.info(f"Dynamo - {item} did not have any changes to send, skipping.")
        #     return

        hash_name = self.hash_key_info.name
        # if isinstance(item, dict):
        #
        # if not getattr(item, hash_name, None):
        #     raise DynamoError(f"Item {item} needs a value for partition key ({hash_name}).")
        #
        # range_name = self.sort_key_name
        # if range_name and not getattr(item, range_name, None):
        #     raise DynamoError(f"Item {item} needs a value for sort key ({range_name}).")

        dump_data = item
        if isinstance(item, BaseModel):
            dump_data = item.model_dump(mode='json')

        self._inject_key_value_into_dump(self.hash_key_info, dump_data)
        if v := self.sort_key_info:
            self._inject_key_value_into_dump(v, dump_data)

        params = {"Item": dump_data}

        resource = self._table_or_batch_writer()
        if condition:

            # Can't batch-put things with conditions, so we do them one at a time instead.
            resource = self.table

            # Add the conditional query to the dynamodb params dict...
            self._add_conditions_from_query(
                query=condition, params=params, filter_key='ConditionExpression', consistent_read=False
            )

        # Finally, tell the boto resource to put the item:
        exceptions = []
        try:
            resource.put_item(**params)
        except dynamodb.meta.client.exceptions.ConditionalCheckFailedException as e:
            log.info(
                f"Didn't put dynamo item ({item}) due to condition not met ({condition}), "
                f"exception from dynamo ({e})."
            )
            exceptions.append(DynamoConditionError(item=item, condition=condition or {}, original=e))

        num_exceptions = len(exceptions)
        if num_exceptions > 1:
            raise ExceptionGroup("Group of dynamo conditional errors when deleting objects.", exceptions)
        elif num_exceptions == 1:
            raise exceptions[0].with_traceback(exceptions[0].__traceback__)

    def _get_all_items(self, consistent_read: bool | DefaultType = Default):
        params = {}
        if self._resolve_consistent_reads(consistent_read):
            params['ConsistentRead'] = True

        return self._paginate_all_items_generator(method='scan', params=params)

    def _paginate_all_items_generator(
            self, *,
            method: str,
            params: Dict[str, Any],
            use_table=True,
    ) -> Iterator[M]:
        model_type = self.obj_type
        is_pydantic_model = issubclass(model_type, BaseModel)

        # Get table name, and also ensures table exists.
        table = self.table
        table_name = table.name
        resource = table if use_table else dynamodb

        while True:
            table_method = getattr(resource, method)
            # Execute Scan/Query on table:
            response = table_method(**params)
            last_key = response.get('LastEvaluatedKey', None)

            db_datas = response.get('Items')

            if not db_datas:
                responses: Dict[str, List] = response.get("Responses")
                if responses:
                    db_datas = responses[table_name]
                else:
                    db_datas = tuple()

            for data in db_datas:
                if is_pydantic_model:
                    model_type: BaseModel
                    yield model_type.model_validate(data)
                else:
                    # Return the raw dict-data.
                    yield data

            if last_key:
                params['ExclusiveStartKey'] = last_key
                continue

            unprocessed = response.get('UnprocessedKeys')
            if not unprocessed:
                return

            # We need to try the fetch again for the remaining items...
            # todo: Boto/AWS recommend an exponential backoff when we retry in this case...
            #   see BatchGetItem / batch_get_item
            params['RequestItems'] = unprocessed
            continue

