import json
import os

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


def test_encrypted_resave_of_same_data_keeps_file_bytes(tmp_path):
    path = tmp_path / "state.enc"
    store = EncryptedFileStore(path, generate_key())
    store.save(DATA)
    before = path.read_bytes()
    store.save(DATA)
    assert path.read_bytes() == before


def test_encrypted_save_of_changed_data_rewrites_file(tmp_path):
    path = tmp_path / "state.enc"
    store = EncryptedFileStore(path, generate_key())
    store.save(DATA)
    before = path.read_bytes()
    changed = {**DATA, "watches": {}}
    store.save(changed)
    assert path.read_bytes() != before
    assert store.load().data == changed


def test_json_resave_of_same_data_does_not_replace(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    store = JsonFileStore(path)
    store.save(DATA)
    replaced = []
    real_replace = os.replace

    def counting_replace(src, dst):
        replaced.append(dst)
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", counting_replace)
    store.save(DATA)
    assert replaced == []
    store.save({**DATA, "watches": {}})
    assert len(replaced) == 1


def test_save_overwrites_unreadable_existing_file(tmp_path):
    path = tmp_path / "state.enc"
    path.write_text("{not json", encoding="utf-8")
    EncryptedFileStore(path, generate_key()).save(DATA)
    assert path.read_bytes() != b"{not json"


@pytest.mark.parametrize("make_store", [
    lambda path: JsonFileStore(path),
    lambda path: EncryptedFileStore(path, generate_key()),
], ids=["json", "encrypted"])
def test_failed_replace_removes_tmp_and_keeps_previous_state(tmp_path, monkeypatch, make_store):
    path = tmp_path / "state.dat"
    store = make_store(path)
    store.save(DATA)
    before = path.read_bytes()

    def failing_replace(src, dst):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(os, "replace", failing_replace)
    with pytest.raises(OSError):
        store.save({**DATA, "watches": {}})
    assert list(tmp_path.glob("*.tmp")) == []
    assert path.read_bytes() == before


def test_non_str_key_raises_value_error_without_echoing_key(tmp_path):
    with pytest.raises(ValueError) as excinfo:
        EncryptedFileStore(tmp_path / "state.enc", None)
    assert "None" not in str(excinfo.value)

    secret_like = b"sentinel-key-material"
    with pytest.raises(ValueError) as excinfo:
        EncryptedFileStore(tmp_path / "state.enc", secret_like)
    assert "sentinel" not in str(excinfo.value)


def test_plaintext_json_file_loads_as_reset_without_leaking_content(tmp_path):
    path = tmp_path / "state.json"
    JsonFileStore(path).save(DATA)
    loaded = EncryptedFileStore(path, generate_key()).load()
    assert loaded.data == empty_state()
    assert loaded.reset_reason is not None
    assert "搖滾" not in loaded.reset_reason and "watches" not in loaded.reset_reason
