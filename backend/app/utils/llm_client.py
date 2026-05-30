"""
LLM客户端封装
统一使用OpenAI兼容格式调用，支持云端和本地 API 切换
"""

import json
import re
from typing import Optional, Dict, Any, List
from openai import OpenAI


def _clean_think_tags(content: str) -> str:
    """移除模型输出中的 <think> 标签（DeepSeek-R1、MiniMax等推理模型）"""
    # 标准闭合标签
    content = re.sub(r'<think>[\s\S]*?</think>', '', content)
    # 未闭合标签（从<think>到结尾）
    content = re.sub(r'<think>[\s\S]*$', '', content)
    return content.strip()


def _clean_markdown_json(content: str) -> str:
    """清理markdown代码块标记"""
    cleaned = content.strip()
    cleaned = re.sub(r'^```(?:json)?\s*\n?', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\n?```\s*$', '', cleaned)
    return cleaned.strip()


class LLMClient:
    """LLM客户端（兼容所有OpenAI格式API，支持运行时切换provider）"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
        provider: Optional[str] = None
    ):
        from ..settings import runtime_settings

        # 如果显式传参则优先使用，否则从 RuntimeSettings 读取当前激活的 provider 配置
        cfg = runtime_settings.get_llm_config(provider)
        self.provider = provider or runtime_settings._data.get('provider', 'network')
        self.api_key = api_key or cfg['api_key']
        self.base_url = base_url or cfg['base_url']
        self.model = model or cfg['model']

        # Local provider (Ollama/LM Studio) may not need an API key
        if not self.api_key:
            if self.provider == 'local':
                self.api_key = 'not-needed'  # placeholder for local APIs
            else:
                raise ValueError("LLM_API_KEY 未配置（云端模式）")

        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url
        )

    @classmethod
    def from_provider(cls, provider: str) -> 'LLMClient':
        """为指定 provider 创建客户端（'network' 或 'local'）"""
        return cls(provider=provider)

    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        response_format: Optional[Dict] = None
    ) -> str:
        """
        发送聊天请求

        Args:
            messages: 消息列表
            temperature: 温度参数
            max_tokens: 最大token数
            response_format: 响应格式（如JSON模式）

        Returns:
            模型响应文本
        """
        kwargs = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        if response_format:
            kwargs["response_format"] = response_format

        response = self.client.chat.completions.create(**kwargs)
        content = response.choices[0].message.content
        content = _clean_think_tags(content)
        return content

    def chat_json(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.3,
        max_tokens: int = 4096
    ) -> Dict[str, Any]:
        """
        发送聊天请求并返回JSON

        先尝试使用 json_object 模式。
        如果JSON解析失败，回退到无格式模式并尝试提取JSON。

        Args:
            messages: 消息列表
            temperature: 温度参数
            max_tokens: 最大token数

        Returns:
            解析后的JSON对象
        """
        # 尝试1: 使用 json_object 格式
        try:
            response = self.chat(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format={"type": "json_object"}
            )
            cleaned = _clean_markdown_json(response)
            return json.loads(cleaned)
        except Exception:
            pass

        # 尝试2: 回退到无格式模式，显式要求JSON输出
        fallback_messages = list(messages)
        fallback_messages.append({
            "role": "system",
            "content": "你必须只输出有效的JSON，不要包含任何markdown代码块或其他文本。"
        })
        response = self.chat(
            messages=fallback_messages,
            temperature=min(temperature, 0.1),
            max_tokens=max_tokens
        )
        cleaned = _clean_markdown_json(response)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            # 最后尝试: 从文本中提取第一个JSON对象
            json_match = re.search(r'\{[\s\S]*\}', cleaned)
            if json_match:
                try:
                    return json.loads(json_match.group(0))
                except json.JSONDecodeError:
                    pass
            raise ValueError(f"LLM返回的JSON格式无效: {cleaned[:500]}")
