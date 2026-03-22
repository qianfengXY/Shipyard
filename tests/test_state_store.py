from __future__ import annotations

import pytest

from shipyard.exceptions import StateStoreError
from shipyard.models import Phase
from shipyard.repository import RepositoryPaths
from shipyard.state_store import StateStore


def test_state_store_initializes_state(tmp_path):
    store = StateStore(RepositoryPaths(tmp_path))

    state = store.load_or_init()

    assert state.phase == Phase.INIT.value
    assert (tmp_path / ".shipyard" / "state.json").exists()


def test_state_store_reads_and_writes_state(tmp_path):
    store = StateStore(RepositoryPaths(tmp_path))
    state = store.load_or_init()
    state.phase = Phase.SELECT_TASK.value

    store.save(state)
    loaded = store.load()

    assert loaded.phase == Phase.SELECT_TASK.value


def test_state_store_atomic_write_leaves_no_temp_file(tmp_path):
    store = StateStore(RepositoryPaths(tmp_path))
    state = store.load_or_init()

    store.save(state)

    assert not (tmp_path / ".shipyard" / "state.json.tmp").exists()
    assert (tmp_path / ".shipyard" / "state.json").exists()


def test_state_store_raises_on_corrupted_state_file(tmp_path):
    state_dir = tmp_path / ".shipyard"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "state.json").write_text("{invalid", encoding="utf-8")

    store = StateStore(RepositoryPaths(tmp_path))

    with pytest.raises(StateStoreError, match="corrupted"):
        store.load()
