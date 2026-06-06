"""
tests/api/test_site_config.py — Unit tests for api.site_config.load_site_config.

All tests use pytest's ``tmp_path`` fixture or patch the module-level path
constant so the real filesystem is never polluted.
"""

import os
from unittest.mock import patch

import pytest
import yaml

import api.site_config as sc
from api.site_config import get_oidc_config


# ---------------------------------------------------------------------------
# load_site_config
# ---------------------------------------------------------------------------


class TestLoadSiteConfig:
    def test_returns_dict_from_valid_yaml(self, tmp_path):
        cfg_file = tmp_path / "site_config.yaml"
        cfg_file.write_text("cluster_domain: my.cluster\n", encoding="utf-8")

        with patch.object(sc, "_SITE_CONFIG_PATH", str(cfg_file)):
            result = sc.load_site_config()

        assert result == {"cluster_domain": "my.cluster"}

    def test_returns_empty_dict_when_file_missing(self, tmp_path):
        missing = str(tmp_path / "nonexistent.yaml")
        with patch.object(sc, "_SITE_CONFIG_PATH", missing):
            result = sc.load_site_config()
        assert result == {}

    def test_returns_empty_dict_on_invalid_yaml(self, tmp_path):
        cfg_file = tmp_path / "site_config.yaml"
        cfg_file.write_text(":\tthis is not valid yaml\x00", encoding="utf-8")

        with patch.object(sc, "_SITE_CONFIG_PATH", str(cfg_file)):
            result = sc.load_site_config()

        assert result == {}

    def test_returns_empty_dict_when_yaml_is_not_a_mapping(self, tmp_path):
        cfg_file = tmp_path / "site_config.yaml"
        cfg_file.write_text("- item1\n- item2\n", encoding="utf-8")

        with patch.object(sc, "_SITE_CONFIG_PATH", str(cfg_file)):
            result = sc.load_site_config()

        assert result == {}

    def test_cluster_domain_key_present(self, tmp_path):
        cfg_file = tmp_path / "site_config.yaml"
        cfg_file.write_text("cluster_domain: prod.example.com\n", encoding="utf-8")

        with patch.object(sc, "_SITE_CONFIG_PATH", str(cfg_file)):
            result = sc.load_site_config()

        assert result.get("cluster_domain") == "prod.example.com"

    def test_extra_keys_are_preserved(self, tmp_path):
        cfg_file = tmp_path / "site_config.yaml"
        cfg_file.write_text(
            "cluster_domain: dev.local\nsome_future_key: value\n",
            encoding="utf-8",
        )

        with patch.object(sc, "_SITE_CONFIG_PATH", str(cfg_file)):
            result = sc.load_site_config()

        assert result["cluster_domain"] == "dev.local"
        assert result["some_future_key"] == "value"


# ---------------------------------------------------------------------------
# get_oidc_config
# ---------------------------------------------------------------------------


_PROD_ISSUER = "https://aai.egi.eu/auth/realms/egi"
_DEV_ISSUER = "https://aai-dev.egi.eu/auth/realms/egi"


class TestGetOidcConfig:
    """Priority: env var > site_config.yaml > hardcoded default."""

    def _write_oidc_yaml(self, tmp_path, oidc_block: str) -> str:
        cfg_file = tmp_path / "site_config.yaml"
        cfg_file.write_text(f"cluster_domain: dev.local\n{oidc_block}", encoding="utf-8")
        return str(cfg_file)

    def test_returns_all_keys(self, tmp_path):
        path = self._write_oidc_yaml(tmp_path, "")
        with patch.object(sc, "_SITE_CONFIG_PATH", path):
            with patch.dict(os.environ, {}, clear=False):
                for key in ("CHECKIN_ISSUER", "CHECKIN_CLIENT_ID",
                            "CHECKIN_SCOPE", "CHECKIN_REDIRECT_URI"):
                    os.environ.pop(key, None)
                cfg = get_oidc_config()

        assert set(cfg) >= {"issuer", "client_id", "scope", "redirect_uri"}

    def test_defaults_without_oidc_section(self, tmp_path):
        path = self._write_oidc_yaml(tmp_path, "")
        with patch.object(sc, "_SITE_CONFIG_PATH", path):
            with patch.dict(os.environ, {}, clear=False):
                for key in ("CHECKIN_ISSUER", "CHECKIN_CLIENT_ID",
                            "CHECKIN_SCOPE", "CHECKIN_REDIRECT_URI"):
                    os.environ.pop(key, None)
                cfg = get_oidc_config()

        assert cfg["issuer"] == _PROD_ISSUER
        assert cfg["client_id"] == "oidc-agent"

    def test_site_config_overrides_defaults(self, tmp_path):
        oidc_yaml = (
            "oidc:\n"
            f"  issuer: \"{_DEV_ISSUER}\"\n"
            "  client_id: my-client\n"
        )
        path = self._write_oidc_yaml(tmp_path, oidc_yaml)
        with patch.object(sc, "_SITE_CONFIG_PATH", path):
            with patch.dict(os.environ, {}, clear=False):
                for key in ("CHECKIN_ISSUER", "CHECKIN_CLIENT_ID",
                            "CHECKIN_SCOPE", "CHECKIN_REDIRECT_URI"):
                    os.environ.pop(key, None)
                cfg = get_oidc_config()

        assert cfg["issuer"] == _DEV_ISSUER
        assert cfg["client_id"] == "my-client"
        # Unspecified keys fall back to defaults.
        assert cfg["scope"] == "openid offline_access profile email"

    def test_env_var_overrides_site_config(self, tmp_path):
        oidc_yaml = f"oidc:\n  issuer: \"{_DEV_ISSUER}\"\n"
        path = self._write_oidc_yaml(tmp_path, oidc_yaml)
        with patch.object(sc, "_SITE_CONFIG_PATH", path):
            with patch.dict(os.environ, {"CHECKIN_ISSUER": _PROD_ISSUER}):
                cfg = get_oidc_config()

        assert cfg["issuer"] == _PROD_ISSUER  # env var wins

    def test_env_var_overrides_default(self, tmp_path):
        path = self._write_oidc_yaml(tmp_path, "")
        with patch.object(sc, "_SITE_CONFIG_PATH", path):
            with patch.dict(os.environ, {"CHECKIN_CLIENT_ID": "custom-client"}):
                cfg = get_oidc_config()

        assert cfg["client_id"] == "custom-client"

    def test_partial_oidc_section_fills_missing_with_defaults(self, tmp_path):
        oidc_yaml = "oidc:\n  client_id: partial-client\n"
        path = self._write_oidc_yaml(tmp_path, oidc_yaml)
        with patch.object(sc, "_SITE_CONFIG_PATH", path):
            with patch.dict(os.environ, {}, clear=False):
                for key in ("CHECKIN_ISSUER", "CHECKIN_CLIENT_ID",
                            "CHECKIN_SCOPE", "CHECKIN_REDIRECT_URI"):
                    os.environ.pop(key, None)
                cfg = get_oidc_config()

        assert cfg["client_id"] == "partial-client"
        assert cfg["issuer"] == _PROD_ISSUER  # default

    def test_returns_defaults_when_file_missing(self, tmp_path):
        missing = str(tmp_path / "nonexistent.yaml")
        with patch.object(sc, "_SITE_CONFIG_PATH", missing):
            with patch.dict(os.environ, {}, clear=False):
                for key in ("CHECKIN_ISSUER", "CHECKIN_CLIENT_ID",
                            "CHECKIN_SCOPE", "CHECKIN_REDIRECT_URI"):
                    os.environ.pop(key, None)
                cfg = get_oidc_config()

        assert cfg["issuer"] == _PROD_ISSUER
        assert cfg["client_id"] == "oidc-agent"


