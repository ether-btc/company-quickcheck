#!/usr/bin/env python3
"""Unit tests for company_quickcheck.config module (TST-03)."""

import os
import tempfile
import unittest
from pathlib import Path
from company_quickcheck.config import Config, DEFAULT_CONFIG_PATH


class TestConfigDefaults(unittest.TestCase):
    """Tests for Config default values."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.config_file = Path(self.temp_dir) / "config.yaml"

    def test_loads_empty_config_when_file_missing(self):
        """When config file path doesn't exist, _load_config returns {}."""
        cfg = Config(config_path=Path("/nonexistent/path/config.yaml"))
        # _load_config returns {} for missing files, but _validate() then
        # applies defaults, so data is not empty — just no file-loaded values
        self.assertEqual(cfg.get_base_url(), "https://api.opendata.host/1.0")

    def test_default_base_url(self):
        """Default base URL is opendata.host."""
        cfg = Config(config_path=self.config_file)
        self.assertEqual(cfg.get_base_url(), "https://api.opendata.host/1.0")

    def test_default_rate_limit_delay(self):
        """Default rate limit delay is 1.1 seconds."""
        cfg = Config(config_path=self.config_file)
        self.assertEqual(cfg.get_rate_limit_delay(), 1.1)

    def test_default_rate_limit_enabled(self):
        """Rate limit is enabled by default."""
        cfg = Config(config_path=self.config_file)
        # get_rate_limit_delay() returns the delay when enabled
        self.assertEqual(cfg.get_rate_limit_delay(), 1.1)

    def test_default_country(self):
        """Default country is AT (Austria)."""
        cfg = Config(config_path=self.config_file)
        self.assertEqual(cfg.get_country(), "AT")

    def test_get_with_default_when_key_missing(self):
        """get() returns the provided default when key is absent."""
        cfg = Config(config_path=self.config_file)
        self.assertIsNone(cfg.get("nonexistent"))
        self.assertEqual(cfg.get("nonexistent", "default"), "default")

    def test_get_nested_key(self):
        """get('api.base_url') retrieves nested keys."""
        cfg = Config(config_path=self.config_file)
        # This tests the key splitting logic
        val = cfg.get("api.base_url")
        self.assertIsNotNone(val)


class TestConfigYamlLoading(unittest.TestCase):
    """Tests for Config YAML file loading."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.config_file = Path(self.temp_dir) / "config.yaml"

    def test_loads_yaml_file(self):
        """Config loads values from a YAML file."""
        content = """
api:
  base_url: https://custom.api.example.com
  api_key: test-key-123
country: DE
rate_limit:
  enabled: true
  delay: 2.5
"""
        self.config_file.write_text(content)
        cfg = Config(config_path=self.config_file)
        self.assertEqual(cfg.get("api", {}).get("base_url"), "https://custom.api.example.com")
        self.assertEqual(cfg.get_api_key(), "test-key-123")
        self.assertEqual(cfg.get_country(), "DE")
        self.assertEqual(cfg.get_rate_limit_delay(), 2.5)

    def test_missing_api_key_from_config_falls_back_to_env(self):
        """When api_key is not in config, get_api_key() checks OPENDATA_API_KEY env."""
        content = """
api:
  base_url: https://api.example.com
"""
        self.config_file.write_text(content)
        os.environ["OPENDATA_API_KEY"] = "env-key-456"
        try:
            cfg = Config(config_path=self.config_file)
            self.assertEqual(cfg.get_api_key(), "env-key-456")
        finally:
            del os.environ["OPENDATA_API_KEY"]

    def test_missing_api_key_no_env_returns_none(self):
        """When neither config nor env has api_key, get_api_key() returns None."""
        content = """
api:
  base_url: https://api.example.com
"""
        self.config_file.write_text(content)
        # Ensure env var is not set
        os.environ.pop("OPENDATA_API_KEY", None)
        cfg = Config(config_path=self.config_file)
        self.assertIsNone(cfg.get_api_key())

    def test_rate_limit_disabled_returns_zero_delay(self):
        """When rate_limit.enabled is False, get_rate_limit_delay() returns 0."""
        content = """
rate_limit:
  enabled: false
  delay: 5.0
"""
        self.config_file.write_text(content)
        cfg = Config(config_path=self.config_file)
        self.assertEqual(cfg.get_rate_limit_delay(), 0)

    def test_valid_yaml_only(self):
        """Config only processes valid YAML (invalid YAML raises ScannerError)."""
        # yaml.safe_load raises ScannerError on invalid YAML - code has no
        # try/except, so invalid YAML propagates up as an exception
        self.config_file.write_text("invalid: yaml: content: [")
        with self.assertRaises(Exception):  # yaml.scanner.ScannerError
            Config(config_path=self.config_file)


class TestBuildRateLimiter(unittest.TestCase):
    """Tests for Config.build_rate_limiter()."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.config_file = Path(self.temp_dir) / "config.yaml"

    def test_build_rate_limiter_returns_limiter(self):
        """build_rate_limiter() returns an AdaptiveRateLimiter instance."""
        self.config_file.write_text("")
        cfg = Config(config_path=self.config_file)
        from company_quickcheck.rate_limiter import AdaptiveRateLimiter
        limiter = cfg.build_rate_limiter()
        self.assertIsInstance(limiter, AdaptiveRateLimiter)

    def test_build_rate_limiter_with_custom_delay(self):
        """Custom rate_limit.delay sets the initial_delay."""
        content = """
rate_limit:
  delay: 3.0
"""
        self.config_file.write_text(content)
        cfg = Config(config_path=self.config_file)
        limiter = cfg.build_rate_limiter()
        self.assertEqual(limiter.initial_delay, 3.0)

    def test_build_rate_limiter_adaptive_config(self):
        """adaptive_rate_limit.* keys configure the limiter."""
        content = """
rate_limit:
  delay: 2.0
adaptive_rate_limit:
  min_delay: 0.5
  max_delay: 20.0
  backoff_multiplier: 2.0
  success_divisor: 2.0
"""
        self.config_file.write_text(content)
        cfg = Config(config_path=self.config_file)
        limiter = cfg.build_rate_limiter()
        self.assertEqual(limiter.min_delay, 0.5)
        self.assertEqual(limiter.max_delay, 20.0)
        self.assertEqual(limiter.backoff_multiplier, 2.0)


class TestGetStealthCoreConfigPath(unittest.TestCase):
    """Tests for Config.get_stealth_core_config_path()."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.config_file = Path(self.temp_dir) / "config.yaml"

    def test_default_path_is_hermes_location(self):
        """Default stealth_core config path is in the hermes projects dir."""
        self.config_file.write_text("")
        cfg = Config(config_path=self.config_file)
        path = cfg.get_stealth_core_config_path()
        self.assertIn("stealth-core", path)
        self.assertIn("config.yaml", path)

    def test_custom_stealth_core_path(self):
        """stealth_core.config_path in config overrides the default."""
        content = """
stealth_core:
  config_path: /custom/path/config.yaml
"""
        self.config_file.write_text(content)
        cfg = Config(config_path=self.config_file)
        self.assertEqual(cfg.get_stealth_core_config_path(), "/custom/path/config.yaml")


if __name__ == "__main__":
    unittest.main()
