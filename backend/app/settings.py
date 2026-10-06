"""
Runtime Settings Manager
------------------------
Manages LLM provider configuration at runtime. On startup, reads defaults from
.env (via Config). Runtime changes are persisted to macfish_settings.json at
the project root, which takes precedence over .env on subsequent loads.

Supports two providers: "network" (cloud API) and "local" (local AI like Ollama),
plus an "ocr" block configuring the document-OCR engine.
"""

import copy
import json
import os
import threading
from typing import Dict, Any, Optional

from openai import OpenAI

# Path to the runtime settings file (project root, next to .env)
_SETTINGS_FILE = os.path.join(os.path.dirname(__file__), '../../macfish_settings.json')

#: OCR 引擎可选值
OCR_ENGINES = ('auto', 'vision', 'api', 'off')
#: OCR 页数上限的合法区间（防止配出一个把请求拖死的值）
OCR_MAX_PAGES_RANGE = (1, 500)


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
            # 文档 OCR 配置。本地 Vision 默认可用；视觉 API 需要单独配密钥。
            "ocr": {
                "engine": os.environ.get('OCR_ENGINE', 'auto'),
                "scan_pdf_fallback": os.environ.get('OCR_SCAN_PDF', 'true').lower() == 'true',
                "max_pages": int(os.environ.get('OCR_MAX_PAGES', '30')),
                "vision": {
                    # Vision 支持的语言标识；zh-Hans 需放在前面以保证简体优先
                    "languages": ["zh-Hans", "en-US"],
                },
                "api": {
                    "api_key": os.environ.get('VISION_API_KEY', ''),
                    "base_url": os.environ.get(
                        'VISION_BASE_URL',
                        'https://dashscope.aliyuncs.com/compatible-mode/v1',
                    ),
                    "model_name": os.environ.get('VISION_MODEL_NAME', 'qwen-vl-max'),
                },
            },
            # 预算护栏。max_calls = 0 表示不限；否则预估调用数超过它时拒绝启动模拟。
            "budget": {
                "max_calls": int(os.environ.get('BUDGET_MAX_CALLS', '0')),
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

    def get_ocr_config(self) -> Dict[str, Any]:
        """
        Return the OCR configuration block, deep-merged over the defaults.

        必须做合并：`_load()` 是裸 json.load、不与 _defaults() 合并，所以一个
        早于本次改动写下的 macfish_settings.json 里根本没有 'ocr' 键。
        """
        return self._merged_block('ocr')

    def get_budget_config(self) -> Dict[str, Any]:
        """预算护栏配置：{'max_calls': int}，0 表示不限。同样做默认值合并。"""
        return self._merged_block('budget')

    def _merged_block(self, name: str) -> Dict[str, Any]:
        """把 _data 里的某个配置块深合并到默认值之上。"""
        defaults = self._defaults()[name]
        current = self._data.get(name)
        if not isinstance(current, dict):
            return copy.deepcopy(defaults)

        merged = copy.deepcopy(defaults)
        for key, value in current.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key].update(value)
            else:
                merged[key] = value
        return merged

    def get_full_settings(self) -> Dict[str, Any]:
        """Return the complete settings dict (for the frontend settings API)."""
        # deepcopy：否则给下面写入的 *_masked 字段会落回 _data 本体，被下一次 _save() 持久化
        result = copy.deepcopy(self._data)
        # Mask API keys for display
        for prov in ('network', 'local'):
            if prov in result and result[prov].get('api_key'):
                key = result[prov]['api_key']
                if len(key) > 8:
                    result[prov]['api_key_masked'] = key[:4] + '****' + key[-4:]
                else:
                    result[prov]['api_key_masked'] = '****'

        ocr = result.get('ocr')
        if isinstance(ocr, dict):
            api = ocr.get('api')
            if isinstance(api, dict) and api.get('api_key'):
                key = api['api_key']
                api['api_key_masked'] = (key[:4] + '****' + key[-4:]) if len(key) > 8 else '****'
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

        ocr_data = data.get('ocr')
        if isinstance(ocr_data, dict):
            self._update_ocr(ocr_data)

        budget_data = data.get('budget')
        if isinstance(budget_data, dict) and 'max_calls' in budget_data:
            try:
                max_calls = int(budget_data['max_calls'])
            except (TypeError, ValueError):
                max_calls = None
            if max_calls is not None:
                self._data.setdefault('budget', {})['max_calls'] = max(0, max_calls)

        self._save()

    def _update_ocr(self, ocr_data: Dict[str, Any]):
        """合并 ocr 配置块，逐项校验取值范围。"""
        ocr = self._data.setdefault('ocr', {})

        if ocr_data.get('engine') in OCR_ENGINES:
            ocr['engine'] = ocr_data['engine']

        if 'scan_pdf_fallback' in ocr_data:
            ocr['scan_pdf_fallback'] = bool(ocr_data['scan_pdf_fallback'])

        if 'max_pages' in ocr_data:
            try:
                pages = int(ocr_data['max_pages'])
            except (TypeError, ValueError):
                pages = None
            if pages is not None:
                low, high = OCR_MAX_PAGES_RANGE
                ocr['max_pages'] = max(low, min(pages, high))

        vision_data = ocr_data.get('vision')
        if isinstance(vision_data, dict):
            languages = vision_data.get('languages')
            if isinstance(languages, list):
                cleaned = [str(item).strip() for item in languages if str(item).strip()]
                if cleaned:
                    ocr.setdefault('vision', {})['languages'] = cleaned

        api_data = ocr_data.get('api')
        if isinstance(api_data, dict):
            api = ocr.setdefault('api', {})
            if api_data.get('api_key') and '****' not in str(api_data['api_key']):
                api['api_key'] = api_data['api_key']
            if 'base_url' in api_data:
                api['base_url'] = api_data['base_url']
            if 'model_name' in api_data:
                api['model_name'] = api_data['model_name']

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
