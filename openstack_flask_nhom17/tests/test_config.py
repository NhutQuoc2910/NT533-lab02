import pytest

import config


def test_validate_raises_when_credentials_missing(monkeypatch):
    monkeypatch.setattr(config, "OS_APP_CRED_ID", "")
    monkeypatch.setattr(config, "OS_APP_CRED_SECRET", "")
    with pytest.raises(RuntimeError) as exc:
        config.validate()
    assert "OS_APP_CRED_ID" in str(exc.value)
    assert "OS_APP_CRED_SECRET" in str(exc.value)


def test_validate_passes_when_credentials_present():
    config.validate()


def test_group_constants():
    assert config.NET_NAME == "nhom17_net"
    assert config.SUBNET_NAME == "nhom17_subnet"
    assert config.ROUTER_NAME == "nhom17_router"
    assert config.CIDR == "192.168.17.0/24"
    assert config.ALLOCATION_POOL == {"start": "192.168.17.101", "end": "192.168.17.200"}
    assert config.HTTP_TIMEOUT > 0
