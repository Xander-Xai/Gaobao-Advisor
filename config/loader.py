"""
LLM 配置加载器 — 从 YAML 配置文件 + 环境变量合并生成最终配置。

优先级: 环境变量 > YAML 文件 > 默认值

用法:
    from config.loader import load_llm_config
    CONFIG = load_llm_config()
"""

from __future__ import annotations

import os
from typing import Any


def _load_yaml_simple(path: str) -> dict[str, Any]:
    """极简 YAML 解析器（仅支持本项目需要的子集）。
    不引入 pyyaml 依赖，避免增加安装复杂度。
    支持: 嵌套字典、字符串/数字/null 值、带冒号的引号字符串。

    注意: 使用 partition(":") 只在第一个冒号处分割，因此
    "key: https://example.com:8080/v1" 会正确解析为 key="https://example.com:8080/v1"。
    但如果 VALUE 本身不含引号且包含冒号（如 "note: This is: important"），
    也会被正确解析，因为 partition 只分割第一个冒号。

    缩进规则: 栈中记录“开启该子字典的键的缩进”。一个缩进为 I 的行属于
    最内层的“键缩进 < I”的子字典；否则属于根字典。因此
    ``providers:`` / ``  openai:`` / ``    rpm: 1`` 会分别落入
    根字典、providers 子字典、openai 子字典。
    """
    result: dict[str, Any] = {}
    current_section: dict[str, Any] = result
    section_stack: list[tuple[int, dict]] = []

    with open(path, encoding="utf-8") as f:
        for line in f:
            stripped = line.rstrip()
            if not stripped or stripped.lstrip().startswith("#"):
                continue

            indent = len(line) - len(line.lstrip())
            raw = stripped.lstrip()

            # 回退到正确的父级
            while section_stack and indent <= section_stack[-1][0]:
                section_stack.pop()
            current_section = section_stack[-1][1] if section_stack else result

            if ":" in raw:
                key, _, value = raw.partition(":")
                key = key.strip()
                value = value.strip()

                if value == "" or value == "|":
                    # 子字典或列表开始
                    new_section: dict[str, Any] = {}
                    current_section[key] = new_section
                    section_stack.append((indent, new_section))
                else:
                    current_section[key] = _parse_yaml_value(value)

    return result


def load_yaml_mapping(path: str) -> dict[str, Any]:
    """读取一个 YAML 子集的映射文件。

    与 :func:`_load_yaml_simple` 使用同一解析器，但对不存在的文件返回空字典，
    便于可选配置文件（如模型治理配额）安全缺省。
    """
    if not path or not os.path.exists(path):
        return {}
    return _load_yaml_simple(path)


def _parse_yaml_value(value: str) -> Any:
    """解析 YAML 值：字符串、数字、布尔、null。"""
    if value in ("null", "~", ""):
        return None
    if value.lower() in ("true", "yes"):
        return True
    if value.lower() in ("false", "no"):
        return False
    # 数字
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        pass
    # 去除引号
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    return value


def load_llm_config(config_path: str | None = None) -> dict[str, Any]:
    """加载 LLM 配置，合并 YAML + 环境变量。

    Args:
        config_path: YAML 配置文件路径。为 None 时使用默认路径。

    Returns:
        配置字典，包含 base_url, api_key, model, max_tokens, temperature, enable_search
    """
    # 确定 YAML 路径
    if config_path is None:
        config_path = os.environ.get(
            "LLM_CONFIG_PATH",
            os.path.join(os.path.dirname(__file__), "llm_providers.yaml"),
        )

    # 加载 YAML（如果存在）
    yaml_config: dict[str, Any] = {}
    if os.path.exists(config_path):
        yaml_config = _load_yaml_simple(config_path)

    providers = yaml_config.get("providers", {})
    defaults = yaml_config.get("defaults", {})

    # 确定当前 provider
    provider_name = os.getenv("LLM_PROVIDER", "").lower()
    if not provider_name:
        provider_name = defaults.get("provider", "deepseek")

    # 从 YAML 获取 provider 配置
    preset = providers.get(provider_name, {})

    # 默认值
    default_temp = defaults.get("temperature", 0.7)
    default_search = defaults.get("enable_search", True)

    # 合并: 环境变量 > YAML > 默认值
    enable_search_raw = os.getenv("ENABLE_SEARCH", "")
    if enable_search_raw:
        enable_search = enable_search_raw.lower() in ("true", "1", "yes")
    else:
        enable_search = default_search

    # temperature: 环境变量 > YAML > 默认值
    temp_raw = os.getenv("LLM_TEMPERATURE", "")
    if temp_raw:
        try:
            temperature = float(temp_raw)
        except (ValueError, TypeError):
            temperature = float(preset.get("temperature", default_temp))
    else:
        temperature = float(preset.get("temperature", default_temp))

    # max_tokens: 环境变量 > YAML > 默认值
    max_tokens_raw = os.getenv("LLM_MAX_TOKENS", "")
    if max_tokens_raw:
        try:
            max_tokens = int(max_tokens_raw)
        except (ValueError, TypeError):
            max_tokens = preset.get("max_tokens") or defaults.get("max_tokens")
    else:
        max_tokens = preset.get("max_tokens") or defaults.get("max_tokens")

    return {
        "base_url": os.getenv("LLM_BASE_URL") or preset.get("base_url", "https://api.deepseek.com"),
        "api_key": os.getenv("LLM_API_KEY", ""),
        "model": os.getenv("LLM_MODEL") or preset.get("model", "deepseek-chat"),
        "max_tokens": max_tokens,
        "temperature": temperature,
        "enable_search": enable_search,
    }


