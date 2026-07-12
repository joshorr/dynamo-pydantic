from datetime import datetime, timezone, timedelta
import pytest
from pydantic import BaseModel, ValidationError
import time

from dynamo_pydantic.dates import UtcDateTime, UtcDateTimeNow


# Assuming your code above is in a file named `models.py`
# from models import UtcDateTime

class TestModel(BaseModel):
    timestamp: UtcDateTime


class Account(BaseModel):
    id: int
    created_at: UtcDateTimeNow


def test_serialize_utc_datetime():
    """Should seamlessly serialize a UTC datetime with a 'Z' suffix."""
    dt = datetime(2026, 7, 11, 12, 0, 0, tzinfo=timezone.utc)
    model = TestModel(timestamp=dt)

    assert model.model_dump()["timestamp"] == "2026-07-11T12:00:00Z"
    assert model.model_dump_json() == '{"timestamp":"2026-07-11T12:00:00Z"}'


def test_serialize_timezone_conversion():
    """Should shift a non-UTC timezone offset to UTC hours and append 'Z'."""
    # 12:00 PM at UTC-5 is 5:00 PM (17:00) at UTC
    eastern_tz = timezone(timedelta(hours=-5))
    dt = datetime(2026, 7, 11, 12, 0, 0, tzinfo=eastern_tz)
    model = TestModel(timestamp=dt)

    assert model.model_dump()["timestamp"] == "2026-07-11T17:00:00Z"
    assert model.model_dump_json() == '{"timestamp":"2026-07-11T17:00:00Z"}'


def test_serialize_naive_datetime():
    """Should assume system local time for naive datetimes, convert to UTC, and append 'Z'."""
    dt = datetime(2026, 7, 11, 12, 0, 0)  # No tzinfo
    model = TestModel(timestamp=dt)

    # Calculate expected UTC time dynamically based on the running system's local offset
    expected_utc = dt.astimezone(timezone.utc).replace(tzinfo=None).isoformat() + "Z"

    assert model.model_dump()["timestamp"] == expected_utc
    assert model.model_dump_json() == f'{{"timestamp":"{expected_utc}"}}'


def test_validator_forces_utc_internal_tz():
    """The internal datetime object should be converted to timezone.utc."""
    # Inputting a timezone with a -5 hour offset
    eastern_tz = timezone(timedelta(hours=-5))
    dt = datetime(2026, 7, 11, 12, 0, 0, tzinfo=eastern_tz)

    model = TestModel(timestamp=dt)

    # 12:00 at -05:00 is 17:00 at UTC
    assert model.timestamp.hour == 17
    assert model.timestamp.tzinfo == timezone.utc


def test_validator_handles_naive_datetime():
    """Naive datetimes should be localized and stored internally as UTC."""
    dt = datetime(2026, 7, 11, 12, 0, 0)
    model = TestModel(timestamp=dt)

    # Calculate what the system local time converts to in UTC
    expected_utc_dt = dt.astimezone(timezone.utc)

    assert model.timestamp == expected_utc_dt
    assert model.timestamp.tzinfo == timezone.utc


def test_end_to_end_serialization():
    """The full parse-to-serialize lifecycle should result in a clean Z string."""
    eastern_tz = timezone(timedelta(hours=-5))

    # Parse from an input dictionary/json
    model = TestModel.model_validate({"timestamp": "2026-07-11T12:00:00-05:00"})

    # Calculate what the system local time converts to in UTC
    dt = datetime(2026, 7, 11, 12, 0, 0, tzinfo=eastern_tz)
    expected_utc_dt = dt.astimezone(timezone.utc)

    # Check validated datetime object on model is in utc timezone.
    assert model.timestamp == expected_utc_dt

    assert model.model_dump()["timestamp"] == "2026-07-11T17:00:00Z"
    assert model.model_dump_json() == '{"timestamp":"2026-07-11T17:00:00Z"}'


def test_annotated_type_provides_automatic_default():
    """Omitting the field completely should trigger the embedded default_factory."""
    before = datetime.now(timezone.utc)
    account = Account(id=101)
    after = datetime.now(timezone.utc)

    # 1. Verify the field populated itself
    assert account.created_at is not None
    # 2. Verify it sits realistically within the execution window
    assert before <= account.created_at <= after
    # 3. Verify it is a true UTC timezone object
    assert account.created_at.tzinfo == timezone.utc


def test_embedded_default_factory_is_not_cached():
    """Consecutive instances must receive distinct timestamps (not a static cached time)."""
    acc_one = Account(id=1)
    time.sleep(0.001)  # Force a tiny delay so the system clock ticks
    acc_two = Account(id=2)

    assert acc_one.created_at < acc_two.created_at


def test_explicit_value_overrides_embedded_default():
    """Providing an explicit datetime must bypass the default factory entirely."""
    specific_time = datetime(2025, 12, 25, 8, 0, 0, tzinfo=timezone.utc)
    account = Account(id=102, created_at=specific_time)

    assert account.created_at == specific_time
    assert account.model_dump()["created_at"] == "2025-12-25T08:00:00Z"


def test_passing_none_fails_validation():
    """Since the type is `datetime` (not `datetime | None`), passing literal None should fail."""
    with pytest.raises(ValidationError) as exc_info:
        Account(id=103, created_at=None)

    # Validates that it expects an actual datetime input, not None
    assert "Input should be a valid datetime" in str(exc_info.value)
