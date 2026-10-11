import json

import pytest

from statusrelay.state import EncryptedFileStore, JsonFileStore, empty_state, generate_key

DATA = {"version": 1, "watches": {"w": {"resolved": {"areas": {"a2": {"name": "搖滾B區"}, "a1": {"name": "搖滾A區"}}}}}}


def test_missing_file_is_empty_without_notice(tmp_path):
    loaded = JsonFileStore(tmp_path / "state.json").load()
    assert loaded.data == empty_state() and loaded.reset_reason is None


def test_round_trip_preserves_unicode_and_order(tmp_path):
    store = JsonFileStore(tmp_path / "nested" / "state.json")
    store.save(DATA)
    assert "搖滾B區" in (tmp_path / "nested" / "state.json").read_text(encoding="utf-8")
    loaded = store.load()
    assert loaded.data == DATA and loaded.reset_reason is None
    assert list(loaded.data["watches"]["w"]["resolved"]["areas"]) == ["a2", "a1"]
    assert not (tmp_path / "nested" / "state.json.tmp").exists()


def test_corrupt_file_resets_with_reason(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{not json", encoding="utf-8")
    loaded = JsonFileStore(path).load()
    assert loaded.data == empty_state() and "unreadable" in loaded.reset_reason


def test_version_mismatch_resets_with_reason(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"version": 99, "watches": {}}), encoding="utf-8")
    loaded = JsonFileStore(path).load()
    assert loaded.data == empty_state() and "version" in loaded.reset_reason


def test_encrypted_round_trip_hides_plaintext(tmp_path):
    key = generate_key()
    path = tmp_path / "state.enc"
    EncryptedFileStore(path, key).save(DATA)
    raw = path.read_bytes()
    assert "搖滾".encode() not in raw and b"watches" not in raw
    assert EncryptedFileStore(path, key).load().data == DATA


def test_wrong_key_is_treated_as_corrupt(tmp_path):
    path = tmp_path / "state.enc"
    EncryptedFileStore(path, generate_key()).save(DATA)
    loaded = EncryptedFileStore(path, generate_key()).load()
    assert loaded.data == empty_state() and "unreadable" in loaded.reset_reason


def test_malformed_key_rejected(tmp_path):
    with pytest.raises(ValueError):
        EncryptedFileStore(tmp_path / "state.enc", "not-a-key")


def test_generate_key_is_unique_fernet_key():
    a, b = generate_key(), generate_key()
    assert a != b and len(a) == 44