def primary_provider_name(config_path: str | None = None) -> str:
    """返回当前主 Provider 名称。

    主 Provider 仍然是唯一拥有全局 ``LLM_*`` 环境变量覆盖权的 Provider，
    其余 Provider 只能通过 ``LLM_<字段>_<PROVIDER>`` 形式单独覆盖，
    避免多 Provider 场景下环境变量互相污染。
    """
    if config_path is None:
        config_path = os.environ.get(
            "LLM_CONFIG_PATH",
            os.path.join(os.path.dirname(__file__), "llm_providers.yaml"),
        )
    yaml_config = load_yaml_mapping(config_path)
    defaults = yaml_config.get("defaults", {}) or {}
    provider_name = os.getenv("LLM_PROVIDER", "").strip().lower()
    if provider_name:
        return provider_name
    return str(defaults.get("provider", "deepseek")).strip().lower()


def load_provider_config(provider: str, config_path: str | None = None) -> dict[str, Any]:
    """读取单个 Provider 的完整调用配置。

    返回 ``base_url`` / ``model`` / ``temperature`` / ``max_tokens`` /
    ``api_key``。凭据只从环境变量读取，配置文件里永远不出现密钥。
    """
    if config_path is None:
        config_path = os.environ.get(
            "LLM_CONFIG_PATH",
            os.path.join(os.path.dirname(__file__), "llm_providers.yaml"),
        )
    yaml_config = load_yaml_mapping(config_path)
    providers = yaml_config.get("providers", {}) or {}
    defaults = yaml_config.get("defaults", {}) or {}
    name = str(provider).strip()
    preset = providers.get(name, {}) or {}

    is_primary = name == primary_provider_name(config_path)
    suffix = name.upper().replace("-", "_")

    def _env(field_name: str, provider_specific: str) -> str:
        if is_primary:
            return os.getenv(provider_specific, "")
        return os.getenv(f"{provider_specific}_{suffix}", "") or os.getenv(provider_specific, "")

    max_tokens_raw = _env("max_tokens", "LLM_MAX_TOKENS")
    temperature_raw = _env("temperature", "LLM_TEMPERATURE")
    try:
        max_tokens = int(max_tokens_raw) if max_tokens_raw else (preset.get("max_tokens") or defaults.get("max_tokens"))
    except (TypeError, ValueError):
        max_tokens = preset.get("max_tokens") or defaults.get("max_tokens")
    try:
        temperature = (
            float(temperature_raw)
            if temperature_raw
            else float(preset.get("temperature", defaults.get("temperature", 0.7)))
        )
    except (TypeError, ValueError):
        temperature = float(preset.get("temperature", defaults.get("temperature", 0.7)))

    return {
        "provider": name,
        "base_url": _env("base_url", "LLM_BASE_URL") or preset.get("base_url", ""),
        "model": _env("model", "LLM_MODEL") or preset.get("model", ""),
        "api_key": os.getenv(f"LLM_API_KEY_{suffix}", "") or os.getenv("LLM_API_KEY", ""),
        "max_tokens": max_tokens,
        "temperature": temperature,
        "enable_search": defaults.get("enable_search", True),
    }


def load_all_provider_configs(config_path: str | None = None) -> dict[str, dict[str, Any]]:
    """读取配置文件中声明的全部 Provider。"""
    if config_path is None:
        config_path = os.environ.get(
            "LLM_CONFIG_PATH",
            os.path.join(os.path.dirname(__file__), "llm_providers.yaml"),
        )
    yaml_config = load_yaml_mapping(config_path)
    providers = yaml_config.get("providers", {}) or {}
    return {str(name): load_provider_config(str(name), config_path) for name in providers}
