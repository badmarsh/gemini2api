"""UsageStatsStore must keep persisting after the first save (Windows rename semantics)."""

import json
from pathlib import Path
from types import SimpleNamespace

from app.core.usage_stats import UsageStatsStore


def test_snapshots_keep_persisting_when_rename_refuses_to_overwrite(tmp_path, monkeypatch):
    real_rename = Path.rename

    def windows_like_rename(self, target):
        # Path.rename() raises FileExistsError on Windows when the destination exists.
        if Path(target).exists():
            raise FileExistsError(str(target))
        return real_rename(self, target)

    monkeypatch.setattr(Path, "rename", windows_like_rename)

    path = tmp_path / "usage-stats.json"
    store = UsageStatsStore(file_path=path, retention_days=30)
    pool = SimpleNamespace(accounts=[])

    store.record_snapshot(pool, {})
    store.record_snapshot(pool, {})

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert len(saved["snapshots"]) == 2
    assert not list(tmp_path.glob("*.tmp"))
