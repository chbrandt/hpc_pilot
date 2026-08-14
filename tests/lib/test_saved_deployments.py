"""
tests/lib/test_saved_deployments.py — Unit tests for lib.saved_deployments.

All tests use pytest's ``tmp_path`` fixture so the real filesystem is never
polluted.
"""

import json
import os
from unittest.mock import patch

import pytest

import lib.saved_deployments as sd


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path):
    """Redirect the module-level _DATA_DIR to a temp directory."""
    data_dir = str(tmp_path / "data")
    with patch.object(sd, "_DATA_DIR", data_dir):
        yield data_dir


# ---------------------------------------------------------------------------
# _store_path sanitisation
# ---------------------------------------------------------------------------


class TestStorePath:
    def test_basic_namespace(self):
        path = sd._store_path("user-abc123")
        assert path.endswith("user-abc123.json")

    def test_slashes_replaced(self):
        path = sd._store_path("some/namespace")
        assert "/" not in os.path.basename(path)

    def test_double_dot_replaced(self):
        path = sd._store_path("../evil")
        assert ".." not in os.path.basename(path)


# ---------------------------------------------------------------------------
# save_config / list_configs / get_config / delete_config
# ---------------------------------------------------------------------------


class TestCRUD:
    NS = "user-testns"

    def test_save_returns_entry_with_id(self):
        entry = sd.save_config(self.NS, "container", {"name": "myapp", "image": "nginx"})
        assert "id" in entry
        assert entry["kind"] == "container"
        assert entry["name"] == "myapp"

    def test_list_returns_saved_configs(self):
        sd.save_config(self.NS, "container", {"name": "app1", "image": "nginx"})
        sd.save_config(self.NS, "container", {"name": "app2", "image": "redis"})
        configs = sd.list_configs(self.NS)
        assert len(configs) == 2

    def test_list_filtered_by_kind(self):
        sd.save_config(self.NS, "container", {"name": "c1", "image": "nginx"})
        sd.save_config(self.NS, "helm", {"release_name": "myrelease", "chart": "bitnami/nginx"})
        containers = sd.list_configs(self.NS, kind="container")
        helm_releases = sd.list_configs(self.NS, kind="helm")
        assert len(containers) == 1
        assert len(helm_releases) == 1

    def test_list_sorted_newest_first(self):
        from datetime import datetime, timezone
        from unittest.mock import patch

        t1 = datetime(2024, 1, 1, 0, 0, 1, tzinfo=timezone.utc)
        t2 = datetime(2024, 1, 1, 0, 0, 2, tzinfo=timezone.utc)

        with patch("lib.saved_deployments.datetime") as mock_dt:
            mock_dt.now.return_value = t1
            sd.save_config(self.NS, "container", {"name": "first", "image": "nginx"})
            mock_dt.now.return_value = t2
            sd.save_config(self.NS, "container", {"name": "second", "image": "nginx"})

        configs = sd.list_configs(self.NS)
        # saved_at is ISO string; lexicographic sort equals chronological here
        assert configs[0]["name"] == "second"

    def test_get_config_by_id(self):
        entry = sd.save_config(self.NS, "container", {"name": "app", "image": "nginx"})
        fetched = sd.get_config(self.NS, entry["id"])
        assert fetched is not None
        assert fetched["name"] == "app"

    def test_get_config_missing_returns_none(self):
        assert sd.get_config(self.NS, "nonexistent-id") is None

    def test_delete_config_removes_entry(self):
        entry = sd.save_config(self.NS, "container", {"name": "app", "image": "nginx"})
        removed = sd.delete_config(self.NS, entry["id"])
        assert removed is True
        assert sd.get_config(self.NS, entry["id"]) is None

    def test_delete_missing_returns_false(self):
        assert sd.delete_config(self.NS, "does-not-exist") is False

    def test_multiple_namespaces_are_isolated(self):
        sd.save_config("ns-a", "container", {"name": "app", "image": "nginx"})
        assert sd.list_configs("ns-b") == []


# ---------------------------------------------------------------------------
# seed_defaults
# ---------------------------------------------------------------------------

FAKE_CHARTS_CONFIG = {
    "default_charts": [
        {
            "kind": "helm",
            "release_name": "interlink",
            "chart": "oci://ghcr.io/example/interlink",
            "version": "0.1.0",
            "singleton": True,
            "description": "InterLink VK",
            "values_yaml": (
                "namespace: __NAMESPACE__\n"
                "host: __HOSTNAME__\n"
            ),
        }
    ],
}

# Site config supplied explicitly by callers (as the api layer would)
FAKE_SITE_CONFIG = {"hostname": "test.local"}


class TestSeedDefaults:
    NS = "user-abcdef1234567890"

    @pytest.fixture(autouse=True)
    def patch_charts_config(self):
        with patch.object(sd, "_load_charts_config", return_value=FAKE_CHARTS_CONFIG):
            yield

    def test_seed_inserts_default_chart(self):
        sd.seed_defaults(self.NS, FAKE_SITE_CONFIG)
        configs = sd.list_configs(self.NS)
        assert len(configs) == 1
        assert configs[0]["release_name"] == "interlink"

    def test_seed_is_idempotent(self):
        sd.seed_defaults(self.NS, FAKE_SITE_CONFIG)
        sd.seed_defaults(self.NS, FAKE_SITE_CONFIG)
        assert len(sd.list_configs(self.NS)) == 1

    def test_placeholder_namespace_resolved(self):
        sd.seed_defaults(self.NS, FAKE_SITE_CONFIG)
        entry = sd.list_configs(self.NS)[0]
        assert self.NS in entry["values_yaml"]
        assert "__NAMESPACE__" not in entry["values_yaml"]

    def test_placeholder_hostname_resolved(self):
        sd.seed_defaults(self.NS, FAKE_SITE_CONFIG)
        entry = sd.list_configs(self.NS)[0]
        assert "test.local" in entry["values_yaml"]
        assert "__HOSTNAME__" not in entry["values_yaml"]

    def test_seed_with_no_defaults_does_nothing(self):
        with patch.object(sd, "_load_charts_config", return_value={}):
            sd.seed_defaults(self.NS, FAKE_SITE_CONFIG)
        assert sd.list_configs(self.NS) == []

    def test_seed_without_site_config_uses_fallback_hostname(self):
        """Calling seed_defaults with no site_config falls back to 'dev.local'."""
        sd.seed_defaults(self.NS)
        entry = sd.list_configs(self.NS)[0]
        assert "dev.local" in entry["values_yaml"]
        assert "__HOSTNAME__" not in entry["values_yaml"]


# ---------------------------------------------------------------------------
# load_default_charts with missing file
# ---------------------------------------------------------------------------


class TestLoadHelpers:
    def test_load_default_charts_missing_file(self):
        with patch.object(sd, "_CHARTS_CONFIG", "/nonexistent/path.yaml"):
            charts = sd.load_default_charts()
        assert charts == []

    def test_def_chart_is_singleton_true(self):
        with patch.object(sd, "_load_charts_config", return_value=FAKE_CHARTS_CONFIG):
            assert sd.def_chart_is_singleton("oci://ghcr.io/example/interlink") is True

    def test_def_chart_is_singleton_false(self):
        with patch.object(sd, "_load_charts_config", return_value=FAKE_CHARTS_CONFIG):
            assert sd.def_chart_is_singleton("bitnami/nginx") is False


