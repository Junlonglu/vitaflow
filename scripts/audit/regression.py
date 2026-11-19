"""ResumeFlow UI 回归审计（交叉态矩阵）

把 design-system.md §6.10–§6.22 各轮收口的结论变成可复跑的断言。
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
import re
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

  // 3) 字体栈（§6.22）：body / --font-sans / --wb-font-sans 必须同源，
  //    且逐字是 WorkBuddy 系统栈。曾经 --font-sans 打头的 Inter 从未生效。
  // 浏览器计算值会剥掉中文字体名的引号（"微软雅黑" → 微软雅黑），
  // 自定义属性则保留字面量；比较前统一去引号 + 压空白，否则会假失败。
  const norm = (s) => s.replace(/["']/g, "").replace(/\s+/g, " ").trim();
  const rootCS = getComputedStyle(document.documentElement);
  const font = {
    body: norm(getComputedStyle(document.body).fontFamily),
    sans: norm(rootCS.getPropertyValue("--font-sans")),
    src: norm(rootCS.getPropertyValue("--wb-font-sans")),
    mono: norm(rootCS.getPropertyValue("--font-mono")),
  };

  // 4) 选区基线：控件不可选，正文仍可选（§6.17 双向判定）
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

  // 5) 各轮新增控件的状态自洽性
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
  const gridCls = gridHost ? String(gridHost.className) : null;

  // 6) 侧栏轨位（§6.23）：父行 / 会话行 / 查看更多 / 新对话的文字必须同落 40 轨，
  //    父子两行时间戳右缘同列（父行常驻展开箭头由会话行右侧占位抵消）。
  //    只有展开态才量得到会话行，其余格子跳过这条而不是硬报缺失。
  const aside = document.querySelector('aside');
  const srow = aside ? aside.querySelector('[data-session-row]') : null;
  let sidebar = null;
  if (aside && srow) {
    const L = (el) => el ? el.getBoundingClientRect().left - aside.getBoundingClientRect().left : null;
    const R = (el) => el ? el.getBoundingClientRect().right - aside.getBoundingClientRect().left : null;
    const pbtn = aside.querySelector('div.group > button');
    const more = aside.querySelector('[data-session-more]');
    // 「新建对话」已是父行上的图标按钮（无文字轨），矩阵这里只量有文字的轨位；
    // 它的位置/显隐由 check_session_folding 单独验。
    sidebar = {
      parentText: L(pbtn?.querySelector('span.truncate')),
      parentTimeRight: R(aside.querySelector('div.group span.tabular-nums')),
      sessionText: L(srow.querySelector('span.truncate')),
      sessionTimeRight: R(srow.querySelector('span.tabular-nums')),
      moreText: more ? L(more.querySelector('span')) : null,
    };
  }

  return {
    theme, font, sidebar,
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
    gridCls,
    gridCols: gridHost ? getComputedStyle(gridHost).gridTemplateColumns.split(" ").length : null,
  };
}"""


# Tailwind v4 容器查询断点（@container 下的 @sm/@2xl/@3xl… 按容器宽度生效）
CONTAINER_BREAKPOINTS = {
    "3xs": 256, "2xs": 288, "xs": 320, "sm": 384, "md": 448, "lg": 512,
    "xl": 576, "2xl": 672, "3xl": 768, "4xl": 896, "5xl": 1024, "6xl": 1152, "7xl": 1280,
}


def expected_grid_cols(class_name: str, container_width: int) -> int:
    """从 `grid-cols-2 @3xl:grid-cols-5` 这类类名算出该宽度下应有的列数。"""
    base = re.search(r"(?:^|\s)grid-cols-(\d+)(?:\s|$)", class_name)
    want = int(base.group(1)) if base else 1
    best = -1
    for name, cols in re.findall(r"@([0-9a-z]+):grid-cols-(\d+)", class_name):
        bp = CONTAINER_BREAKPOINTS.get(name)
        if bp is not None and container_width >= bp and bp > best:
            best, want = bp, int(cols)
    return want


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

    # 字体栈：单一来源 + 逐字对齐 WorkBuddy（§6.22）
    f = r["font"]
    if f["sans"] != f["src"] or f["sans"] != f["body"]:
        bad.append("字体栈未接单一来源（--font-sans / --wb-font-sans / body 不一致）")
    if "Inter" in f["body"] or "-apple-system" not in f["body"] or "Microsoft YaHei" not in f["body"]:
        bad.append(f"字体栈偏离 WorkBuddy: {f['body'][:72]}")
    if "Consolas" not in f["mono"]:
        bad.append(f"等宽栈偏离 WorkBuddy: {f['mono'][:72]}")

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

    # 统计卡：期望列数从元素自己的类名推导（各页档位不同），而不是写死 4 列——
    # 看板 KPI 条是 @3xl:grid-cols-5、成本页是 @3xl:grid-cols-4、增长页是 @2xl:3 @4xl:6。
    if r["gridCols"] is not None and r["containerW"] is not None and r["gridCls"]:
        want = expected_grid_cols(r["gridCls"], r["containerW"])
        if r["gridCols"] != want:
            bad.append(f"容器 {r['containerW']} 列数 {r['gridCols']}，应为 {want}（{r['gridCls']}）")

    # 侧栏轨位（§6.23）：文字同一竖线、父子时间戳右缘同一列
    sb = r["sidebar"]
    if sb is not None:
        texts = [v for k, v in sb.items() if k.endswith("Text") and v is not None]
        if texts and max(texts) - min(texts) > 1:
            bad.append(f"侧栏文字轨不齐: {texts}")
        if sb["parentTimeRight"] is not None and sb["sessionTimeRight"] is not None \
                and abs(sb["parentTimeRight"] - sb["sessionTimeRight"]) > 2:
            bad.append(f"父子时间戳右缘不齐: {sb['parentTimeRight']} / {sb['sessionTimeRight']}")
    return bad


