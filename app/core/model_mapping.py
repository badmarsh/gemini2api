import json
import logging
from pathlib import Path
from typing import Dict

logger = logging.getLogger(__name__)


class ModelMapping:
    def __init__(self, path: str = "data/model-mapping.json"):
        self.path = Path(path)
        self.mappings: Dict[str, str] = {}
        self._load()

    def _load(self):
        if self.path.exists():
            try:
                self.mappings = json.loads(self.path.read_text(encoding="utf-8-sig"))
                logger.info(f"Loaded {len(self.mappings)} model mappings")
            except Exception as e:
                logger.error(f"Failed to load model mappings: {e}")
                self.mappings = {}
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._save()

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.mappings, indent=2, ensure_ascii=False), encoding="utf-8")

    def get_all(self) -> Dict[str, str]:
        return dict(self.mappings)

    def set(self, alias: str, target: str):
        self.mappings[alias] = target
        self._save()

    def delete(self, alias: str) -> bool:
        if alias in self.mappings:
            del self.mappings[alias]
            self._save()
            return True
        return False

    def resolve(self, model: str) -> str:
        return self.mappings.get(model, model)
