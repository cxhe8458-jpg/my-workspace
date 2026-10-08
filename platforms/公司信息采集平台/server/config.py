# -*- coding: utf-8 -*-
"""配置管理：数据库连接、Basic Auth 账号、抓取并发、入库默认值。

配置统一保存在项目根目录的 server_config.json（首次运行自动生成模板）。
用户可在网页界面填写并保存，无需手改文件。
"""
import os
import json
import copy

# 项目根目录（server/ 的上一级）
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "server_config.json")

# 默认配置模板
DEFAULT_CONFIG = {
    "db": {
        "host": "127.0.0.1",
        "port": 3306,
        "database": "",
        "username": "root",
        "password": "",
        "table": "company",
    },
    "auth": {
        "username": "admin",
        "password": "admin123",
    },
    "crawl": {
        "max_workers": 8,          # 最大同时抓取家数（script.py 抓取线程已是 8）
    },
    # AI 接口：抓取解析与中英互译都走这里。任何 **OpenAI 兼容** 的服务都能接
    # （DeepSeek / 通义千问 / Kimi / 智谱 / 硅基流动 / 火山方舟 / OpenRouter / 本地 Ollama…）,
    # 在网页「设置」页随时改，改完立刻生效、不用重启（见 script.ai_config）。
    "ai": {
        "provider": "deepseek",                                    # 仅作界面上的预设标记
        "api_url": "https://api.deepseek.com/chat/completions",    # 完整的 chat/completions 地址
        "api_key": "",                                             # 留空则回落 script.py 里的默认 Key
        "model": "deepseek-v4-flash",
        "max_tokens": 8192,
        "temperature": 0,
        "timeout": 180,
        # 关掉思维链。留空表示不发这个参数（非推理模型用不上）
        "reasoning_effort": "none",
        # 是否发 response_format={"type":"json_object"}。部分服务不支持，可关掉
        "json_mode": True,
    },
    "defaults": {
        "updatedBy": "admin",      # 更新人
        "isEnabled": 1,            # 是否启用
        "isRecommended": 0,        # 是否推荐
        "accessCount": 0,          # 访问次数
        "checkStatus": 0,          # 审核状态（取值含义如有要求请告知，默认 0）
        "type": None,              # 类型（表中取值含义未明，默认留空）
        "industryId": 0,           # 行业ID（需行业表映射，默认 0）
    },
}


def load_config():
    """读取配置；文件不存在或损坏时返回默认模板。"""
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            # 浅合并：缺省字段回退到默认值
            for section, kv in DEFAULT_CONFIG.items():
                if isinstance(kv, dict) and isinstance(data.get(section), dict):
                    merged = dict(kv)
                    merged.update(data[section])
                    cfg[section] = merged
                elif section in data:
                    cfg[section] = data[section]
        except Exception:
            pass
    return cfg


def save_config(cfg):
    """保存配置到 server_config.json。"""
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# 供应商预设：全是 OpenAI 兼容接口，换供应商只是换 URL + 模型名。
# 界面上选一个就自动填好地址，省得记；不在表里的选「自定义」手填即可。
AI_PRESETS = [
    {"id": "deepseek", "name": "DeepSeek",
     "api_url": "https://api.deepseek.com/chat/completions",
     "models": ["deepseek-v4-flash", "deepseek-v4-pro"],
     "note": "当前在用。v4-flash 是推理模型，务必保留 reasoning_effort=none，否则思维链会吃光 max_tokens"},
    {"id": "openai", "name": "OpenAI",
     "api_url": "https://api.openai.com/v1/chat/completions",
     "models": ["gpt-4o-mini", "gpt-4o", "gpt-4.1-mini"], "note": "需要能访问外网"},
    {"id": "dashscope", "name": "阿里云百炼（通义千问）",
     "api_url": "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions",
     "models": ["qwen-plus", "qwen-turbo", "qwen-max"], "note": "国内直连，兼容模式地址"},
    {"id": "moonshot", "name": "月之暗面 Kimi",
     "api_url": "https://api.moonshot.cn/v1/chat/completions",
     "models": ["moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k"],
     "note": "长上下文，适合大页面"},
    {"id": "zhipu", "name": "智谱 GLM",
     "api_url": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
     "models": ["glm-4-flash", "glm-4-plus", "glm-4-air"], "note": "glm-4-flash 免费额度大"},
    {"id": "siliconflow", "name": "硅基流动 SiliconFlow",
     "api_url": "https://api.siliconflow.cn/v1/chat/completions",
     "models": ["Qwen/Qwen2.5-72B-Instruct", "deepseek-ai/DeepSeek-V3"],
     "note": "聚合多家开源模型，模型名带厂商前缀"},
    {"id": "ark", "name": "火山方舟（豆包）",
     "api_url": "https://ark.cn-beijing.volces.com/api/v3/chat/completions",
     "models": [], "note": "模型名要填控制台里的「接入点 ID」（ep-xxxx），不是模型名"},
    {"id": "openrouter", "name": "OpenRouter",
     "api_url": "https://openrouter.ai/api/v1/chat/completions",
     "models": ["deepseek/deepseek-chat", "openai/gpt-4o-mini"], "note": "一个 Key 调多家"},
    {"id": "ollama", "name": "本地 Ollama",
     "api_url": "http://127.0.0.1:11434/v1/chat/completions",
     "models": ["qwen2.5:14b", "llama3.1:8b"],
     "note": "本机跑模型，Key 随便填；多数本地模型不支持 json_mode，建议关掉"},
    {"id": "custom", "name": "自定义", "api_url": "", "models": [],
     "note": "任何 OpenAI 兼容的 /chat/completions 地址都可以"},
]


def public_config(cfg):
    """返回给前端的配置（密钥脱敏，避免明文回传）。"""
    pub = copy.deepcopy(cfg)
    pub["db"]["password"] = "******" if cfg["db"].get("password") else ""
    pub["auth"]["password"] = "******" if cfg["auth"].get("password") else ""
    pub.setdefault("ai", {})
    pub["ai"]["api_key"] = "******" if cfg.get("ai", {}).get("api_key") else ""
    return pub
