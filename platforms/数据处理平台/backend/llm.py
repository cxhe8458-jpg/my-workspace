# -*- coding: utf-8 -*-
"""LLM 调用封装：OpenAI 兼容 / Anthropic 两种接口风格。

任务模型配置字段：
  api(openai|anthropic), base_url, api_key, model, role, prompt, skills[], temperature
system 提示词 = 角色 + 自定义 prompt + 挂载的 skill 全文。
"""
import os, json, re
from urllib.parse import urlparse
import requests
from .paths import SKILLS_DIR

TIMEOUT = 180


def _proxies_for(base_url):
    """本地推理服务(127.0.0.1/localhost，如 Ollama/LM Studio)绕过系统代理。"""
    try:
        host = urlparse(base_url).hostname or ""
    except Exception:
        host = ""
    if host in ("127.0.0.1", "localhost", "::1") or host.startswith("192.168.") or host.startswith("10."):
        return {"http": None, "https": None}
    return None


def build_system_prompt(cfg):
    parts = []
    if cfg.get("role"):
        parts.append("# 角色\n" + cfg["role"])
    if cfg.get("prompt"):
        parts.append("# 任务指令\n" + cfg["prompt"])
    for s in cfg.get("skills", []) or []:
        p = os.path.join(SKILLS_DIR, s)
        if os.path.isfile(p):
            try:
                parts.append(f"# 挂载 Skill: {s}\n" + open(p, encoding="utf-8").read())
            except Exception:
                pass
    return "\n\n---\n\n".join(parts)


def chat(cfg, user_text, system_extra=""):
    """单轮对话，返回 (text, error)。"""
    system = build_system_prompt(cfg)
    if system_extra:
        system = (system + "\n\n" + system_extra) if system else system_extra
    api = cfg.get("api", "openai")
    base = (cfg.get("base_url") or "").rstrip("/")
    key = cfg.get("api_key", "")
    model = cfg.get("model", "")
    try:
        temp = float(cfg.get("temperature", 0.2))
    except (TypeError, ValueError):
        temp = 0.2
    try:
        if api == "anthropic":
            r = requests.post(
                base + "/v1/messages", proxies=_proxies_for(base),
                headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": model, "max_tokens": 2048, "temperature": temp,
                      "system": system or "You are a helpful assistant.",
                      "messages": [{"role": "user", "content": user_text}]},
                timeout=TIMEOUT)
            if r.status_code != 200:
                return None, f"HTTP {r.status_code}: {r.text[:300]}"
            data = r.json()
            return "".join(b.get("text", "") for b in data.get("content", [])), None
        # OpenAI 兼容
        msgs = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.append({"role": "user", "content": user_text})
        r = requests.post(
            base + "/chat/completions", proxies=_proxies_for(base),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": model, "messages": msgs, "temperature": temp},
            timeout=TIMEOUT)
        if r.status_code != 200:
            return None, f"HTTP {r.status_code}: {r.text[:300]}"
        data = r.json()
        return data["choices"][0]["message"]["content"], None
    except requests.exceptions.RequestException as e:
        return None, f"网络错误: {e}"
    except Exception as e:
        return None, f"调用失败: {e!r}"


def extract_json(text):
    """从模型回复中提取第一个 JSON 对象。"""
    if not text:
        return None
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    raw = m.group(1) if m else None
    if not raw:
        m = re.search(r"\{.*\}", text, re.S)
        raw = m.group(0) if m else None
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None
