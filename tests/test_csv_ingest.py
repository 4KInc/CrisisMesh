"""Tests for CSV ingestion — validates parsing against the real seed data files."""

import os

import pytest

# We test the parsing logic without Firestore by mocking the state
from unittest.mock import AsyncMock, MagicMock

from src.services.csv_ingest import ingest_csv

SEED_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "seed")


def _read_seed(filename: str) -> str:
    with open(os.path.join(SEED_DIR, filename)) as f:
        return f.read()


def _mock_state() -> MagicMock:
    state = MagicMock()
    state.upsert_facility_data = AsyncMock()
    state.bulk_upsert_people = AsyncMock(side_effect=lambda people: len(people))
    return state


@pytest.mark.asyncio
async def test_ingest_facility():
    state = _mock_state()
    result = await ingest_csv(state, "facility", _read_seed("facility.csv"))
    assert result["type"] == "facility"
    assert result["records_imported"] == 1
    call_args = state.upsert_facility_data.call_args
    assert call_args[0][0] == "facilities"
    assert call_args[0][1] == "jefferson"


@pytest.mark.asyncio
async def test_ingest_zones():
    state = _mock_state()
    result = await ingest_csv(state, "zones", _read_seed("zones.csv"))
    assert result["type"] == "zones"
    assert result["records_imported"] == 8


@pytest.mark.asyncio
async def test_ingest_rooms():
    state = _mock_state()
    result = await ingest_csv(state, "rooms", _read_seed("rooms.csv"))
    assert result["type"] == "rooms"
    assert result["records_imported"] == 22


@pytest.mark.asyncio
async def test_ingest_personnel():
    state = _mock_state()
    result = await ingest_csv(state, "personnel", _read_seed("personnel.csv"))
    assert result["type"] == "personnel"
    assert result["records_imported"] == 34
    # Verify people were constructed correctly
    people = state.bulk_upsert_people.call_args[0][0]
    principal = next(p for p in people if p.id == "p001")
    assert principal.evacuation_role == "Incident Commander"
    assert principal.is_floor_warden is True
    # Verify mobility limitations parsed
    davis = next(p for p in people if p.id == "p008")
    assert davis.mobility_limitations is True
    assert "wheelchair" in davis.medical_notes


@pytest.mark.asyncio
async def test_ingest_evacuation_routes():
    state = _mock_state()
    result = await ingest_csv(state, "evacuation_routes", _read_seed("evacuation_routes.csv"))
    assert result["type"] == "evacuation_routes"
    assert result["records_imported"] == 13


@pytest.mark.asyncio
async def test_ingest_emergency_resources():
    state = _mock_state()
    result = await ingest_csv(state, "emergency_resources", _read_seed("emergency_resources.csv"))
    assert result["type"] == "emergency_resources"
    assert result["records_imported"] == 17


@pytest.mark.asyncio
async def test_ingest_assembly_points():
    state = _mock_state()
    result = await ingest_csv(state, "assembly_points", _read_seed("assembly_points.csv"))
    assert result["type"] == "assembly_points"
    assert result["records_imported"] == 3


@pytest.mark.asyncio
async def test_ingest_nearby_services():
    state = _mock_state()
    result = await ingest_csv(state, "nearby_services", _read_seed("nearby_services.csv"))
    assert result["type"] == "nearby_services"
    assert result["records_imported"] == 6


@pytest.mark.asyncio
async def test_ingest_unknown_type():
    state = _mock_state()
    result = await ingest_csv(state, "bogus_type", "id,name\n1,test")
    assert "error" in result