# ---------------------------------------------------------------------------
# 矩阵之外的独立检查：审阅台（§6.48）
# 只有存在 pending 提案时这块 UI 才出现，矩阵里永远拿不到。这里先取一份真实
# 简历数据改成「有改动」的版本，再拦下会话 / 消息两个接口把提案喂进去，
# 于是能验证：一次列全部模块、修改前只读、微调的值进预览请求、返回编辑有回收口。
# ---------------------------------------------------------------------------
def _review_resume_id() -> int | None:
    """真实存在的简历 id：矩阵里的 /workspace/79 是历史 id（只验外壳），不能拿来造提案。"""
    req = urllib.request.Request(f"{BASE}/api/resumes", headers={"Authorization": f"Bearer {TOKEN}"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    items = ((payload or {}).get("data") or {}).get("items") or []
    return items[0]["id"] if items else None


def _review_fixture(resume_id: int) -> tuple[dict, list, list] | None:
    """取真实简历 → 造提案 → 返回 (optimized_data, sessions, messages)。"""
    req = urllib.request.Request(
        f"{BASE}/api/resumes/{resume_id}",
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    data = ((payload or {}).get("data") or {}).get("resume_data")
    if not isinstance(data, dict):
        return None

    nxt = json.loads(json.dumps(data, ensure_ascii=False))
    basics = nxt.setdefault("basics", {})
    basics["title"] = f"{basics.get('title') or '工程师'}（审阅台回归）"

    titles = nxt.setdefault("layout", {}).setdefault("section_titles", {})
    list_key = next((k for k in ("projects", "work", "education") if isinstance(nxt.get(k), list) and nxt.get(k)), None)
    if not list_key:
        return None
    titles[list_key] = f"{titles.get(list_key) or list_key}（改）"
    nxt[list_key].append(
        {
            "name": "审阅台回归条目",
            "role": "验证",
            "start_date": "2026.01",
            "description": "用于验证「一次看全部模块」的呈现。",
            "highlights": ["一条亮点"],
        }
    )

    sessions = [{
        "id": 9901, "resume_id": resume_id, "title": "审计：按 JD 改一版",
        "message_count": 2, "last_message": "", "is_pinned": False,
        "create_time": "2026-11-18 10:00:00", "update_time": "2026-11-18 10:00:00",
    }]
    messages = [
        {"id": 9902, "role": "user", "content": "按 JD 改一版", "create_time": "2026-11-18 10:00:00"},
        {"id": 9903, "role": "assistant", "content": "改好了，逐条看下。", "action_status": "pending",
         "optimized_resume_data": nxt, "create_time": "2026-11-18 10:01:00"},
    ]
    return nxt, sessions, messages


def check_splitter(browser) -> list[str]:
    """面板分隔条（§6.51）：一条 1px 常驻细线、没有可见把手；悬停变色 + 拖拽仍改宽度。"""
    bad: list[str] = []
    resume_id = _review_resume_id()
    if resume_id is None:
        return ["分隔条检查跳过：当前账号没有简历"]
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    ctx.add_init_script(
        "localStorage.setItem('vitaflow_token', " + json.dumps(TOKEN) + ");"
        "localStorage.setItem('vitaflow-theme', 'light');"
    )
    page = ctx.new_page()
    SPLIT = "[data-panel-splitter]"
    LINE = "[data-splitter-line]"
    try:
        page.goto(f"{BASE}/workspace/{resume_id}", wait_until="domcontentloaded", timeout=45000)
        page.wait_for_selector(SPLIT, timeout=20000)
        page.wait_for_timeout(1400)
        page.mouse.move(8, 8)
        page.wait_for_timeout(220)

        m = page.evaluate(
            """() => {
          const el = document.querySelector('[data-panel-splitter]');
          const line = el.querySelector('[data-splitter-line]');
          const r = el.getBoundingClientRect(), lr = line.getBoundingClientRect();
          return {
            hitW: +r.width.toFixed(1), kids: el.children.length,
            lineW: +lr.width.toFixed(1), lineGap: +(r.right - lr.right).toFixed(1),
            lineH: +lr.height.toFixed(1), panelH: +r.height.toFixed(1),
            cursor: getComputedStyle(el).cursor,
            lineBg: getComputedStyle(line).backgroundColor,
            hitBg: getComputedStyle(el).backgroundColor,
            mask: getComputedStyle(line).maskImage || getComputedStyle(line).webkitMaskImage || "",
          };
        }"""
        )
        if m["kids"] != 1:
            bad.append(f"分隔条只应有 1px 细线一个子元素，实得 {m['kids']} 个（可见把手回来了？）")
        if abs(m["hitW"] - 16) > 1:
            bad.append(f"命中区可见宽度应为 16px（::after 再外扩到 24），实得 {m['hitW']}px")
        if abs(m["lineW"] - 1) > 0.4:
            bad.append(f"分隔线宽度应为 1px，实得 {m['lineW']}px")
        if abs(m["lineGap"]) > 0.4:
            bad.append(f"分隔线应贴命中区右缘，实际距右缘 {m['lineGap']}px")
        if abs(m["lineH"] - m["panelH"]) > 1:
            bad.append(f"分隔线未贯穿高度（{m['lineH']} vs {m['panelH']}）")
        if m["cursor"] != "col-resize":
            bad.append(f"命中区 cursor 应为 col-resize，实得 {m['cursor']}")
        if m["lineBg"] == m["hitBg"]:
            bad.append("分隔线与命中区底色相同，等于不可见")
        # 形态：两端淡出、中段最实（照 WorkBuddy 的分隔线，§6.51）
        if "linear-gradient" not in m["mask"]:
            bad.append(f"分隔线缺少渐隐遮罩，两端不会淡出（mask={m['mask'] or 'none'}）")
        elif "rgba(0, 0, 0, 0)" not in m["mask"]:
            bad.append(f"分隔线遮罩缺少透明端点，等于没渐变（mask={m['mask']}）")

        # 1px 线没有把手，必须靠悬停变色才能被发现
        page.hover(SPLIT)
        page.wait_for_timeout(280)
        hover_bg = page.evaluate(f"() => getComputedStyle(document.querySelector('{LINE}')).backgroundColor")
        page.mouse.move(8, 8)
        page.wait_for_timeout(200)
        if hover_bg == m["lineBg"]:
            bad.append("悬停时分隔线不变色，拖拽入口不可发现")

        # 拖拽仍然生效，且收尾要还原 body 选区锁
        box = page.locator(SPLIT).bounding_box()
        before = page.evaluate(
            "() => document.querySelector('.workspace-surface').firstElementChild.getBoundingClientRect().width"
        )
        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        page.mouse.down()
        page.mouse.move(box["x"] + box["width"] / 2 - 90, box["y"] + box["height"] / 2, steps=6)
        page.mouse.up()
        page.wait_for_timeout(260)
        after = page.evaluate(
            "() => document.querySelector('.workspace-surface').firstElementChild.getBoundingClientRect().width"
        )
        if abs((before - after) - 90) > 8:
            bad.append(f"拖拽应把左栏收窄约 90px，实得 {round(before)} → {round(after)}")
        if page.evaluate("() => document.body.style.userSelect"):
            bad.append("拖拽结束后 body.userSelect 未还原")

        # 暗色：线要换色，且不能和命中区底色溶在一起
        page.evaluate("() => { document.documentElement.dataset.theme = 'dark'; }")
        page.wait_for_timeout(340)
        dark = page.evaluate(
            """() => {
          const el = document.querySelector('[data-panel-splitter]');
          const line = el.querySelector('[data-splitter-line]');
          return {line: getComputedStyle(line).backgroundColor, hit: getComputedStyle(el).backgroundColor};
        }"""
        )
        if dark["line"] == m["lineBg"]:
            bad.append("暗色下分隔线颜色没跟随主题")
        if dark["line"] == dark["hit"]:
            bad.append("暗色下分隔线与命中区底色相同，等于不可见")
    except Exception as e:  # noqa: BLE001 - 审计失败要把原因带回报告
        bad.append(f"分隔条检查异常: {e}")
    finally:
        ctx.close()
    return bad


def check_review_board(browser) -> list[str]:
    bad: list[str] = []
    try:
        resume_id = _review_resume_id()
        if resume_id is None:
            if not AS_JSON:
                print("  审阅台检查跳过：当前账号没有简历")
            return bad
        fixture = _review_fixture(resume_id)
    except Exception as e:  # noqa: BLE001
        return [f"造审阅提案失败: {type(e).__name__}: {e}"]
    if not fixture:
        return [f"简历 {resume_id} 没有可造提案的列表模块"]

    _, sessions, messages = fixture

    def envelope(data):
        return json.dumps({"code": 200, "message": "success", "data": data}, ensure_ascii=False)

    previews: list[str] = []
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    ctx.add_init_script(
        "localStorage.setItem('vitaflow_token', " + json.dumps(TOKEN) + ");"
        "localStorage.setItem('vitaflow-theme', 'light');"
    )
    page = ctx.new_page()
    try:
        page.route(
            re.compile(rf"/api/ai/resume-chat/{resume_id}/sessions$"),
            lambda route: route.fulfill(status=200, content_type="application/json", body=envelope(sessions)),
        )
        page.route(
            re.compile(rf"/api/ai/resume-chat/{resume_id}/messages"),
            lambda route: route.fulfill(status=200, content_type="application/json", body=envelope(messages)),
        )

        def capture(route):
            previews.append(route.request.post_data or "")
            route.continue_()

        page.route(re.compile(rf"/api/resumes/{resume_id}/preview-html$"), capture)

        page.goto(f"{BASE}/workspace/{resume_id}", wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(1600)
        page.get_by_role("button", name="内容编辑").first.click()
        page.wait_for_selector("[data-review-section]", timeout=15000)
        page.wait_for_timeout(500)

        sections = page.locator("[data-review-section]").count()
        cards = page.locator("[data-review-section] article").count()
        chips = page.locator("[data-review-chips] button").count()
        befores = page.get_by_text("修改前", exact=True).count()
        afters = page.get_by_text("修改后", exact=True).count()

        if sections < 2:
            bad.append(f"审阅台应一次列出多个模块，实得 {sections}")
        if cards < 2:
            bad.append(f"变更卡数量异常: {cards}")
        if chips != sections:
            bad.append(f"模块 chips({chips}) 与模块区块({sections}) 数量不一致")
        if befores != cards or afters != cards:
            bad.append(f"每张卡应同时有修改前/修改后，实得 {befores}/{afters} 对 {cards} 张")
        if page.locator("[data-review-section] article input[type=text], [data-review-section] article textarea").count():
            bad.append("修改前区块是只读的，不该出现输入控件")

        counted = page.get_by_text(re.compile(r"已选 \d+/\d+ 项")).first.inner_text()
        if not counted.startswith(f"已选 {cards}/{cards}"):
            bad.append(f"默认应全选全部变更，实得「{counted}」")

        # 每张可微调的卡都有一条「对比 / 微调」滑块，默认停在对比
        switches = page.locator("[data-review-mode]").count()
        if switches == 0 or switches % 2:
            bad.append(f"呈现方式滑块数量异常: {switches} 段")
        if page.locator('[data-review-mode="compare"][aria-pressed="true"]').count() != switches // 2:
            bad.append("滑块默认应全部停在「对比」")
        if not page.locator("[data-review-diff]").count():
            bad.append("对比态没有渲染词级 diff")

        # 微调 → 值必须进预览请求（预览、落库共用一个合并函数）
        page.locator('[data-review-section] [data-review-mode="edit"]').first.click()
        page.wait_for_timeout(300)
        if not page.locator('[data-review-mode="edit"][aria-pressed="true"]').count():
            bad.append("滑块点「微调」后没有切换过去")
        field = page.locator("[data-review-section] article input[type=text], [data-review-section] article textarea").first
        if not field.count():
            bad.append("点「微调」后没有出现字段控件")
        else:
            field.fill("审阅台回归：人工微调")
            page.wait_for_timeout(1000)
            if not any("审阅台回归：人工微调" in item for item in previews):
                bad.append("人工微调没有进入预览请求")
            if not page.get_by_text("已微调", exact=True).count():
                bad.append("人工微调后没有「已微调」标记")

            # 切回对比：字段表收起、diff 回来，且后侧基准是人工改过的文本
            page.locator('[data-review-section] [data-review-mode="compare"]').first.click()
            page.wait_for_timeout(500)
            if page.locator("[data-review-section] article input[type=text], [data-review-section] article textarea").count():
                bad.append("切回「对比」后字段控件没有收起")
            if not page.locator("[data-review-diff]").count():
                bad.append("切回「对比」后 diff 没有回来")
            if "审阅台回归：人工微调" not in page.locator("[data-review-section] article").first.inner_text():
                bad.append("切回「对比」后看不到人工微调的内容")

        # 返回编辑：表单回来，顶部留一条回审阅台的入口
        page.locator('button[title="返回编辑，建议先留着"]').click()
        page.wait_for_timeout(500)
        if not page.get_by_text(re.compile(r"还有 \d+ 项 AI 修改待确认")).count():
            bad.append("返回编辑后没有回审阅台的入口")
        if page.locator("[data-review-section]").count():
            bad.append("返回编辑后审阅台仍然占着左栏")
    except Exception as e:  # noqa: BLE001 - 审计脚本要把任何异常变成一条明确失败
        bad.append(f"审阅台检查异常: {type(e).__name__}: {e}")
    finally:
        ctx.close()
    return bad


# ---------------------------------------------------------------------------
# 矩阵之外的独立检查：侧栏轨位 + 会话折叠（§6.23）
# 矩阵各路由都没有展开态（/workspace/79 是历史 id，取不到会话），轨位断言会空跑；
# 真实接口里每份简历通常也只有一两条对话，跑不到「过多对话」这一态。
# 所以这里用 Pinia 临时往内存里注入 8 条（不落库、不改后端数据），强制验证
# 文字/图标/时间戳轨位，以及 5 条折叠 → 展开 8 条 → 收起、激活会话不被藏掉。
# ---------------------------------------------------------------------------
def check_session_folding(browser) -> list[str]:
    bad: list[str] = []
    ctx = browser.new_context(viewport={"width": 1280, "height": 820})
    ctx.add_init_script(
        "localStorage.setItem('vitaflow_token', " + json.dumps(TOKEN) + ");"
        "localStorage.setItem('vitaflow-theme', 'light');"
    )
    page = ctx.new_page()
    try:
        page.goto(BASE + "/workspace", wait_until="domcontentloaded", timeout=45000)
        page.wait_for_selector("aside", timeout=20000)
        page.wait_for_timeout(1800)
        # 先展开，让真实 fetch 完成；之后再注入才不会被接口响应覆盖回去
        exp = page.locator('aside button[aria-label="展开对话"]').first
        if exp.count():
            exp.click()
            page.wait_for_timeout(900)

        injected = page.evaluate(r"""() => {
          const app = document.querySelector('#app').__vue_app__;
          const pinia = app && app.config.globalProperties.$pinia;
          const chat = pinia && pinia._s.get('chat');
          const resume = pinia && pinia._s.get('resume');
          if (!chat || !resume || !resume.resumeList.length) return 0;
          const now = Date.now(), by = { ...chat.sessionsByResume };
          for (const r of resume.resumeList) {
            by[r.id] = Array.from({ length: 8 }, (_, i) => ({
              id: 90000 + r.id * 10 + i, resume_id: r.id, title: '审计会话 ' + (i + 1),
              message_count: 1, last_message: '摘要', is_pinned: false,
              create_time: new Date(now - (i + 1) * 3600000).toISOString(),
              update_time: new Date(now - (i + 1) * 3600000).toISOString(),
            }));
          }
          chat.sessionsByResume = by;
          return chat.sessions(resume.resumeList[0].id).length;
        }""")
        if injected != 8:
            bad.append(f"无法注入审计会话（拿到 {injected} 条），跳过折叠交互检查")
            ctx.close()
            return bad
        page.wait_for_timeout(500)

        def state():
            return page.evaluate("""() => ({
              rows: document.querySelectorAll('aside [data-session-row]').length,
              more: document.querySelector('aside [data-session-more]')?.textContent.trim() || null,
            })""")

        # 量静止态前先把指针挪开：上面点展开箭头会把那一行留在 hover 里，
        # 而「新建对话」本来就是 hover 才显形的
        page.mouse.move(4, 4)
        page.wait_for_timeout(260)

        # 轨位（§6.23）：矩阵各路由都没有展开态，这条强制检查放在这里才真的会跑
        geom = page.evaluate("""() => {
          const a = document.querySelector('aside');
          const L = (el) => el ? +(el.getBoundingClientRect().left - a.getBoundingClientRect().left).toFixed(1) : null;
          const R = (el) => el ? +(el.getBoundingClientRect().right - a.getBoundingClientRect().left).toFixed(1) : null;
          const p = a.querySelector('div.group > button');
          const s = a.querySelector('[data-session-row]');
          const m = a.querySelector('[data-session-more]');
          // 新建对话已改成父行上的图标按钮（WorkBuddy 形态）：没有文字轨，只校验
          // 「是否长在父行里 / 竖直是否收在行高内 / 右缘是否贴着展开箭头 / 静止是否隐藏」。
          const add = a.querySelector('[data-new-session]');
          const addRow = add ? add.closest('div.group') : null;
          const addRect = add ? add.getBoundingClientRect() : null;
          const rowRect = addRow ? addRow.getBoundingClientRect() : null;
          return {
            parentIcon: L(p?.querySelector('svg')), parentText: L(p?.querySelector('span.truncate')),
            parentTimeRight: R(a.querySelector('div.group span.tabular-nums')),
            sessionText: L(s?.querySelector('span.truncate')),
            sessionTimeRight: R(s?.querySelector('span.tabular-nums')),
            moreText: L(m?.querySelector('span')),
            addInsideRow: Boolean(addRow && addRow === p?.parentElement),
            addVert: addRect && rowRect
              ? { top: +(addRect.top - rowRect.top).toFixed(1), bottom: +(rowRect.bottom - addRect.bottom).toFixed(1) }
              : null,
            addRight: addRect ? +(addRect.right - a.getBoundingClientRect().left).toFixed(1) : null,
            rowRight: rowRect ? +(rowRect.right - a.getBoundingClientRect().left).toFixed(1) : null,
            addOpacity: add ? getComputedStyle(add).opacity : null,
            addTitle: add ? add.getAttribute('title') : null,
          };
        }""")
        texts = [v for k, v in geom.items() if k.endswith("Text") and v is not None]
        icons = [v for k, v in geom.items() if k.endswith("Icon") and v is not None]
        if len(texts) < 3 or max(texts) - min(texts) > 1:
            bad.append(f"侧栏文字轨不齐: {geom}")
        if len(icons) < 1 or max(icons) - min(icons) > 1:
            bad.append(f"侧栏图标轨不齐: {geom}")
        if geom["parentTimeRight"] is None or geom["sessionTimeRight"] is None \
                or abs(geom["parentTimeRight"] - geom["sessionTimeRight"]) > 2:
            bad.append(f"父子时间戳右缘不齐: {geom['parentTimeRight']} / {geom['sessionTimeRight']}")
        # 新建对话：父行内、竖直居中收在行高内、右缘贴着展开箭头（20px 槽位）、静止 opacity 0
        if not geom["addInsideRow"]:
            bad.append(f"新建对话不在父行内: {geom}")
        if not geom["addVert"] or geom["addVert"]["top"] < 0 or geom["addVert"]["bottom"] < 0:
            bad.append(f"新建对话竖直溢出父行: {geom['addVert']}")
        if geom["rowRight"] is None or geom["addRight"] is None or geom["rowRight"] - geom["addRight"] < 20:
            bad.append(f"新建对话右缘未贴住展开箭头: row={geom['rowRight']} add={geom['addRight']}")
        if geom["addOpacity"] != "0":
            bad.append(f"新建对话静止态应隐藏，实得 opacity={geom['addOpacity']}")
        if not geom["addTitle"]:
            bad.append("新建对话缺少 title（tooltip 文案）")
        elif "Ctrl" in geom["addTitle"] or "Alt" in geom["addTitle"] or "⌘" in geom["addTitle"]:
            bad.append(f"新建对话 tooltip 不应带快捷键，实得 {geom['addTitle']}")
        page.locator("aside [data-new-session]").first.hover()
        page.wait_for_timeout(260)
        add_op = page.evaluate(
            "() => getComputedStyle(document.querySelector('aside [data-new-session]')).opacity")
        if add_op != "1":
            bad.append(f"新建对话 hover 未显形，opacity={add_op}")

        # 展开/收起箭头刻意不给 tooltip（箭头语义自明，同格已被「+」占用）。
        # 不给 title 也顺带堵死「指针停住 → 浏览器弹原生白框」这条路。
        chev = page.locator('aside button[aria-label="收起对话"], aside button[aria-label="展开对话"]').first
        if not chev.count():
            bad.append("找不到展开/收起箭头（aria-label 缺失？）")
        else:
            if chev.get_attribute("title"):
                bad.append("展开箭头不应带 title：它会退回浏览器原生提示")
            chev.hover()
            page.wait_for_timeout(320)
            chev_shown = page.evaluate(
                "() => { const t = document.querySelector('#wb-tooltip'); return t ? t.dataset.shown : null; }")
            if chev_shown == "true":
                bad.append("展开箭头悬停不应弹气泡")

        s = state()
        if s["rows"] != 5 or s["more"] != "查看更多 (3)":
            bad.append(f"折叠态应为 5 行 + 查看更多 (3)，实得 {s}")
        page.locator("aside [data-session-more]").click()
        page.wait_for_timeout(350)
        s = state()
        if s["rows"] != 8 or s["more"] != "收起":
            bad.append(f"展开态应为 8 行 + 收起，实得 {s}")
        page.locator("aside [data-session-more]").click()
        page.wait_for_timeout(350)
        s = state()
        if s["rows"] != 5 or s["more"] != "查看更多 (3)":
            bad.append(f"再次收起应回到 5 行，实得 {s}")

        # 正在看的对话落在折叠段：必须强制全展开，且不给「收起」把它藏掉
        page.evaluate("""() => {
          const pinia = document.querySelector('#app').__vue_app__.config.globalProperties.$pinia;
          const chat = pinia._s.get('chat');
          // 必须与展开中的那份简历一致：keys[0] 可能是别的简历，改它不会影响当前预览
          const rid = pinia._s.get('resume').resumeList[0].id;
          const list = chat.sessionsByResume[rid];
          chat.activeByResume = { ...chat.activeByResume, [rid]: list[6].id };
        }""")
        page.wait_for_timeout(400)
        s = state()
        if s["rows"] != 8 or s["more"] is not None:
            bad.append(f"激活会话落在折叠段时应强制全展开且隐藏按钮，实得 {s}")
    except Exception as e:  # noqa: BLE001 - 审计脚本要把任何异常变成一条明确失败
        bad.append(f"折叠检查异常: {type(e).__name__}: {e}")
    finally:
        ctx.close()
    return bad


# ---------------------------------------------------------------------------
# 矩阵之外的独立检查：全站 tooltip（§6.24）
# 气泡只在悬停 / 聚焦时才存在，静态矩阵快照里量不到，所以单开一段：
# 悬停一个带 title 的按钮，量计算样式与几何，并验证 title 的摘除与恢复
# （摘除是为了压掉浏览器原生提示；恢复是 §6.14 的文本出口判据）。
# ---------------------------------------------------------------------------
def check_tooltip(browser) -> list[str]:
    bad: list[str] = []
    ctx = browser.new_context(viewport={"width": 1280, "height": 820})
    ctx.add_init_script(
        "localStorage.setItem('vitaflow_token', " + json.dumps(TOKEN) + ");"
        "localStorage.setItem('vitaflow-theme', 'light');"
    )
    page = ctx.new_page()
    try:
        page.goto(BASE + "/workspace", wait_until="domcontentloaded", timeout=45000)
        page.wait_for_selector("aside", timeout=20000)
        page.wait_for_timeout(2000)
        sel = 'aside button[data-new-session]'
        if not page.locator(sel).count():
            bad.append("找不到带 title 的按钮，无法验证 tooltip")
            return bad

        page.hover(sel)
        page.wait_for_timeout(450)
        r = page.evaluate("""() => {
          const t = document.querySelector('#wb-tooltip');
          if (!t) return null;
          const cs = getComputedStyle(t), dot = getComputedStyle(t, '::before');
          const host = document.querySelector('[aria-describedby="wb-tooltip"]');
          const tr = t.getBoundingClientRect(), hr = host ? host.getBoundingClientRect() : null;
          const pl = t.dataset.placement;
          // 三角画在气泡的哪一边取决于放置方向：放下方时朝上（border-bottom），放上方时朝下（border-top）
          const arrow = pl === 'top'
            ? { w: dot.borderLeftWidth, h: dot.borderTopWidth, c: dot.borderTopColor }
            : { w: dot.borderLeftWidth, h: dot.borderBottomWidth, c: dot.borderBottomColor };
          return { shown: t.dataset.shown, placement: pl, text: t.textContent.trim(),
                   bg: cs.backgroundColor, color: cs.color, radius: cs.borderRadius,
                   fontSize: cs.fontSize, pad: cs.padding, z: cs.zIndex,
                   arrowW: arrow.w, arrowH: arrow.h,
                   arrowColor: arrow.c,
                   gap: hr ? +((pl === 'top' ? hr.top - tr.bottom : tr.top - hr.bottom)).toFixed(1) : null,
                   hostTitle: host ? host.hasAttribute('title') : null,
                   hostDescribed: !!host };
        }""")
        if not r:
            bad.append("悬停后没有出现 #wb-tooltip")
            return bad
        if r["shown"] != "true":
            bad.append(f"气泡未显示（shown={r['shown']}）")
        if r["bg"] != "rgb(64, 64, 64)":
            bad.append(f"底色应为 #404040，实得 {r['bg']}")
        if r["color"] != "rgb(255, 255, 255)":
            bad.append(f"文字应为白色，实得 {r['color']}")
        if r["radius"] != "12px":
            bad.append(f"圆角应为 12px，实得 {r['radius']}")
        if r["fontSize"] != "13px":
            bad.append(f"字号应为 13px，实得 {r['fontSize']}")
        if r["pad"] != "10px 14px":
            bad.append(f"内边距应为 10px 14px，实得 {r['pad']}")
        # 侧栏父行的图标按钮显式指定 data-tooltip-placement="top"（下方紧贴会话行）
        if r["placement"] != "top":
            bad.append(f"显式指定 top 的元素应放上方，实得 {r['placement']}")
        if r["gap"] is None or abs(r["gap"] - 8) > 1:
            bad.append(f"气泡与目标间距应为 8，实得 {r['gap']}")
        if r["arrowW"] != "5px" or r["arrowH"] != "5px":
            bad.append(f"三角应为 10×5，实得 {r['arrowW']}×{r['arrowH']}")
        if r["arrowColor"] != "rgb(64, 64, 64)":
            bad.append(f"三角应与气泡同色，实得 {r['arrowColor']}")
        if r["hostTitle"] is not False:
            bad.append("悬停期间 title 未被摘除，会叠出浏览器原生提示")
        if not r["hostDescribed"]:
            bad.append("缺少 aria-describedby 关联")

        # 未指定方向的元素仍默认放下方（§6.24 的约定没变）：临时插一个探针按钮量一次
        page.evaluate("""() => {
          const b = document.createElement('button');
          b.id = '__tip_probe';
          b.title = '默认方向探测';
          b.textContent = 'x';
          b.style.cssText = 'position:fixed;left:640px;top:320px;width:40px;height:24px;z-index:99998';
          document.body.appendChild(b);
        }""")
        page.hover("#__tip_probe")
        page.wait_for_timeout(450)
        d = page.evaluate("""() => {
          const t = document.querySelector('#wb-tooltip');
          const host = document.querySelector('[aria-describedby="wb-tooltip"]');
          if (!t) return null;
          const tr = t.getBoundingClientRect(), hr = host ? host.getBoundingClientRect() : null;
          const pl = t.dataset.placement;
          return { placement: pl, text: t.textContent.trim(),
                   gap: hr ? +((pl === 'top' ? hr.top - tr.bottom : tr.top - hr.bottom)).toFixed(1) : null };
        }""")
        if not d:
            bad.append("探针按钮未触发出气泡")
        else:
            if d["placement"] != "bottom":
                bad.append(f"未指定方向时应默认放下方，实得 {d['placement']}")
            if d["gap"] is None or abs(d["gap"] - 8) > 1:
                bad.append(f"默认方向的间距应为 8，实得 {d['gap']}")
        # 顺序要紧：先移开指针让它按 pointerout 收起，再删探针。
        # 反过来的话元素已从 DOM 消失，pointerout 不会再来，气泡会一直挂在 shown=true。
        page.mouse.move(1240, 780)
        page.wait_for_timeout(240)
        page.evaluate("() => document.querySelector('#__tip_probe')?.remove()")

        # 离开后必须收起，并把 title 还回去
        page.mouse.move(1240, 780)
        page.wait_for_timeout(420)
        left = page.evaluate("""() => {
          const t = document.querySelector('#wb-tooltip');
          return { shown: t ? t.dataset.shown : null,
                   titleBack: !!document.querySelector('aside button[data-new-session][title="新建对话"]') };
        }""")
        if left["shown"] != "false":
            bad.append(f"离开后气泡未收起（shown={left['shown']}）")
        if not left["titleBack"]:
            bad.append("离开后 title 未恢复，文本出口会断（§6.14）")

        # 键盘聚焦也要出气泡，否则键盘用户只剩读屏一条路
        page.evaluate("""() => {
          const b = document.querySelector('aside button[data-new-session]');
          b && b.focus();
        }""")
        page.wait_for_timeout(280)
        focus = page.evaluate("""() => {
          const t = document.querySelector('#wb-tooltip');
          return t ? t.dataset.shown : null;
        }""")
        if focus != "true":
            bad.append(f"键盘聚焦未弹出气泡（shown={focus}）")
    except Exception as e:  # noqa: BLE001
        bad.append(f"tooltip 检查异常: {type(e).__name__}: {e}")
    finally:
        ctx.close()
    return bad


def check_dead_utility_classes() -> list[str]:
    """扫死类名（§6.25）。

    tailwindcss-animate 不在依赖里，`animate-in` / `fade-in-0` / `zoom-in-95`
    / `slide-in-from-*` 这一组看着像动画、实际一条 CSS 都没生成。它们比「没写动效」
    更糟：review 时看着像做过了。所以宁可让审计失败，也不允许再混进来。
    """
    bad: list[str] = []
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "frontend", "src")
    dead = ("animate-in", "animate-out", "fade-in-0", "fade-out-0",
            "zoom-in-95", "zoom-out-95", "slide-in-from-", "slide-out-to-")
    for dirpath, _, files in os.walk(root):
        if "__tests__" in dirpath:
            continue
        for name in files:
            if not name.endswith((".vue", ".ts")):
                continue
            path = os.path.join(dirpath, name)
            with open(path, encoding="utf-8", errors="ignore") as fh:
                src = fh.read()
            hits = sorted({c for c in dead if c in src})
            if hits:
                bad.append(f"{os.path.relpath(path, root)} 含死类名 {', '.join(hits)}")
    return bad


MOTION_JS = """() => {
  const pick = (sel) => document.querySelector(sel);
  const read = (el) => {
    if (!el) return null;
    const cs = getComputedStyle(el);
    return { anim: cs.animationName, dur: cs.animationDuration,
             opacity: cs.opacity, transform: cs.transform,
             translate: cs.translate, radius: cs.borderRadius, shadow: cs.boxShadow };
  };
  return { scrim: read(pick('.wb-anim-scrim')), panel: read(pick('.wb-anim-panel')) };
}"""


def check_motion_tokens(browser) -> list[str]:
    """浮层动效与阴影分级（§6.25）。

    矩阵量的是静态页面，量不到「打开瞬间各自动了什么」，所以单独起上下文：
    亮/暗各开一次弹窗，再开一次用户菜单（下拉档），最后用 reduced_motion
    模拟系统「减少动态效果」。
    """
    bad: list[str] = []

    def open_dialog(page):
        page.goto(BASE + "/workspace/resumes", wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(1700)
        btn = page.locator("button", has_text="新建简历")
        if not btn.count():
            return False
        btn.first.click()
        page.wait_for_timeout(350)
        return True

    def make_ctx(theme: str, reduced: bool = False):
        ctx = browser.new_context(
            viewport={"width": 1280, "height": 820},
            reduced_motion="reduce" if reduced else "no-preference",
        )
        ctx.add_init_script(
            "localStorage.setItem('vitaflow_token', " + json.dumps(TOKEN) + ");"
            "localStorage.setItem('vitaflow-theme', " + json.dumps(theme) + ");"
        )
        return ctx

    try:
        # ---- 亮色：遮罩与面板各自动、阴影走模态档、关闭不被 fill 锁住 ----
        ctx = make_ctx("light")
        page = ctx.new_page()
        if not open_dialog(page):
            bad.append("找不到「新建简历」入口，无法验证弹窗动效")
        else:
            r = page.evaluate(MOTION_JS)
            if not r["scrim"] or not r["panel"]:
                bad.append("弹窗里没有 wb-anim-scrim / wb-anim-panel")
            else:
                if r["scrim"]["anim"] != "wb-scrim-in":
                    bad.append(f"遮罩应播 wb-scrim-in，实得 {r['scrim']['anim']}")
                if r["scrim"]["transform"] != "none":
                    bad.append(f"遮罩不应整体位移，实得 transform={r['scrim']['transform']}")
                if r["panel"]["anim"] != "wb-panel-in":
                    bad.append(f"面板应播 wb-panel-in，实得 {r['panel']['anim']}")
                if r["panel"]["dur"] != "0.2s":
                    bad.append(f"面板入场应 200ms，实得 {r['panel']['dur']}")
                if "48px" not in r["panel"]["shadow"]:
                    bad.append("弹窗应使用模态档阴影（0 8px 48px），实得 " + r["panel"]["shadow"][:60])
                if r["panel"]["radius"] != "16px":
                    bad.append(f"弹窗圆角应为 16px，实得 {r['panel']['radius']}")

            # 关闭：入场动画 fill:both 会把 opacity 钉在 1，必须被 .wb-leaving 摘掉
            page.locator(".wb-anim-panel button[aria-label='关闭']").first.click()
            page.wait_for_timeout(40)
            mid = page.evaluate(
                "() => { const e = document.querySelector('.wb-anim-panel');"
                " return { anim: e ? getComputedStyle(e).animationName : null,"
                " leaving: !!document.querySelector('.wb-leaving') }; }"
            )
            if mid["anim"] not in (None, "none"):
                bad.append(f"离场时面板动画未被摘除（{mid['anim']}），淡出会被 fill 锁住")
            page.wait_for_timeout(320)
            if page.locator(".wb-anim-panel").count():
                bad.append("关闭 360ms 后弹窗仍未移除")

            # ---- 下拉档：用户菜单的阴影必须比模态轻 ----
            page.goto(BASE + "/workspace", wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(1700)
            trigger = page.locator("button[aria-expanded]")
            if trigger.count():
                trigger.last.click()
                page.wait_for_timeout(260)
                dd = page.evaluate(
                    "() => { const e = document.querySelector('.shadow-wb-dropdown');"
                    " return e ? getComputedStyle(e).boxShadow : null; }"
                )
                if not dd:
                    bad.append("用户菜单没有使用 shadow-wb-dropdown")
                elif "48px" in dd:
                    bad.append("下拉误用了模态档阴影（48px 扩散）")
                elif "0.04" not in dd:
                    bad.append("下拉阴影应走 4% 的轻档，实得 " + dd[:60])
        ctx.close()

        # ---- 暗色：同档阴影必须加重，否则深底上看不出浮层边界 ----
        ctx = make_ctx("dark")
        page = ctx.new_page()
        if open_dialog(page):
            r = page.evaluate(MOTION_JS)
            if not r["panel"]:
                bad.append("暗色下没取到弹窗面板")
            elif "0.6" not in r["panel"]["shadow"]:
                bad.append("暗色弹窗阴影未加重（应含 60% 黑），实得 " + r["panel"]["shadow"][:60])
        else:
            bad.append("暗色下找不到「新建简历」入口")
        ctx.close()

        # ---- 系统「减少动态效果」：动画整体退化为瞬时 ----
        ctx = make_ctx("light", reduced=True)
        page = ctx.new_page()
        if open_dialog(page):
            r = page.evaluate(MOTION_JS)
            if r["panel"] and r["panel"]["anim"] != "none":
                bad.append(f"prefers-reduced-motion 下面板仍在播动画（{r['panel']['anim']}）")
            if r["scrim"] and r["scrim"]["anim"] != "none":
                bad.append(f"prefers-reduced-motion 下遮罩仍在播动画（{r['scrim']['anim']}）")
        else:
            bad.append("reduced-motion 下找不到「新建简历」入口")
        ctx.close()
    except Exception as e:  # noqa: BLE001
        bad.append(f"动效检查异常: {type(e).__name__}: {e}")
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
        print(f"ResumeFlow UI 回归审计  base={BASE}  mode={mode}  "
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

        # 会话折叠交互（§6.23）：矩阵里数据不够多，单独注入内存态验证
        folding_bad = check_session_folding(browser)
        # 审阅台（§6.48）：只有存在 pending 提案时才出现，矩阵拿不到
        review_bad = check_review_board(browser)
        # 面板分隔条（§6.51）：1px 细线取代可见把手，矩阵只量外壳
        splitter_bad = check_splitter(browser)
        # 全站 tooltip（§6.24）：气泡只在悬停/聚焦时存在，静态矩阵量不到
        tooltip_bad = check_tooltip(browser)
        # 浮层动效与阴影分级（§6.25）：只在「打开的一瞬间」成立，矩阵也量不到
        motion_bad = check_motion_tokens(browser)
        browser.close()

    # 静态扫描：死动画类名不依赖浏览器，放最后跑
    dead_bad = check_dead_utility_classes()

    if AS_JSON:
        print(json.dumps({"base": BASE, "mode": mode, "cells": rows,
                          "runtime_failures": runtime_bad,
                          "folding_failures": folding_bad,
                          "review_failures": review_bad,
                          "splitter_failures": splitter_bad,
                          "tooltip_failures": tooltip_bad,
                          "motion_failures": motion_bad,
                          "dead_class_failures": dead_bad,
                          "failed_cells": len(failures)}, ensure_ascii=False, indent=2))
    else:
        print("─" * 62)
        print(f"运行时 light→dark（不刷新）: {'✓ 稳定' if not runtime_bad else '✗ ' + '; '.join(runtime_bad)}")
        print(f"侧栏轨位 + 折叠 5→8→5 + 激活保护: {'✓ 通过' if not folding_bad else '✗ ' + '; '.join(folding_bad)}")
        print(f"审阅台（全模块成册 / 修改前只读 / 微调进预览）: {'✓ 通过' if not review_bad else '✗ ' + '; '.join(review_bad)}")
        print(f"面板分隔线（1px 细线 / 无把手 / 悬停变色 / 可拖）: {'✓ 通过' if not splitter_bad else '✗ ' + '; '.join(splitter_bad)}")
        print(f"全站 tooltip 规格与 title 摘除/恢复: {'✓ 通过' if not tooltip_bad else '✗ ' + '; '.join(tooltip_bad)}")
        print(f"浮层动效分层 + 阴影分档 + reduced-motion: {'✓ 通过' if not motion_bad else '✗ ' + '; '.join(motion_bad)}")
        print(f"死动画类名静态扫描: {'✓ 干净' if not dead_bad else '✗ ' + '; '.join(dead_bad)}")
        print(f"合计 {len(rows)} 格 · 断言失败 {len(failures)} 格"
              + ("" if not runtime_bad else " · 运行时切换失败")
              + ("" if not folding_bad else " · 折叠检查失败")
              + ("" if not review_bad else " · 审阅台检查失败")
              + ("" if not splitter_bad else " · 分隔条检查失败")
              + ("" if not tooltip_bad else " · tooltip 检查失败")
              + ("" if not motion_bad else " · 动效检查失败")
              + ("" if not dead_bad else " · 死类名残留"))
    return 1 if (failures or runtime_bad or folding_bad or review_bad or splitter_bad or tooltip_bad
                 or motion_bad or dead_bad) else 0


if __name__ == "__main__":
    sys.exit(main())
