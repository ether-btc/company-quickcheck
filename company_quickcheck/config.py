#!/usr/bin/env python3
"""Configuration management for company-quickcheck."""

import os
import yaml
from pathlib import Path
from typing import Dict, Any, Optional

DEFAULT_CONFIG_PATH = Path.home() / ".hermes" / "config.yaml"


class Config:
    """Configuration holder."""

    def __init__(self, config_path: Optional[Path] = None):
        self.config_path = config_path or DEFAULT_CONFIG_PATH
        self.data = self._load_config()
        self._validate()

    def _load_config(self) -> Dict[str, Any]:
        """Load configuration from YAML file."""
        if self.config_path.exists():
            with open(self.config_path, "r") as f:
                return yaml.safe_load(f) or {}
        return {}

    def _validate(self):
        """Validate configuration."""
        # Ensure required keys exist or set defaults
        self.data.setdefault("api", {})["base_url"] = self.data.get("api", {}).get("base_url", "https://api.opendata.host/1.0")
        self.data.setdefault("api", {})["api_key"] = self.data.get("api", {}).get("api_key")
        self.data.setdefault("rate_limit", {})["enabled"] = self.data.get("rate_limit", {}).get("enabled", True)
        self.data.setdefault("rate_limit", {})["delay"] = self.data.get("rate_limit", {}).get("delay", 1.1)
        # Country configuration
        self.data.setdefault("country", "AT")  # Default to Austria

    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value."""
        keys = key.split(".")
        value = self.data
        for k in keys:
            value = value.get(k, {})
        return value if value != {} else default

    def get_api_key(self) -> Optional[str]:
        """Get API key from config or environment."""
        key = self.data.get("api", {}).get("api_key")
        if not key:
            key = os.getenv("OPENDATA_API_KEY")
        return key

    def get_rate_limit_delay(self) -> float:
        """Get rate limit delay in seconds."""
        enabled = self.data.get("rate_limit", {}).get("enabled", True)
        delay = self.data.get("rate_limit", {}).get("delay", 1.1)
        return delay if enabled else 0

    def get_country(self) -> str:
        """Get country code (e.g., 'AT', 'DE')."""
        return self.data.get("country", "AT")

    def get_base_url(self) -> str:
        """Get base URL for the country."""
        # For now, return from config, fallback to default
        return self.data.get("api", {}).get("base_url", "https://api.opendata.host/1.0")


# Global config instance (can be used across the application)
config = Config()