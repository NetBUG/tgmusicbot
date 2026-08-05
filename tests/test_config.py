import pytest

from tgmusicbot.config import Config
from tgmusicbot.errors import ConfigError

VALID = {
    "TG_TOKEN": "123456:AAbbCC",
    "OUTPUT_FOLDER": "/tmp/library",
    "TG_ALLOWED_USERS": "42, 43;44",
}


def test_from_env_parses_everything():
    config = Config.from_env(VALID | {"WORKERS": "4", "MAX_UPLOAD_MB": "50"})
    assert config.allowed_users == frozenset({42, 43, 44})
    assert str(config.library_root) == "/tmp/library"
    assert config.workers == 4
    assert config.max_upload_bytes == 50 * 1024 * 1024
    assert config.log_path is None


def test_config_is_frozen():
    config = Config.from_env(VALID)
    with pytest.raises(Exception):
        config.workers = 9


@pytest.mark.parametrize(
    "override,variable",
    [
        ({"TG_TOKEN": ""}, "TG_TOKEN"),
        ({"TG_TOKEN": "%YOUR_TG_TOKEN_HERE%"}, "TG_TOKEN"),
        ({"OUTPUT_FOLDER": ""}, "OUTPUT_FOLDER"),
        ({"TG_ALLOWED_USERS": ""}, "TG_ALLOWED_USERS"),
        ({"TG_ALLOWED_USERS": "everyone"}, "TG_ALLOWED_USERS"),
        ({"WORKERS": "0"}, "WORKERS"),
        ({"WORKERS": "many"}, "WORKERS"),
        ({"PROGRESS_INTERVAL_S": "-1"}, "PROGRESS_INTERVAL_S"),
    ],
)
def test_bad_configuration_names_the_variable(override, variable):
    with pytest.raises(ConfigError) as raised:
        Config.from_env(VALID | override)
    assert raised.value.variable == variable


def test_allowlist_is_mandatory():
    """Without it, anyone who finds the bot can write to the collection."""
    env = dict(VALID)
    del env["TG_ALLOWED_USERS"]
    with pytest.raises(ConfigError):
        Config.from_env(env)
