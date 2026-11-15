"""VitaFlow UI 回归审计（交叉态矩阵）

把 design-system.md §6.10–§6.19 十一轮收口的结论变成可复跑的断言。
此前这些检查散落在临时目录，口径还会漏宽度带（§6.19：800 视口才暴露
模型名截断），故合并为一个自包含脚本。

用法（在仓库根目录）：
    VITAFLOW_TOKEN=<jwt> backend/.venv/Scripts/python.exe scripts/audit/regression.py
    VITAFLOW_TOKEN=<jwt> npm --prefix frontend run audit:ui -- --quick

环境变量：
    VITAFLOW_TOKEN      必需。登录态 JWT，只写入 localStorage，不落盘
    VITAFLOW_BASE_URL   可选，默认 http://127.0.0.1:5173（需先起 dev server）
参数：
    --quick   只跑「最窄容器 × 暗色」子集（8 格），约 20 秒
    --json    以 JSON 输出每格指标，便于 CI 存档/对比
退出码：0 全绿；1 有断言失败或运行错误；2 缺少依赖/配置

依赖 playwright（backend/.venv 内已装）。不依赖 PIL 等图像库。
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

from playwright.sync_api import sync_playwright  # noqa: E402

BASE = os.environ.get("VITAFLOW_BASE_URL", "http://127.0.0.1:5173").rstrip("/")
TOKEN = os.environ.get("VITAFLOW_TOKEN", "").strip()
ARGS = sys.argv[1:]
QUICK = "--quick" in ARGS
AS_JSON = "--json" in ARGS

ROUTES = [
    ("/workspace/info", "info"),       # 横滚 chips 提示
    ("/workspace/admin", "admin"),     # 容器查询统计卡 + 模型名
    ("/workspace/79", "editor"),       # 三栏 / 单栏切换
    ("/workspace/resumes", "resumes"), # 卡片标题自定义气泡
    ("/workspace/help", "help"),       # 导航型横滚条
]
FULL_CASES = [("light", 640, 700), ("dark", 640, 700),
              ("light", 800, 600), ("dark", 800, 600),
              ("light", 1280, 820), ("dark", 1280, 820)]
QUICK_CASES = [("dark", 640, 700), ("dark", 800, 600)]
CASES = QUICK_CASES if QUICK else FULL_CASES
ROUTE_SET = ROUTES[:4] if QUICK else ROUTES

# ---------------------------------------------------------------------------
# 每格在浏览器里跑一次，返回指标。断言用「状态是否自洽」，不硬编码像素：
#   - 横滚提示：有溢出才显示右箭头，不溢出必须隐藏
#   - 紧凑模式：主区 <900 走单栏（底部分段 + 汉堡），否则三栏
#   - 容器查询：容器 >=672 排 4 列，否则 2 列
# ---------------------------------------------------------------------------
JS = r"""() => {
  const vw = document.documentElement.clientWidth;
  const theme = document.documentElement.dataset.theme || '(none)';

  const scrollableX = (el) => {
    let n = el.parentElement;
    while (n && n !== document.body) {
      const cs = getComputedStyle(n);
      if (/auto|scroll/.test(cs.overflowX) && n.scrollWidth > n.clientWidth + 1) return n;
      n = n.parentElement;
    }
    return null;
  };
  const clippedByAncestor = (el, r) => {
    let n = el.parentElement;
    while (n && n !== document.documentElement) {
      const cs = getComputedStyle(n);
      if (cs.overflowX === "hidden" || cs.overflowY === "hidden") {
        const b = n.getBoundingClientRect();
        if (r.right <= b.left + 1 || r.left >= b.right - 1) return true;
      }
      n = n.parentElement;
    }
    return false;
  };

  // 1) 横向不可达控件（竖直方向、被祖先裁掉、可横滚到达都排除）
  const unreachable = [];
  const controls = [...document.querySelectorAll(
    'button, a[href], input, select, textarea, [role="button"], [role="tab"], [role="switch"]')];
  for (const el of controls) {
    const cs = getComputedStyle(el);
    if (cs.visibility === "hidden" || cs.display === "none" || cs.pointerEvents === "none") continue;
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) continue;
    if (r.left < vw && r.right > 0) continue;
    if (clippedByAncestor(el, r) || scrollableX(el)) continue;
    unreachable.push((el.textContent || el.getAttribute("aria-label") || "").trim().slice(0, 16));
  }

  // 2) 无出口的文本裁剪；自定义气泡（v-if + hover 才渲染）按容器特征豁免
  const hasOwnText = (el) => {
    for (const n of el.childNodes) if (n.nodeType === 3 && n.textContent.trim()) return true;
    return false;
  };
  const hint = (el) => {
    if (el.parentElement && String(el.parentElement.className).includes("group/title")) return true;
    let n = el;
    for (let i = 0; i < 4 && n; i++) {
      if (n.getAttribute && (n.getAttribute("title") || n.getAttribute("aria-label"))) return true;
      n = n.parentElement;
    }
    return false;
  };
  const clipped = [];
  for (const el of document.querySelectorAll("body *")) {
    if (!hasOwnText(el)) continue;
    const cs = getComputedStyle(el);
    if (cs.visibility === "hidden" || cs.display === "none" || cs.opacity === "0") continue;
    if (el.closest('[aria-hidden="true"]')) continue;
    const cx = (cs.overflowX === "hidden" || cs.overflowX === "clip") && el.scrollWidth > el.clientWidth + 1;
    const cy = (cs.overflowY === "hidden" || cs.overflowY === "clip") && el.scrollHeight > el.clientHeight + 1;
    if (!cx && !cy) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 8 || r.height < 8 || hint(el)) continue;
    clipped.push(el.textContent.trim().slice(0, 16));
  }

  // 3) 选区基线：控件不可选，正文仍可选（§6.17 双向判定）
  // 表单控件（input/textarea/select）本来就允许选中文字，不进这条基线，
  // 否则会把「能输入」误报成「控件可选中」。
  const visibleControls = [...document.querySelectorAll(
    'button, a[href], [role="button"], [role="tab"], [role="menuitem"]')].filter((e) => {
    const cs = getComputedStyle(e), r = e.getBoundingClientRect();
    return cs.display !== "none" && cs.visibility !== "hidden" && r.width > 0 && r.height > 0
      && (e.textContent || "").trim().length > 1;
  });
  const selectable = visibleControls.filter((e) => getComputedStyle(e).userSelect !== "none")
    .map((e) => (e.textContent || "").trim().slice(0, 14));
  const paragraphs = [...document.querySelectorAll("p")].filter((p) => p.textContent.trim().length > 60);
  const paragraphsSelectable = paragraphs.filter((p) => getComputedStyle(p).userSelect !== "none").length;

  // 4) 各轮新增控件的状态自洽性
  const sh = document.querySelector("[data-sh]");
  const wrap = sh ? sh.parentElement : null;
  const arrow = wrap ? wrap.querySelector(".wb-sh-arrow-right") : null;
  const arrowCS = arrow ? getComputedStyle(arrow) : null;
  const shOver = sh ? sh.scrollWidth - sh.clientWidth : null;

  const ws = document.querySelector(".workspace-surface");
  const wsW = ws ? Math.round(ws.getBoundingClientRect().width) : null;
  const prev = ws ? [...ws.children].find((e) => String(e.className).includes("bg-zinc-100/50")) : null;
  const bottomNav = [...document.querySelectorAll("div")].find((e) => String(e.className).includes("bottom-2.5"));
  const gridHost = [...document.querySelectorAll("[class*='@container']")].map((e) => e.firstElementChild).find(Boolean);
  const containerW = gridHost ? Math.round(gridHost.parentElement.getBoundingClientRect().width) : null;

  return {
    theme,
    hscroll: document.documentElement.scrollWidth > vw,
    unreachable, clipped, selectable,
    paragraphs: paragraphs.length, paragraphsSelectable,
    sh: sh ? { left: sh.dataset.shLeft, right: sh.dataset.shRight, over: shOver } : null,
    arrowOpacity: arrowCS ? arrowCS.opacity : null,
    arrowBg: arrowCS ? arrowCS.backgroundColor : null,
    wsW,
    prevDisplay: prev ? getComputedStyle(prev).display : null,
    bottomNav: bottomNav ? getComputedStyle(bottomNav).display !== "none" : false,
    hamburger: !![...document.querySelectorAll("button")].find((b) => b.getAttribute("aria-label") === "Toggle Mobile Menu"),
    containerW,
    gridCols: gridHost ? getComputedStyle(gridHost).gridTemplateColumns.split(" ").length : null,
  };
}"""


def assert_cell(theme: str, width: int, route: str, r: dict) -> list[str]:
    """返回该格的断言失败列表。"""
    bad = []
    if r["theme"] != theme:
        bad.append(f"主题未生效: {r['theme']}")
    if r["hscroll"]:
        bad.append("页面横向滚动")
    if r["unreachable"]:
        bad.append(f"横向不可达控件 {len(r['unreachable'])} {r['unreachable'][:2]}")
    if r["clipped"]:
        bad.append(f"无出口文本裁剪 {len(r['clipped'])} {r['clipped'][:2]}")
    if r["selectable"]:
        bad.append(f"可选中交互控件 {len(r['selectable'])} {r['selectable'][:2]}")
    if r["paragraphs"] and r["paragraphsSelectable"] != r["paragraphs"]:
        bad.append(f"正文段落误伤 {r['paragraphsSelectable']}/{r['paragraphs']}")

    # 横滚提示：溢出才有右箭头，不溢出必须隐藏
    sh = r["sh"]
    if sh is not None and r["arrowOpacity"] is not None:
        want = "1" if sh["over"] > 1 else "0"
        if r["arrowOpacity"] != want:
            bad.append(f"横滚箭头 opacity={r['arrowOpacity']}，溢出 {sh['over']} 应为 {want}")
        if sh["over"] > 1 and sh["right"] != "1":
            bad.append(f"有溢出但 data-sh-right={sh['right']}")

    # 编辑器：主区 <900 走单栏，>=900 三栏
    if r["wsW"] is not None:
        compact = r["wsW"] < 900
        if compact and not (r["bottomNav"] and r["hamburger"]):
            bad.append(f"主区 {r['wsW']} 未进入紧凑模式")
        if not compact and (r["bottomNav"] or r["hamburger"]):
            bad.append(f"主区 {r['wsW']} 不该是紧凑模式")
        if (r["prevDisplay"] == "none") != compact:
            bad.append(f"预览区显隐与模式不符: {r['prevDisplay']}")

    # 统计卡：容器 >=672 排 4 列，否则 2 列
    if r["gridCols"] is not None and r["containerW"] is not None:
        want = 4 if r["containerW"] >= 672 else 2
        if r["gridCols"] != want:
            bad.append(f"容器 {r['containerW']} 列数 {r['gridCols']}，应为 {want}")
    return bad


def main() -> int:
    if not TOKEN:
        print("缺少 VITAFLOW_TOKEN。请设置登录态 JWT（只写 localStorage，不落盘）：\n"
              "  Windows : $env:VITAFLOW_TOKEN='<jwt>'; npm --prefix frontend run audit:ui\n"
              "  POSIX   : VITAFLOW_TOKEN=<jwt> npm --prefix frontend run audit:ui")
        return 2
    try:
        urllib.request.urlopen(BASE, timeout=4)
    except (urllib.error.URLError, OSError) as e:
        print(f"dev server 不可达: {BASE} ({e})\n请先启动 frontend 的 vite dev server。")
        return 2

    mode = "quick" if QUICK else "full"
    if not AS_JSON:
        print(f"VitaFlow UI 回归审计  base={BASE}  mode={mode}  "
              f"矩阵={len(CASES)}×{len(ROUTE_SET)}={len(CASES) * len(ROUTE_SET)} 格")

    rows, failures = [], []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        for theme, width, height in CASES:
            ctx = browser.new_context(viewport={"width": width, "height": height})
            ctx.add_init_script(
                "localStorage.setItem('vitaflow_token', " + json.dumps(TOKEN) + ");"
                "localStorage.setItem('vitaflow-theme', " + json.dumps(theme) + ");"
            )
            page = ctx.new_page()
            for url, name in ROUTE_SET:
                page.goto(BASE + url, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(1300)
                r = page.evaluate(JS)
                bad = assert_cell(theme, width, name, r)
                rows.append({"theme": theme, "viewport": f"{width}x{height}", "route": name,
                             **r, "failures": bad})
                if bad:
                    failures.append((theme, width, name, bad))
                if not AS_JSON:
                    mark = "✓" if not bad else "✗"
                    detail = (f"sh={r['sh']['over'] if r['sh'] else '-'} "
                              f"ws={r['wsW'] or '-'} cols={r['gridCols'] or '-'}")
                    print(f"  {theme:5} {width:>4} {name:8} {mark}  {detail}")
                    for msg in bad:
                        print(f"        ⚠ {msg}")
            ctx.close()

        # 运行时主题切换（不刷新页面）：CSS 变量与指令状态必须稳定
        runtime_bad = []
        ctx = browser.new_context(viewport={"width": 640, "height": 700})
        ctx.add_init_script(
            "localStorage.setItem('vitaflow_token', " + json.dumps(TOKEN) + ");"
            "localStorage.setItem('vitaflow-theme', 'light');"
        )
        page = ctx.new_page()
        page.goto(BASE + "/workspace/info", wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(1800)
        before = page.evaluate(JS)
        page.evaluate("() => { document.documentElement.dataset.theme = 'dark'; "
                      "document.documentElement.style.colorScheme = 'dark'; }")
        page.wait_for_timeout(700)
        after = page.evaluate(JS)
        if after["theme"] != "dark":
            runtime_bad.append("dataset.theme 未切换")
        if before["arrowBg"] == after["arrowBg"]:
            runtime_bad.append("箭头配色未跟随主题")
        if before["sh"] != after["sh"]:
            runtime_bad.append("横滚提示状态被主题切换改变")
        if after["hscroll"] or after["unreachable"] or after["clipped"]:
            runtime_bad.append("切换后出现横滚/不可达/裁剪")
        ctx.close()
        browser.close()

    if AS_JSON:
        print(json.dumps({"base": BASE, "mode": mode, "cells": rows,
                          "runtime_failures": runtime_bad,
                          "failed_cells": len(failures)}, ensure_ascii=False, indent=2))
    else:
        print("─" * 62)
        print(f"运行时 light→dark（不刷新）: {'✓ 稳定' if not runtime_bad else '✗ ' + '; '.join(runtime_bad)}")
        print(f"合计 {len(rows)} 格 · 断言失败 {len(failures)} 格"
              + ("" if not runtime_bad else " · 运行时切换失败"))
    return 1 if (failures or runtime_bad) else 0


if __name__ == "__main__":
    sys.exit(main())
