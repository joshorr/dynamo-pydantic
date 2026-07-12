

def test__example_1():
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
