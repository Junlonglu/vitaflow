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
    const addBtn = [...aside.querySelectorAll('button')].find(
      (b) => b.querySelector('svg') && b.textContent.trim() === '新对话');
    sidebar = {
      parentText: L(pbtn?.querySelector('span.truncate')),
      parentTimeRight: R(pbtn?.querySelector('span.tabular-nums')),
      sessionText: L(srow.querySelector('span.truncate')),
      sessionTimeRight: R(srow.querySelector('span.tabular-nums')),
      moreText: more ? L(more.querySelector('span')) : null,
      addText: addBtn ? L(addBtn.querySelector('span')) : null,
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

    # 统计卡：容器 >=672 排 4 列，否则 2 列
    if r["gridCols"] is not None and r["containerW"] is not None:
        want = 4 if r["containerW"] >= 672 else 2
        if r["gridCols"] != want:
            bad.append(f"容器 {r['containerW']} 列数 {r['gridCols']}，应为 {want}")

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
        exp = page.locator('aside button[title="展开对话"]').first
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

        # 轨位（§6.23）：矩阵各路由都没有展开态，这条强制检查放在这里才真的会跑
        geom = page.evaluate("""() => {
          const a = document.querySelector('aside');
          const L = (el) => el ? +(el.getBoundingClientRect().left - a.getBoundingClientRect().left).toFixed(1) : null;
          const R = (el) => el ? +(el.getBoundingClientRect().right - a.getBoundingClientRect().left).toFixed(1) : null;
          const p = a.querySelector('div.group > button');
          const s = a.querySelector('[data-session-row]');
          const m = a.querySelector('[data-session-more]');
          const add = [...a.querySelectorAll('button')].find(
            (b) => b.querySelector('svg') && b.textContent.trim() === '新对话');
          return {
            parentIcon: L(p?.querySelector('svg')), parentText: L(p?.querySelector('span.truncate')),
            parentTimeRight: R(p?.querySelector('span.tabular-nums')),
            sessionText: L(s?.querySelector('span.truncate')),
            sessionTimeRight: R(s?.querySelector('span.tabular-nums')),
            moreText: L(m?.querySelector('span')), addIcon: L(add?.querySelector('svg')),
            addText: L(add?.querySelector('span')),
          };
        }""")
        texts = [v for k, v in geom.items() if k.endswith("Text") and v is not None]
        icons = [v for k, v in geom.items() if k.endswith("Icon") and v is not None]
        if len(texts) < 4 or max(texts) - min(texts) > 1:
            bad.append(f"侧栏文字轨不齐: {geom}")
        if len(icons) < 2 or max(icons) - min(icons) > 1:
            bad.append(f"侧栏图标轨不齐: {geom}")
        if geom["parentTimeRight"] is None or geom["sessionTimeRight"] is None \
                or abs(geom["parentTimeRight"] - geom["sessionTimeRight"]) > 2:
            bad.append(f"父子时间戳右缘不齐: {geom['parentTimeRight']} / {geom['sessionTimeRight']}")

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
        sel = 'aside button[title="展开对话"]'
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
          return { shown: t.dataset.shown, placement: t.dataset.placement, text: t.textContent.trim(),
                   bg: cs.backgroundColor, color: cs.color, radius: cs.borderRadius,
                   fontSize: cs.fontSize, pad: cs.padding, z: cs.zIndex,
                   arrowW: dot.borderLeftWidth, arrowH: dot.borderBottomWidth || dot.borderTopWidth,
                   arrowColor: dot.borderBottomColor || dot.borderTopColor,
                   gap: hr ? +(tr.top - hr.bottom).toFixed(1) : null,
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
        if r["placement"] != "bottom":
            bad.append(f"默认应放下方，实得 {r['placement']}")
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

        # 离开后必须收起，并把 title 还回去
        page.mouse.move(1240, 780)
        page.wait_for_timeout(420)
        left = page.evaluate("""() => {
          const t = document.querySelector('#wb-tooltip');
          return { shown: t ? t.dataset.shown : null,
                   titleBack: !!document.querySelector('aside button[title="展开对话"]') };
        }""")
        if left["shown"] != "false":
            bad.append(f"离开后气泡未收起（shown={left['shown']}）")
        if not left["titleBack"]:
            bad.append("离开后 title 未恢复，文本出口会断（§6.14）")

        # 键盘聚焦也要出气泡，否则键盘用户只剩读屏一条路
        page.evaluate("""() => {
          const b = document.querySelector('aside button[title="展开对话"]');
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
                          "tooltip_failures": tooltip_bad,
                          "motion_failures": motion_bad,
                          "dead_class_failures": dead_bad,
                          "failed_cells": len(failures)}, ensure_ascii=False, indent=2))
    else:
        print("─" * 62)
        print(f"运行时 light→dark（不刷新）: {'✓ 稳定' if not runtime_bad else '✗ ' + '; '.join(runtime_bad)}")
        print(f"侧栏轨位 + 折叠 5→8→5 + 激活保护: {'✓ 通过' if not folding_bad else '✗ ' + '; '.join(folding_bad)}")
        print(f"全站 tooltip 规格与 title 摘除/恢复: {'✓ 通过' if not tooltip_bad else '✗ ' + '; '.join(tooltip_bad)}")
        print(f"浮层动效分层 + 阴影分档 + reduced-motion: {'✓ 通过' if not motion_bad else '✗ ' + '; '.join(motion_bad)}")
        print(f"死动画类名静态扫描: {'✓ 干净' if not dead_bad else '✗ ' + '; '.join(dead_bad)}")
        print(f"合计 {len(rows)} 格 · 断言失败 {len(failures)} 格"
              + ("" if not runtime_bad else " · 运行时切换失败")
              + ("" if not folding_bad else " · 折叠检查失败")
              + ("" if not tooltip_bad else " · tooltip 检查失败")
              + ("" if not motion_bad else " · 动效检查失败")
              + ("" if not dead_bad else " · 死类名残留"))
    return 1 if (failures or runtime_bad or folding_bad or tooltip_bad
                 or motion_bad or dead_bad) else 0


if __name__ == "__main__":
    sys.exit(main())
