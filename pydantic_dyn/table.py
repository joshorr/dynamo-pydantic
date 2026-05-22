"""
Used to keep track of a shared connection and Dynamo table resources.

If you want to easily send/get information from Dynamo via orm.

Important Classes

- `DynamoDB`: A resource (see `xyn_resource` for more details) that keeps
    track of a shared boto3 dynamo session/connection. It also caches the fact if a table
    is created (ie: no need to check every time, just the first time we use a table).

- `DynamoTableCreator`: Interface used by `DynamoDB` to have an easy way to initiate the creation
    of a Dynamo table if needed (if it does not exist).

"""
import logging
from typing import Any, Dict, Optional, TYPE_CHECKING

from botocore.exceptions import ClientError
from mypy_boto3_dynamodb.service_resource import Table
from xcon import xcon_settings
from xinject import DependencyPerThread

from xboto.resource import dynamodb

from .errors import DynamoError

log = logging.getLogger(__name__)

__all__ = ["TableRepo"]

_auto_create_table_only_in_environments = {'unittest', 'local'}

if TYPE_CHECKING:
    from .client import DynClient


class TableRepo(DependencyPerThread, attributes_to_skip_while_copying=["_table", "_verified", "_db"]):
    """
    Resource that represents a DynamoDB connection.  This allows us to pool the dynamo
    connection among everything that uses Dynamo, so that we can reuse existing connections.
    This speeds up Dynamo by a fair amount.  Every time you open a connection with Dynamo,
    it has to figure out how to get the encryption key, but subsequent requests using the same
    connection don't have to.
    """
    _tables: Dict[str, Table]
    _verified: Dict[str, bool]

    def __init__(self):
        self._tables = {}
        self._verified = {}

    def table__for_client(self, client: DynClient, create_if_needed: bool = False) -> Table:
        if create_if_needed:
            return self._create_table_if_needed(client)
        return self.table(client.table_name)

    def table(self, full_name: str) -> Table:
        """
        Returns existing table or creates + returns one if we don't have the resource
        currently. When we create a new one, we will remember it in a weak-fashion in
        case we get asked again. Once all other references to the table resource are
        gone the python garbage-collector will clean up our reference automatically.
        If that happens, and we get asked for that table, we will create and
        return a new one [and remember if for future use].

        The reason to provide a table_creator is so you can easily Mock DynamoDB, as it
        will be created in the 'Mock' framework automatically.

        Also when devs run the code locally without deploying it, it will create the table
        for them.

        When you deploy code via serverless, it should be the thing the creates the table
        for the deployed environment [ie: testing/prod/etc].  The table should already exist
        that way when the app runs, and so it does **NOT** need table-creating AWS permissions.

        Args:
            full_name: Fully qualified name of the table to get a resource for.
            table_creator (Union[DynamoTableCreator, None]): ...
                If None: Won't verify table is created or ready.

                If DynamoTableCreator:
                    Verifies the table exists and is ready. If it does not exist we create
                    the table by 'calling' what's passed in here and wait for it to be ready.
                    If the table is in a status that indicates it can't be used at the moment
                    [example: If table is 'DELETING'], we raise an XynLibError.
        """
        table = self._tables.get(full_name)
        if table is not None:
            return table

        table = dynamodb.Table(full_name)

        # Don't verify table if we don't have a table creator, just return it.
        self._tables[full_name] = table
        self._verified.setdefault(full_name, False)
        return table

    def _create_table_if_needed(self, client: DynClient) -> Table:
        """
        This is mainly here to create table when mocking aws for unit tests. If the table
        really does not exist in reality, this also can create it.

        Time To Live Notes:

        We can't enable TimeToLive during table creation, we have to wait until after it's
        created. This is a minor issue, since the table will still function correctly,
        the queries will still filter out expired items like normal.  The only difference
        is we could get charged extra for storage we are not using. We need to still filter
        items out of our queries because deletion does not happen immediately [could take up
        to 48 hours].

        At this point, it's expected that you'll have to go into the AWS dynamo console
        to set up automatic TimeToLive item deletion for a table if you want this to create it for
        you.

        In reality, serverless framework is expected to setup the real tables for services that
        are running directly in aws; and that's where you should setup the TTL stuff for real
        tables.
        """
        name = client.table_name
        verified = self._verified.get(name, False)

        try:
            from xcon import xcon_settings
            # We only verify/create-table-if-needed in specific environments.
            if xcon_settings.environment not in _auto_create_table_only_in_environments:
                verified = True
        except ImportError:
            # If `xcon` unavailable, just assume we don't want to auto-create tables
            # todo: Put in a configurable setting that allows one to turn on/off
            #       auto-table-creation.
            #       (and some way to communicate billing mode???).
            #
            # todo: Log about why not creating tables, but log it only once.
            verified = True

        table = self._tables.get(name)
        if table is not None and verified:
            return table

        if table is None:
            table = dynamodb.Table(name)

        if verified:
            self._tables[name] = table
            return table

        try:
            log.info(f"Getting Table Status for ({name}).")
            status = table.table_status
            # It turns out, a table is still usable while "UPDATING", so only worry about
            # DELETING and CREATING.
            if status == "CREATING":
                log.warning(
                    f"Dynamo status for table ({name}) is CREATING; based on past experience "
                    f"we can still use the table [to at least read values] if the table is "
                    f"being restored from backup while it's in the CREATING status. So I am not "
                    f"going to wait for table to become ACTIVE, I'll try to use the table "
                    f"immediately."
                )
                # This is how we could wait for the table, disabling for now, see log.warning ^^^
                # table.wait_until_exists()
            elif status == "DELETING":
                raise DynamoError(f"Dynamo Table ({name}) status is 'DELETING'???")
        except ClientError:
            # This means the table has not been created yet, we create and wait for it to exist.
            log.warning(f"Dynamo table ({name}) does not exist, creating...")
            table = self._create_table(client)
            table.wait_until_exists()

        self._tables[name] = table
        self._verified[name] = True
        return table

    def _create_table(self, client: DynClient):
        if not client.hash_key_info.dy_name:
            raise DynamoError(
                f"While creating table for ({client}), found no partition-key field name; dynamo tables"
                f"need a hash-field."
            )

        hash_info = client.hash_key_info
        key_schemas = [
            # Partition Key
            {'AttributeName': hash_info.dy_name, 'KeyType': 'HASH'}
        ]
        attribute_definitions = [
            {
                'AttributeName': hash_info.dy_name,
                'AttributeType': hash_info.dy_type
            }
        ]

        # If we have a sort-key, add that in.
        if sort_info := client.sort_key_info:
            key_schemas.append({
                'AttributeName': sort_info.dy_name,
                'KeyType': 'RANGE',
            })
            attribute_definitions.append({
                'AttributeName': sort_info.dy_name,
                'AttributeType': sort_info.dy_type,
            })

        return dynamodb.create_table(
            TableName=client.table_name,
            KeySchema=key_schemas,
            AttributeDefinitions=attribute_definitions,
            BillingMode='PAY_PER_REQUEST',
            Tags=[{'Key': 'DDBTableGroupKey', 'Value': xcon_settings.service}]
        )


table_repo = TableRepo.proxy()
