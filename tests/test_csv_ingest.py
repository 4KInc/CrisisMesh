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
    assert result["records_loaded"] == 1
    call_args = state.upsert_facility_data.call_args
    assert call_args[0][0] == "facilities"
    assert call_args[0][1] == "jefferson"


@pytest.mark.asyncio
async def test_ingest_zones():
    state = _mock_state()
    result = await ingest_csv(state, "zones", _read_seed("zones.csv"))
    assert result["type"] == "zones"
    assert result["records_loaded"] == 8


@pytest.mark.asyncio
async def test_ingest_rooms():
    state = _mock_state()
    result = await ingest_csv(state, "rooms", _read_seed("rooms.csv"))
    assert result["type"] == "rooms"
    assert result["records_loaded"] == 22


@pytest.mark.asyncio
async def test_ingest_personnel():
    state = _mock_state()
    result = await ingest_csv(state, "personnel", _read_seed("personnel.csv"))
    assert result["type"] == "personnel"
    assert result["records_loaded"] == 34
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
    assert result["records_loaded"] == 13


@pytest.mark.asyncio
async def test_ingest_emergency_resources():
    state = _mock_state()
    result = await ingest_csv(state, "emergency_resources", _read_seed("emergency_resources.csv"))
    assert result["type"] == "emergency_resources"
    assert result["records_loaded"] == 17


@pytest.mark.asyncio
async def test_ingest_assembly_points():
    state = _mock_state()
    result = await ingest_csv(state, "assembly_points", _read_seed("assembly_points.csv"))
    assert result["type"] == "assembly_points"
    assert result["records_loaded"] == 3


@pytest.mark.asyncio
async def test_ingest_nearby_services():
    state = _mock_state()
    result = await ingest_csv(state, "nearby_services", _read_seed("nearby_services.csv"))
    assert result["type"] == "nearby_services"
    assert result["records_loaded"] == 6


@pytest.mark.asyncio
async def test_ingest_unknown_type():
    state = _mock_state()
    result = await ingest_csv(state, "bogus_type", "id,name\n1,test")
    assert "error" in result


@pytest.mark.asyncio
async def test_report_shape():
    """All handlers return the validation report shape."""
    state = _mock_state()
    result = await ingest_csv(state, "facility", _read_seed("facility.csv"))
    assert "records_loaded" in result
    assert "records_rejected" in result
    assert "rejected_rows" in result
    assert isinstance(result["rejected_rows"], list)


# ── Semantic validation tests (Batch F) ──


class TestSemanticEvacuationRoute:
    """Rule 1: route may not terminate in a blocked/threat zone."""

    @pytest.fixture(autouse=True)
    def load_kb(self):
        from src.core.knowledge_base import KnowledgeBase, init_knowledge_base
        KnowledgeBase.reset()
        init_knowledge_base(SEED_DIR)
        yield
        KnowledgeBase.reset()

    @pytest.mark.asyncio
    async def test_valid_route_loads(self):
        state = _mock_state()
        csv = (
            "facility_id,name,from_zone,to_exit,route_description,accessibility,blocked_by_zones\n"
            "jefferson,Test Route,east-wing-f1,Door 3,Through hallway,standard,\n"
        )
        result = await ingest_csv(state, "evacuation_routes", csv)
        assert result["records_loaded"] == 1
        assert result["records_rejected"] == 0

    @pytest.mark.asyncio
    async def test_route_unknown_blocked_zone_rejected(self):
        state = _mock_state()
        csv = (
            "facility_id,name,from_zone,to_exit,route_description,accessibility,blocked_by_zones\n"
            "jefferson,Bad Route,west-wing-f2,Door 1,Through west hall,standard,nonexistent-zone\n"
        )
        result = await ingest_csv(state, "evacuation_routes", csv)
        assert result["records_loaded"] == 0
        assert result["records_rejected"] == 1
        reason = result["rejected_rows"][0]["reason"]
        assert "unknown blocked zone" in reason
        assert "nonexistent-zone" in reason

    @pytest.mark.asyncio
    async def test_route_unknown_from_zone_rejected(self):
        state = _mock_state()
        csv = (
            "facility_id,name,from_zone,to_exit,route_description,accessibility,blocked_by_zones\n"
            "jefferson,Ghost Route,nonexistent-zone,Door 1,Through nowhere,standard,\n"
        )
        result = await ingest_csv(state, "evacuation_routes", csv)
        assert result["records_loaded"] == 0
        assert result["records_rejected"] == 1
        assert "unknown from_zone" in result["rejected_rows"][0]["reason"]


