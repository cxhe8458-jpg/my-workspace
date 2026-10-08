# -*- coding: utf-8 -*-
"""无头浏览器渲染（playwright/chromium）：用于参数/内容靠运行时 JS/XHR 注入、静态 HTML 拿不到的站。
渲染后输出：完整 HTML、加载的 sitefiles/内容图、XHR/fetch 请求(找参数 API 用)。

准备:
  python3 -m pip install --break-system-packages playwright
  python3 -m playwright install chromium      # MS CDN 通常可下(pyppeteer 的 google CDN 常被墙)

用法:
  python3 render.py <URL> [-o 输出.html]
"""
import asyncio, sys, argparse
from playwright.async_api import async_playwright
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36")

async def run(url, out):
    async with async_playwright() as p:
        b = await p.chromium.launch(args=['--no-sandbox'])
        ctx = await b.new_context(user_agent=UA, viewport={'width': 1440, 'height': 3000})
        pg = await ctx.new_page()
        reqs = []
        pg.on('requestfinished', lambda r: reqs.append((r.resource_type, r.url)))
        await pg.goto(url, wait_until='networkidle', timeout=60000)
        for y in range(0, 3000, 500):           # 触发懒加载/滑块
            await pg.evaluate(f'window.scrollTo(0,{y})'); await asyncio.sleep(0.3)
        await asyncio.sleep(2)
        html = await pg.content()
        if out: open(out, "w", encoding="utf-8").write(html)
        ntbl = await pg.eval_on_selector_all('table', 'e=>e.length')
        print(f"HTML长度={len(html)}  <table>数={ntbl}  请求数={len(reqs)}")
        print("\n=== 内容图(sitefiles/contents) ===")
        for rt, u in reqs:
            if 'sitefiles' in u or '/contents/' in u:
                print("  ", u.split('/')[-1])
        print("\n=== XHR/fetch(找参数API) ===")
        for rt, u in reqs:
            if rt in ('xhr', 'fetch') or any(k in u.lower() for k in ('api', 'ajax', 'detail', 'apiv', 'getdata')):
                print(f"  [{rt}] {u[:120]}")
        if out: print("\n写出:", out)
        await b.close()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url"); ap.add_argument("-o", "--out", default=None)
    a = ap.parse_args()
    asyncio.run(run(a.url, a.out))

if __name__ == "__main__":
    main()
