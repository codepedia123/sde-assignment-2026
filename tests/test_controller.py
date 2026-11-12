import copy

from controller.service import allocation_shift, changed_dimensions
from controller.statistics import Rate, choose_winner
from controller.store import Store


def _config():
    return {
        "cohorts": [],
        "allocations": {
            "default": {
                "welcome_message": {"W1": 1.0},
                "voice": {"V1": 1.0},
                "caller_id": {"D1": 0.5, "D2": 0.5},
            }
        },
        "prompt_version": "P1",
        "schedule": {"hour_weights": {}, "lead_preferred_hour": {}},
    }


def test_changed_dimensions_and_total_variation():
    old = _config()
    new = copy.deepcopy(old)
    new["allocations"]["default"]["caller_id"] = {"D1": 0.3, "D2": 0.5, "D3": 0.2}
    assert changed_dimensions(old, new) == ["caller_id"]
    assert allocation_shift(old, new) == 0.2


def test_winner_requires_samples_uplift_and_adjusted_significance():
    winner = choose_winner([
        Rate("A", 1000, 600),
        Rate("B", 1000, 500),
        Rate("C", 1000, 510),
    ])
    assert winner and winner["winner"] == "A"
    assert choose_winner([Rate("A", 50, 40), Rate("B", 50, 20)]) is None
    assert choose_winner([Rate("A", 1000, 510), Rate("B", 1000, 500)]) is None


def test_chunk_checkpoint_and_event_are_atomic_and_idempotent(tmp_path):
    store = Store(tmp_path / "state.sqlite3")
    meta = {"kind": "events", "chunk": 2, "first_event_id": 10, "last_event_id": 10}
    event = {
        "type": "lead", "event_id": 10, "ts": "2027-01-04T08:00:00+05:30",
        "workflow_id": "wf", "campaign_id": "c1", "lead_id": "l1", "person_id": "p1",
        "features": {"lang": "hi", "city_tier": "1", "age_band": "31-45", "value_band": "mid"},
    }
    store.ingest_chunk(meta, [event], lambda _: 480, 480)
    assert store.chunk_done("events", 2)
    assert store.scalar("SELECT COUNT(*) FROM leads") == 1
    # A replay cannot duplicate either the event or checkpoint.
    store.ingest_chunk(meta, [event], lambda _: 480, 480)
    assert store.scalar("SELECT COUNT(*) FROM leads") == 1
    assert store.scalar("SELECT COUNT(*) FROM chunks") == 1
    store.close()

