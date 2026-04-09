---
title: Getting Started
---
## Getting Started

???+ warning "Alpha Software!"
    This is pre-release Alpha software, based on another code base and
    the needed changes to make a final release version are not yet
    completed. Everything is subject to change!


```shell
poetry install xmodel
```

or

```shell
pip install xmodel
```

Very basic example:

```python
from xmodel import JsonModel

class MyModel(JsonModel):
    some_attr: str

json_dict_input = {'some_attr': 'a-value'}    

obj = MyModel(json_dict_input)
assert obj.some_attr == 'a-value'

json_dict = obj.api.json()
assert json_dict == json_dict_input
```



Also, an abstract RemoteModel interface used in xmodel-rest and xdynamo along with
some common code.


- [Install in Python project](#install-in-python-project)
  - [Poetry](#poetry)
    - [Gemfury](#gemfury)
    - [Git URL](#git-url)
- [Development](#development)
- [How To Use](#how-to-use)

## Install in Python project

This can be installed from git URLs or from gemfury in the `pyproject.toml` file.

You see the documentation locally by doing this in the project root folder with `repoman` installed 
locally:

```bash
repo install
repo docs --live
```

### Poetry

You can install this using gemfury

## Development

How to develop this library. 

__*Requires `repoman` to be installed locally*__

* Install dependencies for development with `repo install`
* Run tests with `repo test`
* See documentation with `repo docs --live` (see `repo docs --help` on how to configure locally)


## How To Use

.. todo::  This is incomplete, need to put more in here, especially about the remote model.
    Also, the docs for some classes in this library are a bit out of date (very alpha software).

The basic use-case of this library is with `JsonModel`, which allows you to map an object
to/from a json dict with various options to control how it maps, converting values to
pyhton native types.

It can also keep track of changes you make to the model object compared to the Json values it
has gotten.

Here is a basic overview of working with a JsonModel subclass, as a series of code-examples
that build on-top of each-other with a number of code comments to explain what's happening:

```python
from xmodel import JsonModel
from decimal import Decimal

class MyModel(JsonModel):
    first_name: str
    volume: int
    price: Decimal

json = {
    'first_name': 'Darius',
    'volume': 20,
    'price': '10.4'
}

# First positional argument for a JsonModel is an optional Json Dict.
# It turns around and simply calls `self.api.update_from_json` for you
# with the dict you pass in.
model = MyModel(json)

# After updating model from json, the values are all set.
assert model.first_name == 'Darius'
assert model.volume == 20
assert model.price == Decimal('10.4')

# JsonModel's keep track of the orginal JSON values, it compares the values
# to what the values are in it's attributes and can return what actaully changed.
# If nothing has changed, it returns a None.
#
# Returns None because no changes vs the inital JSON were made.
assert model.api.json(only_include_changes=True) is None

# Converts to Decimal(30) for you when you set it on object.
model.price = 30
assert model.price == Decimal(30)

# Now when you ask it for changes, it includes the change:
assert model.api.json(only_include_changes=True) == {'price': '30'}

# Update values with a partial JSON object, it only updates what's inside json dict,
# leaves other values alone.
model.api.update_from_json({'first_name': 'new-name'})

assert model.first_name == 'new-name'

# We still have that price change because we have not updated it with the new json-value yet.
assert model.api.json(only_include_changes=True) is None == {'price': '30'}

# It's pretty common when you send values to an API, the server will return what the current
# values are for the object. You can just pipe that into `update_from_json` to update the
# object from it's JSON values.
#
# This also records the orginal json-value, per-key, to compare against for
# the `only_include_changes` option.
model.api.update_from_json({'first_name': 'new-name'})


# Returns None because no changes vs what it has recoded as the original JSON values:
assert model.api.json(only_include_changes=True) is None

```

## Sub-Models

You can type-hint to another JsonModel, if you do it will embed the object when you ask
for the objects JSON.

It can also update it's self and the embedded-JsonModel object for you when you update it
from a Json dict.

```python
from xmodel import JsonModel
from decimal import Decimal

class Address(JsonModel):
    street: str
    city: str

class Account(JsonModel):
    first_name: str
    address: Address

json = {
    'first_name': 'customer-name',
    'address': {'street': '123 Frost Ave', city: 'Lehi'}
}
    
account = Account(json)

assert account.address.street == '123 Frost Ave'

# If you ask for json, should return same values it was given
assert account.api.json() == json

# No changes vs orginal values, so returns None
assert account.api.json(only_include_changes=True) is None

# We make a change to sub-object
account.address.street = 'changed-street'

# Now when we ask for changes, it includes on the sub-object change.
assert account.api.json(only_include_changes=True) == {'address': {'street': 'changed-street'}}
```


## Remote Abstract Interface

I'll briefly mention the `xmodel.remote` module and the `RemoteModel`

It contains a set of common code and an abstract interface that can be used to implment
remote retreival and sending of JsonModel-type objects.

We have two current concreate implementations:

- xmodel-rest: Useful for rest-api's
- xdynamo: Useful for model objects in dynamo tables.

The abstract interface includes a basic way to ask for an object by id,
and a way to send object updates back to API.

This makes it so JsonModel knows how lazily retrieve `RemoteModel`'s if it encounters one
as a sub-object on it's self.
It simply calls the correct abstract method, which should be a concreate implementation
of the RemoteModel and related classes, abstract interfaces.

I won't say much more about it here.
See relevant classes/modules doc-comments for more details.
You can also look at xmodel-rest and xdynamo for concreate,
real-world implementations.

