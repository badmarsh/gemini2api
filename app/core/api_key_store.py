"""
Thread-safe API key storage pool with JSON persistence.
"""

import json
import threading
import time
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.utils.atomic_io import atomic_write_json
from app.config import mask_secret


PROVIDER_CATALOG = {
    "openai": {
        "display_name": "OpenAI",
        "default_base_url": "https://api.openai.com/v1",
        "models": ["gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-3.5-turbo", "o1", "o1-mini", "o1-pro"]
    },
    "anthropic": {
        "display_name": "Anthropic",
        "default_base_url": "https://api.anthropic.com",
        "models": ["claude-3-5-sonnet-20241022", "claude-3-5-haiku-20241022", "claude-3-opus-20240229"]
    },
    "gemini": {
        "display_name": "Google Gemini",
        "default_base_url": "https://generativelanguage.googleapis.com/v1beta",
        "models": ["gemini-2.5-flash-preview-05-20", "gemini-2.5-pro-preview-05-06", "gemini-2.0-flash"]
    },
    "openrouter": {
        "display_name": "OpenRouter",
        "default_base_url": "https://openrouter.ai/api/v1",
        "models": []
    },
    "custom": {
        "display_name": "自定义",
        "default_base_url": "",
        "models": []
    }
}


@dataclass
class ApiKeyEntry:
    id: str
    provider: str
    model: str
    api_key: str
    base_url: str
    label: Optional[str]
    status: str
    added_at: str
    last_used_at: Optional[str]
    reasoning_effort: Optional[str] = None


class ApiKeyPool:
    def __init__(self, file_path: str = "data/api-keys.json"):
        self.file_path = Path(file_path)
        self.lock = threading.Lock()
        self.entries: dict[str, ApiKeyEntry] = {}
        # entry_id -> 冷却截止的 time.monotonic() 时间戳；仅内存，重启即清空。
        self._cooldowns: dict[str, float] = {}
        self._load()

    def add(
        self,
        provider: str,
        model: str,
        api_key: str,
        base_url: str,
        label: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
    ) -> ApiKeyEntry:
        with self.lock:
            entry_id = uuid.uuid4().hex[:12]
            entry = ApiKeyEntry(
                id=entry_id,
                provider=provider,
                model=model,
                api_key=api_key,
                base_url=base_url,
                label=label,
                status="active",
                added_at=datetime.utcnow().isoformat(),
                last_used_at=None,
                reasoning_effort=reasoning_effort,
            )
            self.entries[entry_id] = entry
            self._save()
            return entry

    def remove(self, id: str) -> bool:
        with self.lock:
            if id in self.entries:
                del self.entries[id]
                self._save()
                return True
            return False

    def list_all(self, mask: bool = True) -> list[dict]:
        with self.lock:
            result = []
            for entry in self.entries.values():
                entry_dict = asdict(entry)
                if mask and entry_dict["api_key"]:
                    entry_dict["api_key"] = mask_secret(entry_dict["api_key"])
                result.append(entry_dict)
            return result

    def get(self, id: str) -> Optional[ApiKeyEntry]:
        with self.lock:
            return self.entries.get(id)

    def get_key_for_model(self, model: str) -> Optional[ApiKeyEntry]:
        with self.lock:
            for entry in self.entries.values():
                if entry.model == model and entry.status == "active":
                    return entry
            return None

    def mark_unhealthy(self, id: str, seconds: float) -> None:
        """把某第三方条目标记为冷却 `seconds` 秒（自现在起）。seconds<=0 为空操作。"""
        if seconds <= 0:
            return
        with self.lock:
            self._cooldowns[id] = time.monotonic() + seconds

    def get_entries_for_model(self, model: str) -> list[ApiKeyEntry]:
        """返回该 model 的全部 active 候选：未冷却在前、冷却中在后，组内保持插入序。
        冷却是优先级降级而非硬屏蔽——全部冷却时仍返回全部，绝不饿死。"""
        with self.lock:
            now = time.monotonic()
            healthy: list[ApiKeyEntry] = []
            cooling: list[ApiKeyEntry] = []
            for entry in self.entries.values():
                if entry.model != model or entry.status != "active":
                    continue
                until = self._cooldowns.get(entry.id)
                if until is not None and until > now:
                    cooling.append(entry)
                else:
                    healthy.append(entry)
            return healthy + cooling

    def delete(self, id: str) -> bool:
        return self.remove(id)

    def list_keys(self, masked: bool = True) -> list[dict]:
        return self.list_all(mask=masked)

    def update_status(self, id: str, status: str) -> bool:
        with self.lock:
            if id in self.entries:
                self.entries[id].status = status
                self._save()
                return True
            return False

    def update_label(self, id: str, label: str) -> bool:
        with self.lock:
            if id in self.entries:
                self.entries[id].label = label
                self._save()
                return True
            return False

    def update_reasoning_effort(self, id: str, reasoning_effort: Optional[str]) -> bool:
        with self.lock:
            if id in self.entries:
                self.entries[id].reasoning_effort = None if reasoning_effort in (None, "") else reasoning_effort
                self._save()
                return True
            return False

    def update_last_used(self, id: str) -> bool:
        with self.lock:
            if id in self.entries:
                self.entries[id].last_used_at = datetime.utcnow().isoformat()
                self._save()
                return True
            return False

    def _save(self):
        data = {
            entry_id: asdict(entry)
            for entry_id, entry in self.entries.items()
        }
        # 原子写，避免写入中途崩溃把 api-keys.json 截断损坏（VULN-010）
        atomic_write_json(self.file_path, data, indent=2, ensure_ascii=False)

    def _load(self):
        if not self.file_path.exists():
            return
        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            return  # 文件损坏/不可读：保持空，不抛
        if not isinstance(data, dict):
            return
        loaded: dict[str, ApiKeyEntry] = {}
        for entry_id, entry_data in data.items():
            try:
                loaded[entry_id] = ApiKeyEntry(**entry_data)
            except (TypeError, KeyError):
                # 坏记录单条跳过，保留其余有效记录，绝不清库（VULN-010 读容错）
                continue
        self.entries = loaded
