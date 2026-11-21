"""Regression tests for local agent engine / subscription discovery.

The panel used to show one empty "openai" provider for Hermes because it read
only .env API keys: subscriptions live in auth.json. OMP's model_cache table
also changed columns, and OpenCode/Pi were not listed as engines at all.
"""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import botty_backend


class TestEngineList(unittest.TestCase):
    def test_all_local_engines_are_exposed(self):
        engines = botty_backend.get_agent_engines()["engines"]
        ids = [e["id"] for e in engines]
        self.assertEqual(ids, ["hermes", "omp", "opencode", "pi", "claude", "codex"])

    def test_all_listed_engines_can_be_selected(self):
        for engine in botty_backend.ENGINE_IDS:
            with mock.patch.object(botty_backend, "save_config"):
                with mock.patch.object(botty_backend, "get_status", return_value={"state": "idle"}):
                    with mock.patch.object(botty_backend, "set_status"):
                        result = botty_backend.set_active_engine(engine)
            self.assertTrue(result["ok"], (engine, result))


class TestSubscriptionProviders(unittest.TestCase):
    def test_auth_json_credential_pool_is_read_without_values(self):
        with tempfile.TemporaryDirectory() as d:
            auth = Path(d) / "auth.json"
            auth.write_text(json.dumps({
                "credential_pool": {"opencode-go": {"secret": "do-not-show"},
                                    "copilot": {"token": "also-secret"}},
                "providers": {},
            }), encoding="utf-8")
            with mock.patch.object(botty_backend, "HERMES_PROFILE_AUTH_FILE", auth):
                with mock.patch.object(botty_backend, "HERMES_AUTH_FILE", Path(d) / "missing.json"):
                    with mock.patch.object(botty_backend, "HERMES_PROFILE_MODEL_CACHE", Path(d) / "missing.json"):
                        with mock.patch.object(botty_backend, "HERMES_DIR", Path(d)):
                            ids = botty_backend._hermes_subscription_provider_ids()
        self.assertIn("opencode-go", ids)
        self.assertIn("copilot", ids)
        self.assertFalse(any("secret" in str(x) or "token" in str(x) for x in ids))


class TestOmpModelCacheSchema(unittest.TestCase):
    def test_current_provider_id_schema_is_parsed(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "models.db"
            conn = sqlite3.connect(str(db))
            conn.execute("CREATE TABLE model_cache (provider_id TEXT, version INTEGER, "
                         "materialization_policy TEXT, updated_at INTEGER, models TEXT)")
            conn.execute("INSERT INTO model_cache VALUES (?,?,?,?,?)",
                         ("opencode-go:models-v3:abc", 1, "", 1,
                          json.dumps([{"id": "deepseek-v4-flash", "name": "DeepSeek V4 Flash"}])))
            conn.commit()
            conn.close()
            with mock.patch.object(botty_backend, "OMP_MODELS_DB", db):
                with mock.patch.object(botty_backend, "OMP_CONFIG_FILE", Path(d) / "config.yml"):
                    result = botty_backend.get_dynamic_engine_models("omp")
        providers = {p["id"]: p for p in result["providers"]}
        self.assertIn("opencode-go", providers)
        self.assertIn("opencode-go/deepseek-v4-flash",
                      [m["id"] for m in providers["opencode-go"]["models"]])


class TestOtherEngineCaches(unittest.TestCase):
    def test_opencode_uses_its_local_model_cache(self):
        with tempfile.TemporaryDirectory() as d:
            cache = Path(d) / "models.json"
            cache.write_text(json.dumps({"opencode-go": {
                "models": {"deepseek-v4-flash": {"name": "DeepSeek V4 Flash"}}}}), encoding="utf-8")
            config = Path(d) / "config.json"
            config.write_text(json.dumps({"provider": {"opencode-go": {}}}), encoding="utf-8")
            with mock.patch.object(botty_backend, "OPENCODE_CONFIG_FILE", config):
                with mock.patch.object(botty_backend, "OPENCODE_AUTH_FILE", Path(d) / "auth.json"):
                    with mock.patch.object(botty_backend, "OPENCODE_MODELS_CACHE", cache):
                        result = botty_backend.get_dynamic_engine_models("opencode")
        self.assertEqual(result["providers"][0]["id"], "opencode-go")
        self.assertEqual(result["providers"][0]["models"][0]["id"],
                         "opencode-go/deepseek-v4-flash")

    def test_pi_uses_its_model_store(self):
        with tempfile.TemporaryDirectory() as d:
            store = Path(d) / "models-store.json"
            store.write_text(json.dumps({"opencode-go": {
                "models": [{"id": "deepseek-v4.1-flash", "name": "DeepSeek V4.1 Flash"}]}}),
                encoding="utf-8")
            with mock.patch.object(botty_backend, "PI_SETTINGS_FILE", Path(d) / "settings.json"):
                with mock.patch.object(botty_backend, "PI_AUTH_FILE", Path(d) / "auth.json"):
                    with mock.patch.object(botty_backend, "PI_MODELS_STORE", store):
                        result = botty_backend.get_dynamic_engine_models("pi")
        self.assertEqual(result["providers"][0]["models"][0]["id"],
                         "opencode-go/deepseek-v4.1-flash")


if __name__ == "__main__":
    unittest.main()
