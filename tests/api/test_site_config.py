"""
tests/api/test_site_config.py — Unit tests for api.site_config.load_site_config.

api.site_config now delegates to lib.config.get_site_config(); tests patch
lib.config's module-level path constant so the real filesystem is never
polluted.
"""

from unittest.mock import patch

import pytest
import yaml

import api.site_config as sc
import lib.config as cfg


# ---------------------------------------------------------------------------
# load_site_config
# ---------------------------------------------------------------------------


class TestLoadSiteConfig:
    def test_returns_dict_from_valid_yaml(self, tmp_path):
        cfg_file = tmp_path / "pilot_config.yaml"
        cfg_file.write_text("site:\n  hostname: my.cluster\n", encoding="utf-8")

        with patch.object(cfg, "_config_path_override", str(cfg_file)):
            result = sc.load_site_config()

        assert result == {"hostname": "my.cluster"}

    def test_returns_empty_dict_when_file_missing(self, tmp_path):
        missing = str(tmp_path / "nonexistent.yaml")
        with patch.object(cfg, "_config_path_override", missing):
            result = sc.load_site_config()
        assert result == {}

    def test_returns_empty_dict_on_invalid_yaml(self, tmp_path):
        cfg_file = tmp_path / "pilot_config.yaml"
        cfg_file.write_text(":\tthis is not valid yaml\x00", encoding="utf-8")

        with patch.object(cfg, "_config_path_override", str(cfg_file)):
            result = sc.load_site_config()

        assert result == {}

    def test_returns_empty_dict_when_yaml_is_not_a_mapping(self, tmp_path):
        cfg_file = tmp_path / "pilot_config.yaml"
        cfg_file.write_text("- item1\n- item2\n", encoding="utf-8")

        with patch.object(cfg, "_config_path_override", str(cfg_file)):
            result = sc.load_site_config()

        assert result == {}

    def test_returns_empty_dict_when_site_section_missing(self, tmp_path):
        cfg_file = tmp_path / "pilot_config.yaml"
        cfg_file.write_text("charts:\n  default_charts: []\n", encoding="utf-8")

        with patch.object(cfg, "_config_path_override", str(cfg_file)):
            result = sc.load_site_config()

        assert result == {}

    def test_hostname_key_present(self, tmp_path):
        cfg_file = tmp_path / "pilot_config.yaml"
        cfg_file.write_text("site:\n  hostname: prod.example.com\n", encoding="utf-8")

        with patch.object(cfg, "_config_path_override", str(cfg_file)):
            result = sc.load_site_config()

        assert result.get("hostname") == "prod.example.com"

    def test_extra_keys_are_preserved(self, tmp_path):
        cfg_file = tmp_path / "pilot_config.yaml"
        cfg_file.write_text(
            "site:\n  hostname: dev.local\n  some_future_key: value\n",
            encoding="utf-8",
        )

        with patch.object(cfg, "_config_path_override", str(cfg_file)):
            result = sc.load_site_config()

        assert result["hostname"] == "dev.local"
        assert result["some_future_key"] == "value"

