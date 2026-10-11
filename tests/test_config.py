import pytest

from statusrelay.config import ConfigError, Filters, load_config, load_config_from, parse_config

URL = "https://ticketplus.com.tw/activity/8c47f9efe47df17d6381bcdb71309bfc"
YAML_TEXT = (
    "notifiers:\n  - type: stdout\nwatches:\n"
    f"  - name: Show Day 1\n    activity_url: {URL}\n    session:\n      date: 2026-10-31\n"
)


def raw(**overrides):
    data = {
        "notifiers": [{"type": "stdout"}],
        "watches": [{"name": "Show Day 1", "activity_url": URL, "session": {"date": "2026-10-31"}}],
    }
    data.update(overrides)
    return data


def test_defaults():
    cfg = parse_config(raw())
    assert (cfg.interval_seconds, cfg.reminder_minutes) == (60, 10)
    assert cfg.watches[0].session.date == "2026-10-31"
    assert cfg.watches[0].filters == Filters()


def test_yaml_date_is_normalised(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(YAML_TEXT, encoding="utf-8")
    assert load_config(path).watches[0].session.date == "2026-10-31"


def test_env_takes_precedence(tmp_path):
    cfg = load_config_from(tmp_path / "missing.yaml", {"STATUSRELAY_CONFIG": YAML_TEXT})
    assert cfg.watches[0].name == "Show Day 1"


def test_blank_env_falls_back_to_file(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(YAML_TEXT, encoding="utf-8")
    assert load_config_from(path, {"STATUSRELAY_CONFIG": "  "}).watches[0].name == "Show Day 1"


def test_invalid_env_yaml_does_not_echo_content():
    secret_text = "watches: [unclosed\nsecret-url-here"
    with pytest.raises(ConfigError) as info:
        load_config_from("unused", {"STATUSRELAY_CONFIG": secret_text})
    assert "STATUSRELAY_CONFIG" in str(info.value)
    assert "secret-url-here" not in str(info.value) and "unclosed" not in str(info.value)


def test_interval_floor(caplog):
    assert parse_config(raw(poll={"interval_seconds": 5})).interval_seconds == 30
    assert "raised to 30" in caplog.text


def test_filters_parsed():
    data = raw()
    data["watches"][0]["filters"] = {"area_name_contains": "搖滾", "max_price": 5880}
    f = parse_config(data).watches[0].filters
    assert f.area_name_contains == ("搖滾",)
    assert f.matches_name("搖滾A區") and not f.matches_name("看台")
    assert f.matches_price(5880) and not f.matches_price(5881)


def test_filters_min_price():
    f = Filters(min_price=3000)
    assert not f.matches_price(2940) and f.matches_price(3000)
    assert Filters().matches_name("anything")


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda d: d.update(extra=1), "config: 1 unknown key"),
        (lambda d: d.update(notifiers=[]), "notifiers must be a non-empty list"),
        (lambda d: d.update(notifiers=[{"type": "pigeon"}]), "unknown type"),
        (lambda d: d.update(watches=[]), "watches must be a non-empty list"),
        (lambda d: d["watches"][0].update(session={}), "exactly one of"),
        (lambda d: d["watches"][0].update(session={"date": "2026-10-31", "name_contains": "x"}), "exactly one of"),
        (lambda d: d["watches"].append(dict(d["watches"][0])), r"duplicate watch name at watches\[1\]"),
        (lambda d: d["watches"][0].pop("activity_url"), "activity_url is required"),
        (lambda d: d["watches"][0].update(filters={"max_price": "cheap"}), "must be an integer"),
        (lambda d: d.update(poll={"reminder_minutes": -1}), ">= 0"),
    ],
)
def test_validation_errors(mutate, message):
    data = raw()
    mutate(data)
    with pytest.raises(ConfigError, match=message) as info:
        parse_config(data)
    assert "Show Day 1" not in str(info.value) and URL not in str(info.value)


def test_not_a_mapping():
    with pytest.raises(ConfigError, match="must be a mapping"):
        parse_config(None)


def test_config_hash_changes_with_filters():
    a = parse_config(raw()).watches[0]
    data = raw()
    data["watches"][0]["filters"] = {"max_price": 1}
    b = parse_config(data).watches[0]
    assert a.config_hash().startswith("sha256:")
    assert a.config_hash() == parse_config(raw()).watches[0].config_hash()
    assert a.config_hash() != b.config_hash()


def test_missing_file(tmp_path):
    with pytest.raises(ConfigError, match="cannot read config"):
        load_config(tmp_path / "nope.yaml")


def test_unknown_key_names_are_not_echoed():
    data = raw(**{"Show Day 1": 1, "secret-key": 2})
    with pytest.raises(ConfigError, match=r"^config: 2 unknown key\(s\)$") as info:
        parse_config(data)
    assert "secret-key" not in str(info.value) and "Show Day 1" not in str(info.value)
