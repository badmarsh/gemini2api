"""The test run must never touch a developer's real data/ directory.

ConversationStore persists to $DATA_DIR/conversations (default ./data) and deletes
the oldest entries once it holds more than MAX_CONVERSATIONS. Before conftest
redirected DATA_DIR, every full test run wrote dozens of junk conversations into
the repo's real data/conversations and could evict real ones.
"""
import os
from pathlib import Path

_REPO_DATA = (Path(__file__).resolve().parents[2] / "data").resolve()


def test_conversation_store_does_not_use_the_repo_data_dir():
    from app.core import conversation_store as cs

    store_dir = cs.DATA_DIR.resolve()
    assert _REPO_DATA != store_dir
    assert _REPO_DATA not in store_dir.parents, f"conversation store is inside {_REPO_DATA}"


def test_data_dir_env_points_at_a_throwaway_directory():
    data_dir = Path(os.environ["DATA_DIR"]).resolve()
    assert data_dir.name.startswith("g2a-data-")
    assert data_dir != _REPO_DATA
