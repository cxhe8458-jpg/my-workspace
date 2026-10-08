# -*- coding: utf-8 -*-
"""中英互译：复用 script.py 的 DeepSeek 接口。

deepseek_chat 强制 response_format=json_object，因此翻译统一走
{"result":"译文"} 结构，再取 result。
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import script  # noqa: E402


def _has_cjk(s):
    return any("\u4e00" <= c <= "\u9fff" for c in s)


def translate_text(text, target_lang):
    """把 text 翻译成 target_lang（"zh" 或 "en"）；失败/为空返回 ""。

    复用 script.deepseek_chat，自带重试（MAX_RETRIES）与 JSON 抢救。
    """
    if not text:
        return ""
    text = str(text).strip()
    if not text:
        return ""
    if target_lang == "zh":
        sys_p = "你是资深专业翻译，把内容准确翻译成简体中文，只输出译文，不要解释、不要加引号。"
        hint = "简体中文"
    else:
        sys_p = "你是资深专业翻译，把内容准确翻译成英文，只输出译文，不要解释、不要加引号。"
        hint = "英文"
    user_p = '请把下面内容翻译成%s，只输出 JSON：{"result":"译文"}\n原文：%s' % (hint, text)
    try:
        r = script.deepseek_chat(sys_p, user_p)
        if r and isinstance(r, dict) and r.get("result"):
            out = str(r["result"]).strip()
            # 模型偶尔会带上外层引号，去掉
            if len(out) >= 2 and out[0] == out[-1] and out[0] in "\"'":
                out = out[1:-1]
            return out
    except Exception:
        pass
    return ""
