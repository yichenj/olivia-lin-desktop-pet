"""Model connection settings are local inputs, never embedded defaults."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backend.config import Config, load_config


class ConfigTests(unittest.TestCase):
    def test_no_implicit_service_when_local_configuration_is_absent(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"OLIVIA_CONFIG": str(Path(directory) / "absent.json")}, clear=True):
                config = load_config()
        self.assertEqual((config.api_key, config.base_url, config.model), ("", "", ""))
        self.assertEqual(Config().base_url, "")
        self.assertEqual(Config().model, "")
        self.assertEqual(config.memory_model_timeout_seconds, 300)
        self.assertEqual(config.memory_wait_timeout_seconds, 1800)

    def test_memory_timeouts_are_configurable_and_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'local.json'
            with patch.dict(os.environ, {'OLIVIA_CONFIG': str(path)}, clear=True):
                path.write_text(json.dumps({'memory_model_timeout_seconds': 600,
                                            'memory_wait_timeout_seconds': 3600}))
                config = load_config()
                self.assertEqual(config.memory_model_timeout_seconds, 600)
                self.assertEqual(config.memory_wait_timeout_seconds, 3600)
                for key in ('memory_model_timeout_seconds', 'memory_wait_timeout_seconds'):
                    for value in (True, 0, 100000, '300'):
                        path.write_text(json.dumps({key: value}))
                        with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                            load_config()

    def test_key_url_and_model_are_read_together_with_environment_overrides(self):
        values = {"api_key": "local-test-key", "base_url": "https://example.invalid/local", "model": "local-model"}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "local.json"
            path.write_text(json.dumps(values))
            with patch.dict(os.environ, {"OLIVIA_CONFIG": str(path)}, clear=True):
                config = load_config()
                self.assertEqual((config.api_key, config.base_url, config.model), tuple(values.values()))
                with patch.dict(os.environ, {"ARK_API_KEY": "override-key", "ARK_BASE_URL": "https://example.invalid/override", "ARK_MODEL": "override-model"}):
                    config = load_config()
                    self.assertEqual((config.api_key, config.base_url, config.model), ("override-key", "https://example.invalid/override", "override-model"))
                self.assertEqual(json.loads(path.read_text()), values)