class TestSemanticEmergencyResource:
    """Rule 2: AED/resource must map to a valid floor/zone."""

    @pytest.fixture(autouse=True)
    def load_kb(self):
        from src.core.knowledge_base import KnowledgeBase, init_knowledge_base
        KnowledgeBase.reset()
        init_knowledge_base(SEED_DIR)
        yield
        KnowledgeBase.reset()

    @pytest.mark.asyncio
    async def test_valid_resource_loads(self):
        state = _mock_state()
        csv = (
            "facility_id,resource_type,location_description,floor,zone_id\n"
            "jefferson,aed,Near Room 101,1,east-wing-f1\n"
        )
        result = await ingest_csv(state, "emergency_resources", csv)
        assert result["records_loaded"] == 1
        assert result["records_rejected"] == 0

    @pytest.mark.asyncio
    async def test_resource_unknown_zone_rejected(self):
        state = _mock_state()
        csv = (
            "facility_id,resource_type,location_description,floor,zone_id\n"
            "jefferson,aed,Ghost hallway,1,nonexistent-zone\n"
        )
        result = await ingest_csv(state, "emergency_resources", csv)
        assert result["records_loaded"] == 0
        assert result["records_rejected"] == 1
        assert "unknown zone" in result["rejected_rows"][0]["reason"]

    @pytest.mark.asyncio
    async def test_resource_floor_exceeds_facility_rejected(self):
        state = _mock_state()
        csv = (
            "facility_id,resource_type,location_description,floor,zone_id\n"
            "jefferson,fire_extinguisher,Floor 5 closet,5,\n"
        )
        result = await ingest_csv(state, "emergency_resources", csv)
        assert result["records_loaded"] == 0
        assert result["records_rejected"] == 1
        assert "floor 5" in result["rejected_rows"][0]["reason"].lower()

    @pytest.mark.asyncio
    async def test_resource_unknown_facility_rejected(self):
        state = _mock_state()
        csv = (
            "facility_id,resource_type,location_description,floor,zone_id\n"
            "hogwarts,aed,Great Hall,1,\n"
        )
        result = await ingest_csv(state, "emergency_resources", csv)
        assert result["records_loaded"] == 0
        assert result["records_rejected"] == 1
        assert "unknown facility" in result["rejected_rows"][0]["reason"]


class TestSemanticRoom:
    """Rule 3: room must map to a valid facility."""

    @pytest.fixture(autouse=True)
    def load_kb(self):
        from src.core.knowledge_base import KnowledgeBase, init_knowledge_base
        KnowledgeBase.reset()
        init_knowledge_base(SEED_DIR)
        yield
        KnowledgeBase.reset()

    @pytest.mark.asyncio
    async def test_valid_room_loads(self):
        state = _mock_state()
        csv = (
            "room_id,facility_id,name,floor,zone_id,room_type,capacity,notes\n"
            "999,jefferson,Test Room,1,east-wing-f1,classroom,25,\n"
        )
        result = await ingest_csv(state, "rooms", csv)
        assert result["records_loaded"] == 1
        assert result["records_rejected"] == 0

    @pytest.mark.asyncio
    async def test_room_unknown_facility_rejected(self):
        state = _mock_state()
        csv = (
            "room_id,facility_id,name,floor,zone_id,room_type,capacity,notes\n"
            "999,hogwarts,Room of Requirement,3,tower-f3,classroom,30,\n"
        )
        result = await ingest_csv(state, "rooms", csv)
        assert result["records_loaded"] == 0
        assert result["records_rejected"] == 1
        assert "unknown facility" in result["rejected_rows"][0]["reason"]

    @pytest.mark.asyncio
    async def test_room_unknown_zone_rejected(self):
        state = _mock_state()
        csv = (
            "room_id,facility_id,name,floor,zone_id,room_type,capacity,notes\n"
            "999,jefferson,Ghost Room,1,nonexistent-zone,classroom,25,\n"
        )
        result = await ingest_csv(state, "rooms", csv)
        assert result["records_loaded"] == 0
        assert result["records_rejected"] == 1
        assert "unknown zone" in result["rejected_rows"][0]["reason"]


class TestMixedBatchRejectAndReport:
    """Mixed valid + invalid rows: valid load, invalid quarantined, report correct."""

    @pytest.fixture(autouse=True)
    def load_kb(self):
        from src.core.knowledge_base import KnowledgeBase, init_knowledge_base
        KnowledgeBase.reset()
        init_knowledge_base(SEED_DIR)
        yield
        KnowledgeBase.reset()

    @pytest.mark.asyncio
    async def test_mixed_resources_report(self):
        state = _mock_state()
        csv = (
            "facility_id,resource_type,location_description,floor,zone_id\n"
            "jefferson,aed,Near Room 101,1,east-wing-f1\n"
            "jefferson,fire_extinguisher,Floor 99 closet,99,\n"
            "jefferson,first_aid_kit,Library desk,2,library\n"
            "hogwarts,aed,Great Hall,1,\n"
        )
        result = await ingest_csv(state, "emergency_resources", csv)

        assert result["records_loaded"] == 2
        assert result["records_rejected"] == 2
        reasons = [r["reason"] for r in result["rejected_rows"]]
        assert any("floor 99" in r.lower() for r in reasons)
        assert any("hogwarts" in r for r in reasons)

    @pytest.mark.asyncio
    async def test_mixed_routes_report(self):
        state = _mock_state()
        csv = (
            "facility_id,name,from_zone,to_exit,route_description,accessibility,blocked_by_zones\n"
            "jefferson,Good Route,east-wing-f1,Door 3,Through hallway,standard,\n"
            "jefferson,Zone-Blocked,west-wing-f2,Door 1,Through west,standard,west-wing-f2\n"
            "jefferson,Ghost Route,nonexistent-zone,Door 1,Through nowhere,standard,\n"
        )
        result = await ingest_csv(state, "evacuation_routes", csv)

        assert result["records_loaded"] == 2
        assert result["records_rejected"] == 1
        reasons = [r["reason"] for r in result["rejected_rows"]]
        assert any("unknown from_zone" in r for r in reasons)
