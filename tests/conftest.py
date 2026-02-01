import pytest
import os
import duckdb
from datetime import datetime
from core.clock import ReplayClock

@pytest.fixture
def temp_db(tmp_path):
    db_path = str(tmp_path / "test.duckdb")
    return db_path

@pytest.fixture
def replay_clock():
    return ReplayClock(datetime(2025, 1, 1, 9, 15))