# ---------------------------------------------------------------------------
# SITE_CONFIG env-var — custom config file path
# ---------------------------------------------------------------------------


class TestSiteConfigEnvVar:
    """SITE_CONFIG lets the operator point at an arbitrary config file."""

    def test_load_site_config_reads_from_site_config_env_var(self, tmp_path):
        """When SITE_CONFIG is set, load_site_config must read that file."""
        cfg_file = tmp_path / "custom_site.yaml"
        cfg_file.write_text("cluster_domain: custom.example.com\n", encoding="utf-8")

        with patch.dict(os.environ, {"SITE_CONFIG": str(cfg_file)}):
            # Re-evaluate the module-level path so the env var is picked up.
            with patch.object(sc, "_SITE_CONFIG_PATH", str(cfg_file)):
                result = sc.load_site_config()

        assert result["cluster_domain"] == "custom.example.com"

    def test_get_oidc_config_uses_custom_file_via_site_config_env_var(self, tmp_path):
        """OIDC settings in the custom file are returned by get_oidc_config()."""
        cfg_file = tmp_path / "my_site.yaml"
        cfg_file.write_text(
            "oidc:\n"
            f"  issuer: \"{_DEV_ISSUER}\"\n"
            "  client_id: env-var-client\n",
            encoding="utf-8",
        )

        with patch.object(sc, "_SITE_CONFIG_PATH", str(cfg_file)):
            with patch.dict(os.environ, {}, clear=False):
                for key in ("CHECKIN_ISSUER", "CHECKIN_CLIENT_ID",
                            "CHECKIN_SCOPE", "CHECKIN_REDIRECT_URI"):
                    os.environ.pop(key, None)
                cfg = sc.get_oidc_config()

        assert cfg["issuer"] == _DEV_ISSUER
        assert cfg["client_id"] == "env-var-client"

    def test_site_config_env_var_missing_file_falls_back_to_defaults(self, tmp_path):
        """If the path from SITE_CONFIG does not exist, defaults are still returned."""
        missing = str(tmp_path / "does_not_exist.yaml")

        with patch.object(sc, "_SITE_CONFIG_PATH", missing):
            with patch.dict(os.environ, {}, clear=False):
                for key in ("CHECKIN_ISSUER", "CHECKIN_CLIENT_ID",
                            "CHECKIN_SCOPE", "CHECKIN_REDIRECT_URI"):
                    os.environ.pop(key, None)
                cfg = sc.get_oidc_config()

        assert cfg["issuer"] == _PROD_ISSUER
        assert cfg["client_id"] == "oidc-agent"

    def test_oidc_env_var_still_wins_over_custom_file(self, tmp_path):
        """CHECKIN_ISSUER must override the issuer even when a custom file is used."""
        cfg_file = tmp_path / "custom.yaml"
        cfg_file.write_text(
            f"oidc:\n  issuer: \"{_DEV_ISSUER}\"\n", encoding="utf-8"
        )

        with patch.object(sc, "_SITE_CONFIG_PATH", str(cfg_file)):
            with patch.dict(os.environ, {"CHECKIN_ISSUER": _PROD_ISSUER}):
                cfg = sc.get_oidc_config()

        assert cfg["issuer"] == _PROD_ISSUER  # env var wins over custom file
