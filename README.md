![PythonSupport](https://img.shields.io/static/v1?label=python&message=3.14&color=blue?style=flat-square&logo=python)
![PyPI version](https://badge.fury.io/py/dynamo_pydantic.svg?)

(documentation below is a work in progress)

- [Quick Start](#quick-start)
- [Detail Docs](#detail-docs)
- [Overview](#overview)
    * [Quick high-level overview of features](#quick-high-level-overview-of-features)
- [More Docs Soon To Come](#more-docs-soon-to-come)
- 
# Quick Start

> [!WARNING]
> This is pre-release Beta software, based on another code base and
> the needed changes to make a final release version are not yet
> completed. Everything is subject to change!


```shell
uv add dynamo-pydantic
```

or

```shell
pip install dynamo-pydantic
```

Very basic example:

```python
from dynamo_pydantic import DynamoModel, HashField, SortField

class MyModel(DynamoModel):
    some_attr: HashField[str]
    another_attr: str

obj = MyModel(some_attr='a-value', another_attr='b-value')

# Saves into a dynamo table called `myModel`.
# (See DynObjManager, accessed via DynamoModel.dyn_objs; for more docs around table naming options)
obj.dyn_save()

# Query for object
assert MyModel.dyn_objs.get_first({'some_attr': 'a-value'}).another_attr == 'b-value'

# Delete Object
obj.dyn_delete()

# Not There Anymore.
assert next(MyModel.dyn_objs.get({'some_attr': 'a-value'}), None) is None

```


# Detail Docs

**[📄 Detailed Documentation](https://joshorr.github.io/dynamo-pydantic/latest/)** | **[🐍 PyPi](https://pypi.org/project/dynamo-pydantic/)**

# Overview

This is a port of my old `xdynamo` library to use pydantic.

Allows easy use of Pydantic models with DynamoDB using a Django-like query syntax.

Right now you use a dict to communicate query filtering:

`MyModel.dyn_objs.get({'some_hash_field': 'some-value'})`

In the future, I'll have a simple QuerySet like object/class for filtering,
modeled somewhat on Django's QuerySet class.

But for now, I am starting with `.get(...)`, with the objective being you can
directly communicate url-query params into this get to grab desired objects.

The `dyn_objs` manager object will figure out the best way to query DynamoDB
based on the query attributes/values you provide. Sort of a much simplified
version of a relational-database query planner.

This allows for one to focus on querying your model and dynamo in various ways
without having to figure out exactly the best way in every possible case
when you have the potential for both simple and more complex queries.

Right now, secondary/global indexes are not supported.
They will be added in a future update.
When indexes get added, the plan is for the simplified query planner
to automatically use the index as needed.

The `get` method also by default won't do scans when given a query.
Unless you tell it via a parameter `allow_scan=True` to tell query-panner
if it's ok to automatically drop down into a scan if it's the only way 
to do a query.

In addition to the general `get` method that does query-planning,
there are also a set of explicit methods for ensuring you always do a
query, scan, etc.; as other manager methods `.query(...)`, `.scan(...)`, etc.

You can bulk-save objects via `MyModel.dyn_objs.save([obj1, obj2, ...])`,
or single-save them via `my_obj.dyn_save()` or `MyModel.dyn_objs.save(my_obj)`.

## Quick high-level overview of features

- Supports multiple hash/sort fields
  - Hash/Sort fields are defined on class, the hash/sort-key is defined on table.
  - If there are multiple hash or sort fields, they are combined into their respective hash/sort key via a deliminator.
  - The deliminator does not have to be unique/unused in field data because it won't need to be parsed later,
    it is only used to make the key properly unique to object.
  - By default, these key-fields are inherited, so you can add more hash/sort fields in subclasses,
    and these will be combined with any defined in the parent-class.
  - The individual hahs/sort fields are still stored separately into their own attribute in the dynamo table item.
  - When querying for an item, provide a value for each hash field (and optionally any sort fields).
    - They will automatically be combined and used for the final hash/sort key for table query.

- Use `dynamo_pydantic.UtcDateTime` type to force a datetime to always be and serialize into utc.
  - You can use this with hash and/or sort keys with datetime in them, so they can reliably be queried for.
  - Because we must use a string for a datetime, so if format and timezone are always the same it can be reliably sorted and queried for.
  - Use `UtcDateTimeNow` to default value to now in utc.
  - Example Field: `created_at: UtcDateTimeNow`


# More Docs Soon To Come

I'll have more, detailed documentation soon, along with examples.

I need to rewrite my old docs from the old library for the new way of doing things as I took this opportunity
to simplify and rearranged things (compared to old library).

For now, I have a basic outline above, and some refrence documentation here:

**[📄 Detailed Documentation](https://joshorr.github.io/dynamo-pydantic/latest/)**

