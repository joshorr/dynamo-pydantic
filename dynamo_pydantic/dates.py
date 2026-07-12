from datetime import datetime, timezone
from typing import Annotated
from pydantic import PlainSerializer, AfterValidator, Field
import datetime as dt


# 1. Define the validator function
def ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        # If naive, assume local system time (matching standard Python/Pydantic behavior)
        # and convert it to UTC.
        return dt.astimezone(timezone.utc)
    # If already timezone-aware, shift it to UTC hours
    return dt.astimezone(timezone.utc)


# 1. Define the custom serializer function
def _serialize_utc_z(dt: datetime) -> str:
    # Convert to UTC and strip timezone info to force the clean ISO format, then append 'Z'
    return f'{dt.astimezone(timezone.utc).replace(tzinfo=None).isoformat()}Z'


# 2. Create the reusable Annotated type
UtcDateTime = Annotated[
    datetime,
    AfterValidator(ensure_utc),
    PlainSerializer(_serialize_utc_z, return_type=str, when_used="always")
]

UtcDateTimeNow = Annotated[
    datetime,
    AfterValidator(ensure_utc),
    PlainSerializer(_serialize_utc_z, return_type=str, when_used="always"),
    # This Field metadata attaches the runtime default factory directly to the type
    Field(default_factory=lambda: dt.datetime.now(dt.UTC))
]
