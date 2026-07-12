import os
from moto import mock_aws
import pytest

from dynamo_pydantic import dyn_settings

os.environ.update(dict(
    APP_ENV='unittest',
    AWS_ACCESS_KEY_ID='testing',
    AWS_SECRET_ACCESS_KEY='testing',
    AWS_SECURITY_TOKEN='testing',
    AWS_SESSION_TOKEN='testing',
))


@pytest.fixture(autouse=True)
def mock_all_aws_fixture():
    with mock_aws() as mock:
        yield mock


# We want to always create the tables lazily, during unit testing.
dyn_settings.create_tables_if_needed = True
