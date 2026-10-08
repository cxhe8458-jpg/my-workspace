# -*- coding: utf-8 -*-
"""任务模型配置存储（JSON 文件，最多 5 个）+ LLM 供应商预置。"""
import os, json, threading, uuid
from .paths import DATA_DIR

MODELS_PATH = os.path.join(DATA_DIR, "task_models.json")
MAX_MODELS = 5
_lock = threading.Lock()

# 供应商预置：base_url 可手改；api=接口风格 openai/anthropic/gemini
PROVIDERS = [
    {"key": "deepseek",  "name": "DeepSeek",            "api": "openai",
     "base_url": "https://api.deepseek.com/v1",                       "default_model": "deepseek-chat"},
    {"key": "qwen",      "name": "通义千问 (DashScope)",  "api": "openai",
     "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "default_model": "qwen-plus"},
    {"key": "zhipu",     "name": "智谱 GLM",             "api": "openai",
     "base_url": "https://open.bigmodel.cn/api/paas/v4",              "default_model": "glm-4-plus"},
    {"key": "moonshot",  "name": "月之暗面 Kimi",         "api": "openai",
     "base_url": "https://api.moonshot.cn/v1",                        "default_model": "moonshot-v1-32k"},
    {"key": "doubao",    "name": "豆包 (火山方舟)",        "api": "openai",
     "base_url": "https://ark.cn-beijing.volces.com/api/v3",          "default_model": ""},
    {"key": "openai",    "name": "OpenAI",               "api": "openai",
     "base_url": "https://api.openai.com/v1",                         "default_model": "gpt-4o"},
    {"key": "anthropic", "name": "Anthropic Claude",     "api": "anthropic",
     "base_url": "https://api.anthropic.com",                         "default_model": "claude-sonnet-5"},
    {"key": "gemini",    "name": "Google Gemini",        "api": "openai",
     "base_url": "https://generativelanguage.googleapis.com/v1beta/openai", "default_model": "gemini-2.5-pro"},
    {"key": "custom",    "name": "自定义 (OpenAI 兼容)",   "api": "openai",
     "base_url": "",                                                  "default_model": ""},
]


def _load():
    if os.path.exists(MODELS_PATH):
        try:
            return json.load(open(MODELS_PATH, encoding="utf-8"))
        except Exception:
            return []
    return []


def _save(models):
    tmp = MODELS_PATH + ".tmp"
    json.dump(models, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    os.replace(tmp, MODELS_PATH)


def list_models(mask_key=True):
    with _lock:
        models = _load()
    if mask_key:
        out = []
        for m in models:
            m = dict(m)
            k = m.get("api_key", "")
            m["api_key_set"] = bool(k)
            m["api_key"] = (k[:6] + "…" + k[-4:]) if len(k) > 12 else ("***" if k else "")
            out.append(m)
        return out
    return models


def get_model(mid):
    with _lock:
        for m in _load():
            if m["id"] == mid:
                return m
    return None


def save_model(payload):
    """新建或更新。payload 含 id 则更新；api_key 为空串时保留旧值。"""
    with _lock:
        models = _load()
        mid = payload.get("id")
        if mid:
            for i, m in enumerate(models):
                if m["id"] == mid:
                    if not payload.get("api_key"):
                        payload["api_key"] = m.get("api_key", "")
                    models[i] = {**m, **payload}
                    _save(models)
                    return models[i], None
            return None, "未找到该任务模型"
        if len(models) >= MAX_MODELS:
            return None, f"最多只能保存 {MAX_MODELS} 个任务模型"
        payload["id"] = uuid.uuid4().hex[:8]
        models.append(payload)
        _save(models)
        return payload, None


def delete_model(mid):
    with _lock:
        models = _load()
        n = len(models)
        models = [m for m in models if m["id"] != mid]
        _save(models)
        return len(models) < n
