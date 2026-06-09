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
