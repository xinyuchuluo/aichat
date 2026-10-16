"""局域网阅读分享服务器（只读）。

让同一局域网下的手机 / 平板，通过浏览器查看 NovelForge 写作的
正文与章节大纲。零外部依赖，仅使用 Python 标准库。

设计要点：
- 只读：只响应 GET，且所有数据读取都经由 ProjectManager / chapter_outlines.json，
  不提供任何写入或删除接口，不会改动用户的小说文件。
- 路径安全：项目名只在已注册项目列表中匹配，章节号强制为整数，
  文件访问完全通过 ProjectManager 解析，杜绝路径穿越。
- 单进程单实例：同一个 LanViewerServer 实例 start/stop 幂等。
"""
from __future__ import annotations

import json
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from domains.novel.core.project.manager import ProjectManager
from domains.novel.utils.logger import logger

DEFAULT_PORT = 8765


def detect_lan_ip() -> str:
    """返回本机在局域网中的 IP；失败则回退 127.0.0.1。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # UDP connect 不会真正发包，仅用于探测出口网卡 IP
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
    except Exception:
        try:
            ip = socket.gethostbyname(socket.gethostname())
        except Exception:
            ip = "127.0.0.1"
    finally:
        s.close()
    return ip


def detect_mdns_hostname() -> str:
    """返回本机的 mDNS / Bonjour 主机名（如 Chens-MacBook-Pro.local）。

    该名称与「电脑名」绑定，由系统固定，不随局域网 IP（DHCP 重分配）
    变化而失效，因此最适合作为「固定网址」给同一局域网的手机访问。
    """
    try:
        name = socket.gethostname() or ""
    except Exception:
        name = ""
    name = name.strip()
    if not name or name == "localhost":
        # 退路：尝试完整域名解析
        try:
            name = socket.getfqdn()
        except Exception:
            name = ""
    if not name or name == "localhost":
        return "localhost"
    if not name.endswith(".local"):
        name = name + ".local"
    return name


def _load_outlines(root: str) -> dict:
    """读取 chapter_outlines.json -> {chapter_id: outline_dict}。

    兼容两种存储形态：直接是 list，或 {"outlines": [...]}。
    """
    p = Path(root) / ".novelforge" / "chapter_outlines.json"
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = data.get("outlines", [])
    else:
        return {}
    out = {}
    for it in items:
        if isinstance(it, dict) and "chapter_id" in it:
            try:
                out[int(it["chapter_id"])] = it
            except (TypeError, ValueError):
                continue
    return out


class LanViewerServer:
    """局域网阅读服务封装，持有 ProjectManager 引用。"""

    def __init__(self, project_manager: ProjectManager, host: str = "0.0.0.0",
                 port: int = DEFAULT_PORT):
        self._pm = project_manager
        self._host = host
        self._port = port
        self._httpd = None
        self._thread = None
        self._lock = threading.Lock()

    @property
    def is_running(self) -> bool:
        with self._lock:
            return self._httpd is not None

    @property
    def port(self) -> int:
        return self._port

    @property
    def lan_url(self) -> str:
        return f"http://{detect_lan_ip()}:{self._port}/"

    @property
    def fixed_url(self) -> str:
        """固定网址：基于 mDNS 主机名，不受局域网 IP 变化影响。"""
        return f"http://{detect_mdns_hostname()}:{self._port}/"

    @property
    def local_url(self) -> str:
        return f"http://127.0.0.1:{self._port}/"

    def make_qr_data_uri(self, url: str | None = None, scale: int = 8,
                         border: int = 2) -> str:
        """生成二维码 PNG 的 data URI（可直接喂给 QLabel/浏览器）。

        用纯 Python 的 segno（零外部依赖）。默认编码当前局域网 IP 网址。
        """
        import segno
        target = url or self.lan_url
        qr = segno.make(target, error="m")
        return qr.png_data_uri(scale=scale, border=border)

    def start(self):
        with self._lock:
            if self._httpd is not None:
                return
            pm = self._pm
            handler = self._build_handler(pm)
            try:
                httpd = ThreadingHTTPServer((self._host, self._port), handler)
            except OSError as e:
                logger.error(f"局域网阅读服务启动失败(端口 {self._port}): {e}")
                raise
            self._httpd = httpd
            t = threading.Thread(target=httpd.serve_forever, daemon=True)
            t.start()
            self._thread = t
            logger.info(f"局域网阅读服务已启动: {self.lan_url}")

    def stop(self):
        with self._lock:
            if self._httpd is None:
                return
            try:
                self._httpd.shutdown()
                self._httpd.server_close()
            finally:
                self._httpd = None
                self._thread = None
            logger.info("局域网阅读服务已停止")

    # ------------------------------------------------------------------
    # 请求处理器构建（闭包，使 Handler 能访问 ProjectManager）
    # ------------------------------------------------------------------
    def _build_handler(self, pm: ProjectManager):
        class Handler(BaseHTTPRequestHandler):
            # 默认 HTTP/1.0：每请求一连接，手机端兼容性最好，无需管理 keep-alive
            def log_message(self, *args):
                pass  # 静默，避免刷屏

            def _send(self, code, body: bytes,
                      ctype: str = "text/html; charset=utf-8"):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)

            def _json(self, obj) -> bytes:
                return json.dumps(obj, ensure_ascii=False).encode("utf-8")

            def _projects(self):
                return pm.list_projects()

            def _find_project(self, name: str):
                for p in self._projects():
                    if p.name == name:
                        return p
                return None

            def do_GET(self):
                parsed = urlparse(self.path)
                path = parsed.path
                qs = parse_qs(parsed.query)
                try:
                    if path in ("/", "/index.html"):
                        self._send(200, INDEX_HTML.encode("utf-8"))
                    elif path == "/api/projects":
                        self._send(200, self._json(self._api_projects()),
                                   "application/json; charset=utf-8")
                    elif path == "/api/chapters":
                        name = qs.get("p", [""])[0]
                        self._send(200, self._json(self._api_chapters(name)),
                                   "application/json; charset=utf-8")
                    elif path == "/api/book":
                        name = qs.get("p", [""])[0]
                        self._send(200, self._json(self._api_book(name)),
                                   "application/json; charset=utf-8")
                    elif path == "/api/chapter":
                        name = qs.get("p", [""])[0]
                        cid = int(qs.get("id", ["0"])[0] or 0)
                        self._send(200, self._json(self._api_chapter(name, cid)),
                                   "application/json; charset=utf-8")
                    elif path == "/api/master":
                        name = qs.get("p", [""])[0]
                        self._send(200, self._json(self._api_master(name)),
                                   "application/json; charset=utf-8")
                    else:
                        self._send(404, b"Not Found")
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"LAN 请求处理异常 {self.path}: {e}")
                    self._send(500, str(e).encode("utf-8"))

            def _api_projects(self):
                items = []
                for p in self._projects():
                    items.append({
                        "name": p.name,
                        "genre": p.genre or "",
                        "description": p.description or "",
                        "current_chapter": p.current_chapter or 0,
                    })
                return {"projects": items}

            def _api_chapters(self, name: str):
                p = self._find_project(name)
                if not p:
                    return {"error": "project not found"}
                root = p.project_root
                chapters = pm.get_chapter_list(root)
                outlines = _load_outlines(root)
                items = []
                for ch in chapters:
                    o = outlines.get(ch["chapter_id"])
                    items.append({
                        "id": ch["chapter_id"],
                        "title": ch["title"],
                        "word_count": ch.get("word_count", 0),
                        "has_outline": bool(o),
                        "outline_label": (o or {}).get("chapter_type_label", ""),
                    })
                items.sort(key=lambda x: x["id"])
                return {"name": name, "chapters": items}

            def _api_book(self, name: str):
                """整本书：章节列表 + 大纲，正文按需懒加载（见 /api/chapter）。

                大纲与正文放在同一视图里连续滚动，正文在滚动到视口附近时再加载，
                避免一次性把整本书正文全塞进首屏。
                """
                p = self._find_project(name)
                if not p:
                    return {"error": "project not found"}
                root = p.project_root
                chapters = pm.get_chapter_list(root)
                outlines = _load_outlines(root)
                items = []
                for ch in chapters:
                    o = outlines.get(ch["chapter_id"])
                    items.append({
                        "id": ch["chapter_id"],
                        "title": ch["title"],
                        "word_count": ch.get("word_count", 0),
                        "outline": o or {},
                    })
                items.sort(key=lambda x: x["id"])
                has_master = bool((pm.get_outline(root, "master") or "").strip())
                return {
                    "name": name,
                    "description": p.description or "",
                    "chapters": items,
                    "has_master": has_master,
                    "total": len(items),
                }

            def _api_chapter(self, name: str, cid: int):
                p = self._find_project(name)
                if not p:
                    return {"error": "project not found"}
                root = p.project_root
                body = pm.get_chapter_text(root, cid)
                outlines = _load_outlines(root)
                o = outlines.get(cid, {})
                chapters = pm.get_chapter_list(root)
                ids = sorted(c["chapter_id"] for c in chapters)
                title = ""
                for c in chapters:
                    if c["chapter_id"] == cid:
                        title = c["title"]
                        break
                idx = ids.index(cid) if cid in ids else -1
                prev_id = ids[idx - 1] if idx > 0 else None
                next_id = ids[idx + 1] if idx >= 0 and idx + 1 < len(ids) else None
                return {
                    "id": cid,
                    "title": title,
                    "body": body,
                    "outline": o,
                    "prev_id": prev_id,
                    "next_id": next_id,
                }

            def _api_master(self, name: str):
                p = self._find_project(name)
                if not p:
                    return {"error": "project not found"}
                try:
                    text = pm.get_outline(p.project_root, "master")
                except Exception:
                    text = ""
                return {"name": name, "master": text or ""}

        return Handler


# ======================================================================
# 内嵌手机端阅读页（单页应用，纯原生 JS，无外部依赖）
# 设计：大纲与正文放在同一连续滚动视图，整体统一暗色主题。
# ======================================================================
INDEX_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>NovelForge 阅读</title>
<style>
  :root{ --bg:#1e1e2e; --panel:#262637; --fg:#cdd6f4; --muted:#9aa0b5; --accent:#89b4fa; --line:#3a3d52; }
  *{box-sizing:border-box; -webkit-tap-highlight-color:transparent;}
  html,body{margin:0; padding:0;}
  body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; background:var(--bg); color:var(--fg);}
  header{position:sticky; top:0; background:var(--bg); border-bottom:1px solid var(--line); padding:11px 14px; display:flex; align-items:center; gap:10px; z-index:20;}
  header .back{font-size:22px; cursor:pointer; display:none; padding:0 6px; line-height:1; color:var(--fg);}
  header .title{font-weight:600; font-size:16px; flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;}
  header .actions{margin-left:auto; display:flex; gap:8px;}
  header .actions button{background:var(--panel); border:1px solid var(--line); color:var(--fg); border-radius:8px; padding:6px 11px; font-size:13px; cursor:pointer;}
  header .actions button:active{background:rgba(137,180,250,.15);}
  .wrap{padding:14px 16px 60px; max-width:820px; margin:0 auto;}
  .card{background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:14px; margin-bottom:12px;}
  .card h3{margin:0 0 4px; font-size:16px; color:var(--fg);}
  .card .sub{color:var(--muted); font-size:13px; line-height:1.5;}
  .bookdesc{color:var(--muted); font-size:14px; line-height:1.7; margin:4px 4px 16px; white-space:pre-wrap;}
  /* 连续滚动章节区：大纲与正文同一视图 */
  .chsec{border-top:1px solid var(--line); padding:20px 0 14px; scroll-margin-top:54px;}
  .chsec:first-of-type{border-top:none; padding-top:8px;}
  .chsec h2{font-size:19px; margin:0 0 10px; line-height:1.4; color:var(--fg); font-weight:600;}
  .chsec h2 .wc{font-size:12px; color:var(--muted); font-weight:400; margin-left:8px;}
  .outline-inline{background:var(--panel); border-left:3px solid var(--accent); border-radius:8px; padding:10px 12px; margin:0 0 16px; font-size:13.5px; line-height:1.65; color:var(--fg);}
  .outline-inline .row{margin-top:6px;}
  .outline-inline .row b{color:var(--muted); font-weight:600; margin-right:6px; display:inline-block; min-width:62px;}
  .body p{line-height:1.95; font-size:17px; margin:0 0 16px; text-indent:2em; letter-spacing:.02em; color:var(--fg);}
  .empty{color:var(--muted); text-align:center; margin-top:48px; font-size:15px; line-height:1.7;}
  /* 目录抽屉 */
  .toc-mask{position:fixed; inset:0; background:rgba(0,0,0,.5); z-index:30; display:none;}
  .toc-mask.show{display:block;}
  .toc{position:fixed; top:0; right:0; height:100%; width:78%; max-width:340px; background:var(--bg); border-left:1px solid var(--line); z-index:31; transform:translateX(100%); transition:transform .22s ease; overflow-y:auto; padding:14px 0 30px;}
  .toc.show{transform:translateX(0);}
  .toc h4{margin:6px 16px 10px; font-size:14px; color:var(--muted);}
  .toc .ti{padding:12px 16px; border-bottom:1px solid var(--line); font-size:14px; cursor:pointer; color:var(--fg);}
  .toc .ti:active{background:rgba(137,180,250,.1);}
  .toc .ti .n{color:var(--muted); margin-right:8px; font-size:12px;}
</style>
</head>
<body>
<header>
  <span class="back" id="back">‹</span>
  <span class="title" id="htitle">NovelForge 阅读</span>
  <span class="actions" id="actions"></span>
</header>
<div class="toc-mask" id="tocMask"></div>
<nav class="toc" id="toc"></nav>
<div class="wrap" id="app"></div>
<script>
const app = document.getElementById('app');
const htitle = document.getElementById('htitle');
const back = document.getElementById('back');
const actions = document.getElementById('actions');
const toc = document.getElementById('toc');
const tocMask = document.getElementById('tocMask');
let bodyObserver = null;
const loaded = new Set();
let currentName = '';

function esc(s){
  return (s==null?'':String(s))
    .replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')
    .replace(/'/g,'&#39;');
}
function setBack(fn){
  if(fn){ back.style.display='inline'; back.onclick=fn; }
  else { back.style.display='none'; back.onclick=null; }
}
function setActions(html){ actions.innerHTML = html || ''; }

function md(text){
  if(!text || !text.trim()) return '<p class="empty">（本章暂无正文）</p>';
  return text.split(/\\n\\s*\\n/).map(b=>'<p>'+esc(b).replace(/\\n/g,'<br>')+'</p>').join('');
}
function outlineRowsHtml(o){
  if(!o) return '';
  const rows=[];
  if(o.chapter_type_label) rows.push(['类型',o.chapter_type_label]);
  if(o.rhythm) rows.push(['节奏',o.rhythm]);
  if(o.core_events) rows.push(['核心事件',o.core_events]);
  if(o.opening_hook) rows.push(['开篇钩子',o.opening_hook]);
  if(o.ending_hook) rows.push(['结尾钩子',o.ending_hook]);
  if(o.foreshadowing && o.foreshadowing!=='none') rows.push(['伏笔',o.foreshadowing]);
  if(o.plot_summary) rows.push(['剧情概要',o.plot_summary]);
  if(!rows.length) return '';
  return rows.map(r=>`<div class="row"><b>${esc(r[0])}</b>${esc(r[1])}</div>`).join('');
}

async function getj(url){
  const r = await fetch(url);
  if(!r.ok) throw new Error('请求失败 '+r.status);
  return r.json();
}

async function home(){
  if(bodyObserver){ bodyObserver.disconnect(); bodyObserver=null; }
  setActions('');
  htitle.textContent='选择作品';
  setBack(null);
  let data;
  try{ data = await getj('/api/projects'); }catch(e){ app.innerHTML='<div class="empty">无法连接服务<br>请确认电脑端 NovelForge 分享服务已开启</div>'; return; }
  if(!data.projects.length){ app.innerHTML='<div class="empty">没有已打开过的项目</div>'; return; }
  app.innerHTML = data.projects.map(p=>`
    <div class="card" onclick="openBook('${esc(p.name)}')">
      <h3>${esc(p.name)}</h3>
      <div class="sub">${esc(p.genre||'')} · 已写 ${p.current_chapter||0} 章</div>
      ${p.description?`<div class="sub" style="margin-top:6px">${esc(p.description).slice(0,60)}</div>`:''}
    </div>`).join('');
}

async function openBook(name){
  if(bodyObserver){ bodyObserver.disconnect(); bodyObserver=null; }
  currentName = name;
  closeToc();
  htitle.textContent = name;
  setBack(home);
  app.innerHTML='<div class="empty">加载中…</div>';
  let data;
  try{ data = await getj('/api/book?p='+encodeURIComponent(name)); }catch(e){ app.innerHTML='<div class="empty">加载失败</div>'; return; }
  if(data.error){ app.innerHTML='<div class="empty">'+esc(data.error)+'</div>'; return; }
  if(!data.chapters.length){ app.innerHTML='<div class="empty">该书还没有正文</div>'; return; }
  const acts = [];
  if(data.has_master) acts.push(`<button onclick="openMaster('${esc(name)}')">总纲</button>`);
  if(data.chapters.length>1) acts.push(`<button onclick="openToc()">目录</button>`);
  setActions(acts.join(''));
  toc.innerHTML = `<h4>${esc(name)} · 共 ${data.total} 章</h4>`+
    data.chapters.map(c=>`<div class="ti" onclick="jumpTo(${c.id})"><span class="n">第${c.id}章</span>${esc(c.title||'')}</div>`).join('');
  let html = data.description?`<div class="bookdesc">${esc(data.description)}</div>`:'';
  html += data.chapters.map(c=>`
    <section class="chsec" id="ch-${c.id}">
      <h2>第${c.id}章 ${esc(c.title||'')}<span class="wc">${c.word_count}字</span></h2>
      ${outlineRowsHtml(c.outline)?`<div class="outline-inline">${outlineRowsHtml(c.outline)}</div>`:''}
      <div class="body" data-id="${c.id}">加载中…</div>
    </section>`).join('');
  app.innerHTML = html;
  setupLazyBodies(name);
  window.scrollTo(0,0);
}

function loadBody(el, name){
  const id = el.getAttribute('data-id');
  if(loaded.has(id)) return Promise.resolve();
  loaded.add(id);
  return getj('/api/chapter?p='+encodeURIComponent(name)+'&id='+id)
    .then(d=>{ el.innerHTML = md(d.body); })
    .catch(e=>{ el.innerHTML='<p class="empty">正文加载失败</p>'; });
}
function setupLazyBodies(name){
  if(bodyObserver){ bodyObserver.disconnect(); }
  bodyObserver = new IntersectionObserver((entries)=>{
    entries.forEach(en=>{
      if(!en.isIntersecting) return;
      loadBody(en.target, name);
    });
  }, { rootMargin: '700px 0px' });
  document.querySelectorAll('.body[data-id]').forEach(el=>bodyObserver.observe(el));
}
// 跳转前，确保目标章节及其之前所有章节正文已加载：
// 否则上面的懒加载正文撑开页面会把目标章节往下挤，导致跳转落点偏移（点第N章却落在上游某章）。
async function ensureBodiesUpTo(target, name){
  const ps = [];
  document.querySelectorAll('.body[data-id]').forEach(el=>{
    const id = parseInt(el.getAttribute('data-id'), 10);
    if(!isNaN(id) && id <= target){
      const p = loadBody(el, name);
      if(p) ps.push(p);
    }
  });
  await Promise.all(ps);
}

function openToc(){ toc.classList.add('show'); tocMask.classList.add('show'); }
function closeToc(){ toc.classList.remove('show'); tocMask.classList.remove('show'); }
async function jumpTo(id){
  closeToc();
  const el = document.getElementById('ch-'+id);
  if(!el) return;
  await ensureBodiesUpTo(id, currentName);
  el.scrollIntoView({behavior:'smooth', block:'start'});
}
tocMask.onclick = closeToc;

async function openMaster(name){
  htitle.textContent='总纲';
  setBack(()=>openBook(name));
  setActions('');
  app.innerHTML='<div class="empty">加载中…</div>';
  let data;
  try{ data = await getj('/api/master?p='+encodeURIComponent(name)); }catch(e){ app.innerHTML='<div class="empty">加载失败</div>'; return; }
  app.innerHTML = `<div class="card" style="padding:16px; line-height:1.85; white-space:pre-wrap; font-size:14px; word-break:break-word; color:var(--fg);">${esc(data.master||'（暂无总纲）')}</div>`;
  window.scrollTo(0,0);
}

home();
</script>
</body>
</html>"""
