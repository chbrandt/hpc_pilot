"""
tests/lib/test_config.py — Unit tests for lib.config, the unified
configuration loader.

All tests use pytest's ``tmp_path`` fixture and patch the module-level
``_config_path_override`` so the real filesystem is never polluted.
"""

from unittest.mock import patch

import pytest

import lib.config as cfg


SAMPLE_CONFIG = """
site:
  hostname: test.example.org
  wstunnel:
    port: 443
    local_port: 4000
  allowed_groups:
    - hpc-wg

charts:
  default_charts:
    - kind: helm
      release_name: interlink
      chart: oci://ghcr.io/example/interlink
      version: null
      singleton: true

hpc:
  nodes:
    test-echo:
      hostname: 10.0.0.1
      ssh_port: 22
      plugin: echo
    test-slurm:
      hostname: 10.0.0.2
      ssh_port: 2222
      plugin: slurm
"""


@pytest.fixture()
def sample_config_file(tmp_path):
    cfg_file = tmp_path / "pilot_config.yaml"
    cfg_file.write_text(SAMPLE_CONFIG, encoding="utf-8")
    return cfg_file


# ---------------------------------------------------------------------------
# get_config_path / set_config_path
# ---------------------------------------------------------------------------


class TestConfigPathResolution:
    def test_default_path_used_when_no_override_or_env(self, monkeypatch):
        monkeypatch.delenv("PILOT_CONFIG_PATH", raising=False)
        with patch.object(cfg, "_config_path_override", None):
            assert cfg.get_config_path() == cfg._DEFAULT_CONFIG_PATH

    def test_env_var_overrides_default(self, monkeypatch, tmp_path):
        env_path = str(tmp_path / "from_env.yaml")
        monkeypatch.setenv("PILOT_CONFIG_PATH", env_path)
        with patch.object(cfg, "_config_path_override", None):
            assert cfg.get_config_path() == env_path

    def test_explicit_override_wins_over_env(self, monkeypatch, tmp_path):
        env_path = str(tmp_path / "from_env.yaml")
        override_path = str(tmp_path / "from_override.yaml")
        monkeypatch.setenv("PILOT_CONFIG_PATH", env_path)
        with patch.object(cfg, "_config_path_override", override_path):
            assert cfg.get_config_path() == override_path

    def test_set_config_path_sets_override(self, tmp_path):
        new_path = str(tmp_path / "custom.yaml")
        try:
            cfg.set_config_path(new_path)
            assert cfg.get_config_path() == new_path
        finally:
            cfg.set_config_path(None)


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------


class TestLoadConfig:
    def test_returns_empty_dict_when_file_missing(self, tmp_path):
        missing = str(tmp_path / "nonexistent.yaml")
        with patch.object(cfg, "_config_path_override", missing):
            assert cfg.load_config() == {}

    def test_returns_empty_dict_on_invalid_yaml(self, tmp_path):
        bad_file = tmp_path / "bad.yaml"
        bad_file.write_text(":\tthis is not valid yaml\x00", encoding="utf-8")
        with patch.object(cfg, "_config_path_override", str(bad_file)):
            assert cfg.load_config() == {}

    def test_returns_empty_dict_when_not_a_mapping(self, tmp_path):
        list_file = tmp_path / "list.yaml"
        list_file.write_text("- one\n- two\n", encoding="utf-8")
        with patch.object(cfg, "_config_path_override", str(list_file)):
            assert cfg.load_config() == {}

    def test_parses_full_sample_config(self, sample_config_file):
        with patch.object(cfg, "_config_path_override", str(sample_config_file)):
            data = cfg.load_config()
        assert "site" in data
        assert "charts" in data
        assert "hpc" in data


# ---------------------------------------------------------------------------
# get_site_config / get_charts_config / get_hpc_nodes_config
# ---------------------------------------------------------------------------


class TestSectionAccessors:
    def test_get_site_config(self, sample_config_file):
        with patch.object(cfg, "_config_path_override", str(sample_config_file)):
            site = cfg.get_site_config()
        assert site["hostname"] == "test.example.org"
        assert site["wstunnel"]["port"] == 443

    def test_get_site_config_missing_section_returns_empty(self, tmp_path):
        cfg_file = tmp_path / "no_site.yaml"
        cfg_file.write_text("charts:\n  default_charts: []\n", encoding="utf-8")
        with patch.object(cfg, "_config_path_override", str(cfg_file)):
            assert cfg.get_site_config() == {}

    def test_get_charts_config(self, sample_config_file):
        with patch.object(cfg, "_config_path_override", str(sample_config_file)):
            charts = cfg.get_charts_config()
        assert len(charts["default_charts"]) == 1
        assert charts["default_charts"][0]["release_name"] == "interlink"

    def test_get_charts_config_missing_section_returns_empty(self, tmp_path):
        cfg_file = tmp_path / "no_charts.yaml"
        cfg_file.write_text("site:\n  hostname: x\n", encoding="utf-8")
        with patch.object(cfg, "_config_path_override", str(cfg_file)):
            assert cfg.get_charts_config() == {}

    def test_get_hpc_nodes_config(self, sample_config_file):
        with patch.object(cfg, "_config_path_override", str(sample_config_file)):
            nodes = cfg.get_hpc_nodes_config()
        assert set(nodes) == {"test-echo", "test-slurm"}
        assert nodes["test-echo"]["hostname"] == "10.0.0.1"
        assert nodes["test-slurm"]["plugin"] == "slurm"

    def test_get_hpc_nodes_config_missing_section_returns_empty(self, tmp_path):
        cfg_file = tmp_path / "no_hpc.yaml"
        cfg_file.write_text("site:\n  hostname: x\n", encoding="utf-8")
        with patch.object(cfg, "_config_path_override", str(cfg_file)):
            assert cfg.get_hpc_nodes_config() == {}

    def test_get_hpc_nodes_config_when_hpc_not_a_mapping(self, tmp_path):
        cfg_file = tmp_path / "bad_hpc.yaml"
        cfg_file.write_text("hpc: not-a-mapping\n", encoding="utf-8")
        with patch.object(cfg, "_config_path_override", str(cfg_file)):
            assert cfg.get_hpc_nodes_config() == {}
