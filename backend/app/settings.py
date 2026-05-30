"""
Runtime Settings Manager
------------------------
Manages LLM provider configuration at runtime. On startup, reads defaults from
.env (via Config). Runtime changes are persisted to mirofish_settings.json at
the project root, which takes precedence over .env on subsequent loads.

Supports two providers: "network" (cloud API) and "local" (local AI like Ollama).
"""

import json
import os
import threading
from typing import Dict, Any, Optional

from openai import OpenAI

# Path to the runtime settings file (project root, next to .env)
_SETTINGS_FILE = os.path.join(os.path.dirname(__file__), '../../macfish_settings.json')


class RuntimeSettings:
    """Thread-safe singleton for runtime LLM configuration."""

    _instance = None
    _lock = threading.Lock()

    def __init__(self):
        self._data: Dict[str, Any] = {}
        self._load()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    # ---- persistence ----

    def _load(self):
        """Load settings from JSON file, falling back to defaults."""
        if os.path.exists(_SETTINGS_FILE):
            try:
                with open(_SETTINGS_FILE, 'r', encoding='utf-8') as f:
                    self._data = json.load(f)
                return
            except (json.JSONDecodeError, IOError):
                pass
        self._data = self._defaults()

    def _save(self):
        """Persist current settings to JSON file."""
        os.makedirs(os.path.dirname(_SETTINGS_FILE), exist_ok=True)
        tmp = _SETTINGS_FILE + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(self._data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, _SETTINGS_FILE)

    @staticmethod
    def _defaults() -> Dict[str, Any]:
        from .config import Config
        return {
            "provider": os.environ.get('LLM_PROVIDER', 'network'),
            "network": {
                "api_key": Config.LLM_API_KEY or '',
                "base_url": Config.LLM_BASE_URL,
                "model_name": Config.LLM_MODEL_NAME,
            },
            "local": {
                "api_key": Config.LOCAL_LLM_API_KEY or '',
                "base_url": Config.LOCAL_LLM_BASE_URL,
                "model_name": Config.LOCAL_LLM_MODEL_NAME,
            },
        }

    # ---- public API ----

    def get_llm_config(self, provider: Optional[str] = None) -> Dict[str, Any]:
        """
        Return {api_key, base_url, model} for *provider* (default: active provider).
        """
        p = provider or self._data.get('provider', 'network')
        cfg = self._data.get(p, self._data.get('network', {}))
        return {
            'api_key': cfg.get('api_key', ''),
            'base_url': cfg.get('base_url', 'https://api.deepseek.com'),
            'model': cfg.get('model_name', 'deepseek-chat'),
        }

    def get_full_settings(self) -> Dict[str, Any]:
        """Return the complete settings dict (for the frontend settings API)."""
        result = dict(self._data)
        # Mask API keys for display
        for prov in ('network', 'local'):
            if prov in result and result[prov].get('api_key'):
                key = result[prov]['api_key']
                if len(key) > 8:
                    result[prov]['api_key_masked'] = key[:4] + '****' + key[-4:]
                else:
                    result[prov]['api_key_masked'] = '****'
        return result

    def update_settings(self, data: Dict[str, Any]):
        """Merge *data* into current settings and persist."""
        if 'provider' in data and data['provider'] in ('network', 'local'):
            self._data['provider'] = data['provider']
        for prov in ('network', 'local'):
            if prov in data:
                self._data.setdefault(prov, {})
                prov_data = data[prov]
                if 'api_key' in prov_data and prov_data['api_key']:
                    # Only overwrite if a real key was provided (skip masked placeholders)
                    if '****' not in str(prov_data['api_key']):
                        self._data[prov]['api_key'] = prov_data['api_key']
                if 'base_url' in prov_data:
                    self._data[prov]['base_url'] = prov_data['base_url']
                if 'model_name' in prov_data:
                    self._data[prov]['model_name'] = prov_data['model_name']
        self._save()

    def test_connection(self, provider: Optional[str] = None) -> Dict[str, Any]:
        """
        Send a minimal chat request to verify the provider is reachable.
        Returns {"ok": True, "message": str} or {"ok": False, "message": str}.
        """
        cfg = self.get_llm_config(provider)
        if not cfg['base_url']:
            return {"ok": False, "message": "Base URL is not configured."}

        try:
            client = OpenAI(api_key=cfg['api_key'] or 'not-needed', base_url=cfg['base_url'])
            response = client.chat.completions.create(
                model=cfg['model'],
                messages=[{"role": "user", "content": "Hi"}],
                max_tokens=10,
                temperature=0,
            )
            reply = response.choices[0].message.content
            return {"ok": True, "message": f"Connected successfully. Reply: {reply}"}
        except Exception as exc:
            return {"ok": False, "message": str(exc)}


# Module-level singleton
runtime_settings = RuntimeSettings()
