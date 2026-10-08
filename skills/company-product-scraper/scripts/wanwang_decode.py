# -*- coding: utf-8 -*-
"""万网/wezhan JS 加速站解码：取骨架 -> 找 Body.js -> 解码 document.write(\\uXXXX) -> 输出 HTML。
用法:
  python3 wanwang_decode.py <页面URL> [-o 输出.html]
说明: 若 requests 拿到的是 ~900B 骨架且含 'Body.js'/'wanwang'，即属此类站。
      数据(面包屑/主图/表格)通常已烘焙进 Body.js；纯运行时注入的内容(如某些参数)需改用 render.py 渲染。
"""
import re, sys, argparse, requests
UA = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                    '(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'}

def body_js_url(page_url):
    r = requests.get(page_url, headers=UA, timeout=25)
    m = re.search(r'''src=['"]([^'"]*Body\.js[^'"]*)['"]''', r.text)
    if not m: return None, r.text
    u = m.group(1)
    if u.startswith('//'): u = 'https:' + u
    return u, r.text

def decode(js_text):
    m = re.search(r"document\.write\('(.+)'\)", js_text, re.DOTALL)
    if not m: return ""
    h = m.group(1)
    h = re.sub(r'\\u([0-9a-fA-F]{4})', lambda x: chr(int(x.group(1), 16)), h)
    return (h.replace(r'\r\n', '\n').replace(r'\n', '\n')
              .replace(r'\"', '"').replace(r"\'", "'").replace('\\/', '/'))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url"); ap.add_argument("-o", "--out", default=None)
    a = ap.parse_args()
    bj, skel = body_js_url(a.url)
    if not bj:
        print("未找到 Body.js —— 可能不是万网站，或需渲染。骨架前200:", skel[:200]); return
    print("Body.js:", bj)
    html = decode(requests.get(bj, headers=UA, timeout=25).text)
    print("解码HTML长度:", len(html))
    if a.out:
        open(a.out, "w", encoding="utf-8").write(html); print("写出:", a.out)
    else:
        sys.stdout.write(html[:2000])

if __name__ == "__main__":
    main()
