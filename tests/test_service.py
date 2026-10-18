import os
import stat
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "install-service.sh"
SECRET_AGENT = "market-pulse/0.4 someone@example.org"
SECRET_TOKEN = "mp-test-token-q8w2"

pytestmark = pytest.mark.skipif(not (ROOT / ".venv" / "bin" / "uvicorn").exists(), reason="needs the venv")


def install(tmp_path, *args, **env):
    """Run the installer against a scratch config dir, with systemctl replaced by a recorder."""
    log = tmp_path / "systemctl.log"
    stub = tmp_path / "systemctl"
    stub.write_text(f'#!/bin/sh\necho "$@" >> {log}\n')
    stub.chmod(0o755)
    run_env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "XDG_CONFIG_HOME": str(tmp_path / "config"),
               "SYSTEMCTL": str(stub), "MARKET_PULSE_CONFIG": str(tmp_path / "no-config.toml"),
               "MARKET_PULSE_DATA": str(tmp_path / "data"), **env}
    result = subprocess.run(["sh", str(SCRIPT), *args], env=run_env, capture_output=True, text=True, timeout=60,
                            check=False)
    calls = log.read_text().splitlines() if log.exists() else []
    return result, calls


def paths(tmp_path):
    config = tmp_path / "config"
    return config / "systemd/user/market-pulse.service", config / "market-pulse/env"


def test_installs_a_unit_without_secrets_and_a_private_env_file(tmp_path):
    result, calls = install(tmp_path)
    unit, env_file = paths(tmp_path)
    assert result.returncode == 0, result.stderr
    text = unit.read_text()
    assert f"WorkingDirectory={ROOT}" in text
    assert f"ExecStart={ROOT}/.venv/bin/python -m app.serve --host 127.0.0.1 --port 8000\n" in text
    assert f"EnvironmentFile=-{env_file}" in text and "UMask=0077" in text
    assert "Environment=MARKET_PULSE_SERVICE=1" in text
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(env_file.parent.stat().st_mode) == 0o700
    assert calls == ["--user daemon-reload"]  # installed, not started
    assert "sec_user_agent is still the example" in result.stdout


def test_values_in_the_env_file_are_used_but_never_printed_or_copied(tmp_path):
    install(tmp_path)
    unit, env_file = paths(tmp_path)
    env_file.write_text(f'SEC_USER_AGENT="{SECRET_AGENT}"\nACCESS_TOKEN={SECRET_TOKEN}\n')
    result, calls = install(tmp_path, "--enable", MP_HOST="0.0.0.0", MP_PORT="8123")
    assert result.returncode == 0, result.stderr
    output = result.stdout + result.stderr
    assert SECRET_AGENT not in output and SECRET_TOKEN not in output
    assert "still the example" not in output
    assert SECRET_TOKEN not in unit.read_text() and SECRET_AGENT not in unit.read_text()
    assert "--host 0.0.0.0 --port 8123\n" in unit.read_text()
    assert calls[-2:] == ["--user enable --now market-pulse.service", "--user restart market-pulse.service"]
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600


def test_network_access_without_a_token_is_refused(tmp_path):
    result, calls = install(tmp_path, MP_HOST="0.0.0.0")
    assert result.returncode == 1 and "Refusing MP_HOST=0.0.0.0 without an access token" in result.stderr
    assert not paths(tmp_path)[0].exists() and calls == []


def test_a_token_and_contact_saved_in_setup_count(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "settings.json").write_text(f'{{"access_token": "{SECRET_TOKEN}", "sec_user_agent": "{SECRET_AGENT}"}}')
    result, _ = install(tmp_path, MP_HOST="0.0.0.0", MP_HTTPS="1")
    assert result.returncode == 0, result.stderr
    output = result.stdout + result.stderr
    assert "still the example" not in output
    assert SECRET_TOKEN not in output and SECRET_AGENT not in output
    assert "--host 0.0.0.0 --port 8000 --https\n" in paths(tmp_path)[0].read_text()


@pytest.mark.parametrize("env", [{"MP_HOST": "0.0.0.0 --reload"}, {"MP_PORT": "80; rm"}])
def test_odd_addresses_are_rejected(tmp_path, env):
    result, _ = install(tmp_path, **env)
    assert result.returncode == 1 and not paths(tmp_path)[0].exists()


def test_uninstall_removes_the_unit_and_keeps_settings(tmp_path):
    install(tmp_path)
    result, calls = install(tmp_path, "--uninstall")
    unit, env_file = paths(tmp_path)
    assert result.returncode == 0 and not unit.exists() and env_file.exists()
    assert "--user disable --now market-pulse.service" in calls


def test_https_is_switched_on_with_mp_https(tmp_path):
    install(tmp_path)
    unit, env_file = paths(tmp_path)
    env_file.write_text(f"ACCESS_TOKEN={SECRET_TOKEN}\n")
    result, _ = install(tmp_path, MP_HOST="0.0.0.0", MP_HTTPS="1")
    assert result.returncode == 0, result.stderr
    assert "--host 0.0.0.0 --port 8000 --https\n" in unit.read_text()
    bad, _ = install(tmp_path, MP_HTTPS="yes")
    assert bad.returncode == 1 and "MP_HTTPS must be 0 or 1" in bad.stderr
