"""Behavioural regression tests: non-ASCII data must survive text I/O on a Windows ANSI locale.

The ``cp1252_locale`` fixture (tests/unit/conftest.py) makes every text-mode open() without an
explicit ``encoding=`` behave like native Windows (cp1252). Every test here fails without the fix
and passes once all file I/O names its encoding.
"""

import json
import time
from pathlib import Path

import pytest

import app.core.conversation_store as cs
from app.config import settings
from app.core.account_pool import Account, AccountPool

# U+010D is not in cp1252; the CJK characters are not either.
LABEL = "\u00da\u010det \u010d \u65e5\u672c\u8a9e"
TEXT = "Zdrav\u00edm \u010d \u4f60\u597d \u65e5\u672c\u8a9e"


def _write_accounts(path: Path, label: str, *, bom: bool = False) -> None:
    data = {"accounts": [{"id": "account-0", "psid": "psid-value", "psidts": "psidts-value", "label": label}]}
    raw = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    path.write_bytes((b"\xef\xbb\xbf" if bom else b"") + raw)


# --------------------------------------------------------------------------- AccountPool

def test_accounts_file_with_non_cp1252_label_is_loaded(cp1252_locale, tmp_path):
    path = tmp_path / "accounts.json"
    _write_accounts(path, LABEL)

    pool = AccountPool()
    pool._load_from_file(path)

    assert [a.label for a in pool._accounts] == [LABEL]
    assert pool._accounts[0].psid == "psid-value"


def test_accounts_file_with_utf8_bom_is_loaded(cp1252_locale, tmp_path):
    path = tmp_path / "accounts.json"
    _write_accounts(path, LABEL, bom=True)

    pool = AccountPool()
    pool._load_from_file(path)

    assert [a.label for a in pool._accounts] == [LABEL]


def test_accounts_save_then_load_round_trip(cp1252_locale, tmp_path, monkeypatch):
    path = tmp_path / "accounts.json"
    monkeypatch.setattr(settings, "accounts_file", str(path))

    pool = AccountPool()
    pool._accounts = [Account(id="account-0", psid="psid-value", psidts="psidts-value", label=LABEL)]
    pool._save_to_file()

    reloaded = AccountPool()
    reloaded._load_from_file(path)
    assert [a.label for a in reloaded._accounts] == [LABEL]


def test_corrupt_accounts_file_is_backed_up_before_starting_empty(tmp_path):
    path = tmp_path / "accounts.json"
    broken = b'{"accounts": [{"id": "account-0", "psid": "abc'  # truncated JSON
    path.write_bytes(broken)

    pool = AccountPool()
    pool._load_from_file(path)

    assert pool._accounts == []
    backups = list(tmp_path.glob("accounts.json.corrupt-*"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == broken

    # A later save overwrites accounts.json, but the backup keeps the original bytes.
    path.write_text("{}", encoding="utf-8")
    assert backups[0].read_bytes() == broken


# --------------------------------------------------------------------------- ConversationStore

@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(cs, "DATA_DIR", tmp_path)
    return cs.ConversationStore()


def _conv(text: str, conv_id: str = "conv-1") -> cs.Conversation:
    conv = cs.Conversation(conv_id)
    conv.model = "gemini-pro"
    conv.add_message("user", text)
    return conv


@pytest.mark.asyncio
async def test_conversation_with_non_ascii_text_round_trips(cp1252_locale, store, tmp_path):
    await store.update(_conv(TEXT))

    on_disk = json.loads((tmp_path / "conv-1.json").read_bytes().decode("utf-8"))
    assert on_disk["messages"][0]["content"] == TEXT

    reloaded = cs.ConversationStore()  # fresh instance reads the files back
    loaded = await reloaded.get("conv-1")
    assert loaded is not None
    assert loaded.messages[0]["content"] == TEXT


@pytest.mark.asyncio
async def test_update_survives_persistence_failure_and_keeps_memory_state(store, tmp_path, monkeypatch, caplog):
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    monkeypatch.setattr(cs, "DATA_DIR", blocker)  # every write now fails with OSError

    conv = _conv(TEXT)
    with caplog.at_level("WARNING", logger=cs.__name__):
        await store.update(conv)  # must not raise: persistence is best-effort

    assert await store.get("conv-1") is conv
    assert any(r.levelname == "WARNING" for r in caplog.records)


@pytest.mark.asyncio
async def test_unencodable_text_does_not_truncate_the_existing_file(store, tmp_path):
    await store.update(_conv("fine"))
    good = (tmp_path / "conv-1.json").read_bytes()

    bad = _conv("lone surrogate \ud800 cannot be encoded as UTF-8")
    await store.update(bad)  # must not raise

    assert (tmp_path / "conv-1.json").read_bytes() == good
    assert not list(tmp_path.glob("*.tmp"))
    assert await store.get("conv-1") is bad  # in-memory state is still updated


def test_one_bad_conversation_file_does_not_abort_loading_the_rest(tmp_path, monkeypatch):
    monkeypatch.setattr(cs, "DATA_DIR", tmp_path)
    now = time.time()
    ids = ["good-a", "bad-json", "bad-bytes", "good-b"]
    (tmp_path / "index.json").write_text(
        json.dumps([{"id": i, "updated_at": now} for i in ids]), encoding="utf-8"
    )
    for cid, text in (("good-a", "first"), ("good-b", TEXT)):
        payload = _conv(text, cid).to_dict()
        (tmp_path / f"{cid}.json").write_bytes(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    (tmp_path / "bad-json.json").write_bytes(b"{not json")
    (tmp_path / "bad-bytes.json").write_bytes(b"\xff\xfe\x00garbage")

    loaded = cs.ConversationStore()

    assert set(loaded._conversations) == {"good-a", "good-b"}
    assert loaded._conversations["good-b"].messages[0]["content"] == TEXT


# --------------------------------------------------------------------------- other persisted files

def test_model_mapping_round_trips_non_ascii(cp1252_locale, tmp_path):
    from app.core.model_mapping import ModelMapping

    path = tmp_path / "model-mapping.json"
    mm = ModelMapping(path=str(path))
    mm.set("alias-\u010d", "target-\u65e5\u672c")

    assert ModelMapping(path=str(path)).get_all() == {"alias-\u010d": "target-\u65e5\u672c"}


def test_persist_api_key_reads_env_with_non_ascii_comment(cp1252_locale, tmp_path, monkeypatch):
    import app.config as config

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("API_KEY", raising=False)
    monkeypatch.setattr(config.settings, "api_key", "sk-generated-for-test")
    env = tmp_path / ".env"
    env.write_bytes("# \u00da\u010det \u010d\nAPI_KEY=\nPORT=8000\n".encode("utf-8"))

    config._persist_api_key()

    text = env.read_bytes().decode("utf-8")
    assert "API_KEY=sk-generated-for-test" in text
    assert "# \u00da\u010det \u010d" in text


def test_settings_panel_env_update_keeps_non_ascii_comment(cp1252_locale, tmp_path, monkeypatch):
    import app.routers.settings as panel

    env = tmp_path / ".env"
    env.write_bytes("# \u00da\u010det \u010d\nLOG_LEVEL=info\n".encode("utf-8"))
    monkeypatch.setattr(panel, "ENV_PATH", env)

    panel._update_env_file({"log_level": "debug"})

    text = env.read_bytes().decode("utf-8")
    assert "# \u00da\u010det \u010d" in text
    assert "log_level=debug" in text.lower() or "LOG_LEVEL=debug" in text
