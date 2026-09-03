"""Generación del mapa de contenido en HTML.

Produce un único fichero HTML autónomo (sin dependencias externas ni CDN) con un
grafo dirigido de fuerza dibujado en SVG mediante una simulación en JavaScript
puro. Los nodos son rutas y las aristas, enlaces entre ellas.
"""

from __future__ import annotations

import html
import json
from urllib.parse import urlparse

from route_mapper.models import CrawlResult, ExecutionMetadata, FetchOutcome

_TEMPLATE = """<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Mapa de contenido — {domain}</title>
<style>
  :root {{
    --bg: #0f1420; --panel: #182030; --fg: #e6e9ef; --muted: #9aa5b8;
    --edge: #38425a; --accent: #6ea8fe;
  }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; font: 14px/1.5 system-ui, -apple-system, Segoe UI, Roboto, sans-serif;
    background: var(--bg); color: var(--fg); overflow: hidden; }}
  header {{ position: fixed; top: 0; left: 0; right: 0; padding: 10px 16px;
    background: rgba(15,20,32,.9); backdrop-filter: blur(4px); z-index: 10;
    display: flex; gap: 18px; align-items: center; flex-wrap: wrap;
    border-bottom: 1px solid #2a3346; }}
  header h1 {{ font-size: 15px; margin: 0; font-weight: 600; }}
  header .stat {{ color: var(--muted); }}
  header .stat b {{ color: var(--fg); }}
  #legend {{ display: flex; gap: 12px; flex-wrap: wrap; }}
  #legend span {{ display: inline-flex; align-items: center; gap: 5px; cursor: pointer;
    user-select: none; opacity: .95; }}
  #legend span.off {{ opacity: .3; }}
  #legend i {{ width: 11px; height: 11px; border-radius: 50%; display: inline-block; }}
  svg {{ width: 100vw; height: 100vh; display: block; cursor: grab; }}
  svg.grabbing {{ cursor: grabbing; }}
  .edge {{ stroke: #38425a; stroke-width: 1; }}
  .edge.hl {{ stroke: var(--accent); stroke-width: 1.6; }}
  .node circle {{ stroke: #0b0f18; stroke-width: 1.5; cursor: pointer; }}
  .node.dim {{ opacity: .15; }}
  .node text {{ fill: var(--muted); font-size: 10px; pointer-events: none;
    paint-order: stroke; stroke: #0b0f18; stroke-width: 3px; }}
  #tip {{ position: fixed; pointer-events: none; background: var(--panel);
    border: 1px solid #2f3a52; border-radius: 6px; padding: 6px 9px; font-size: 12px;
    max-width: 380px; word-break: break-all; opacity: 0; transition: opacity .1s;
    z-index: 20; }}
  #tip .s {{ color: var(--muted); }}
  footer {{ position: fixed; bottom: 8px; right: 12px; color: var(--muted); font-size: 11px; }}
  #meta {{ position: fixed; top: 52px; left: 12px; z-index: 9; max-width: 340px;
    background: rgba(24,32,48,.92); border: 1px solid #2f3a52; border-radius: 6px;
    padding: 6px 10px; font-size: 12px; color: var(--muted); }}
  #meta summary {{ cursor: pointer; color: var(--fg); user-select: none; }}
  #meta table {{ border-collapse: collapse; margin-top: 6px; }}
  #meta td {{ padding: 1px 10px 1px 0; vertical-align: top; word-break: break-word; }}
  #meta td:first-child {{ color: var(--fg); white-space: nowrap; }}
</style>
</head>
<body>
<header>
  <h1>Mapa de contenido · {domain}</h1>
  <span class="stat"><b>{total}</b> rutas</span>
  <span class="stat"><b>{edges}</b> enlaces</span>
  <span class="stat"><b>{broken}</b> rotas</span>
  <div id="legend"></div>
</header>
{meta}
<svg id="graph"></svg>
<div id="tip"></div>
<footer>arrastra nodos · rueda para zoom · clic abre la URL</footer>
<script id="data" type="application/json">{data}</script>
<script>
{script}
</script>
</body>
</html>
"""

_SCRIPT = r"""
const DATA = JSON.parse(document.getElementById('data').textContent);
const SVG_NS = 'http://www.w3.org/2000/svg';
const svg = document.getElementById('graph');
const tip = document.getElementById('tip');

const CLASS_COLOR = {
  '2xx': '#3fb950', '3xx': '#6ea8fe', '4xx': '#e3b341',
  '5xx': '#f85149', 'err': '#8b949e', 'pending': '#6e7681'
};

function statusClass(n) {
  if (n.outcome === 'connection_error') return 'err';
  if (n.status == null) return 'pending';
  const c = Math.floor(n.status / 100);
  return c >= 2 && c <= 5 ? c + 'xx' : 'err';
}

const W = () => svg.clientWidth, H = () => svg.clientHeight;
const nodes = DATA.nodes.map(n => ({...n,
  x: W()/2 + (Math.random()-.5)*400,
  y: H()/2 + (Math.random()-.5)*400, vx:0, vy:0,
  cls: statusClass(n) }));
const byId = new Map(nodes.map(n => [n.id, n]));
const links = DATA.links.map(l => ({ source: byId.get(l.s), target: byId.get(l.t) }))
  .filter(l => l.source && l.target);

// --- SVG scaffolding -------------------------------------------------------
const root = document.createElementNS(SVG_NS, 'g');
svg.appendChild(root);
const defs = document.createElementNS(SVG_NS, 'defs');
defs.innerHTML = '<marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" ' +
  'markerWidth="6" markerHeight="6" orient="auto-start-reverse">' +
  '<path d="M0 0 L10 5 L0 10 z" fill="#38425a"/></marker>';
svg.appendChild(defs);

const edgeSel = links.map(l => {
  const line = document.createElementNS(SVG_NS, 'line');
  line.setAttribute('class', 'edge');
  line.setAttribute('marker-end', 'url(#arrow)');
  root.appendChild(line);
  return line;
});

const maxIn = Math.max(1, ...nodes.map(n => n.indeg));
const nodeSel = nodes.map(n => {
  const g = document.createElementNS(SVG_NS, 'g');
  g.setAttribute('class', 'node');
  const r = 4 + 9 * Math.sqrt(n.indeg / maxIn);
  n.r = r;
  const c = document.createElementNS(SVG_NS, 'circle');
  c.setAttribute('r', r);
  c.setAttribute('fill', CLASS_COLOR[n.cls] || '#8b949e');
  g.appendChild(c);
  if (n.indeg >= 3 || n.depth === 0) {
    const t = document.createElementNS(SVG_NS, 'text');
    t.setAttribute('x', r + 3); t.setAttribute('y', 3);
    t.textContent = n.label;
    g.appendChild(t);
  }
  root.appendChild(g);
  g._node = n;
  bindNode(g, n);
  return g;
});

// --- force simulation ----------------------------------------------------
let alpha = 1;
function tick() {
  alpha *= 0.985;
  const k = alpha;
  // repulsion
  for (let i = 0; i < nodes.length; i++) {
    const a = nodes[i];
    for (let j = i + 1; j < nodes.length; j++) {
      const b = nodes[j];
      let dx = a.x - b.x, dy = a.y - b.y;
      let d2 = dx*dx + dy*dy || 1;
      const f = 2600 / d2 * k;
      const d = Math.sqrt(d2);
      const ux = dx/d, uy = dy/d;
      a.vx += ux*f; a.vy += uy*f;
      b.vx -= ux*f; b.vy -= uy*f;
    }
  }
  // springs
  for (const l of links) {
    let dx = l.target.x - l.source.x, dy = l.target.y - l.source.y;
    const d = Math.sqrt(dx*dx + dy*dy) || 1;
    const f = (d - 90) * 0.02 * k;
    const ux = dx/d, uy = dy/d;
    l.source.vx += ux*f; l.source.vy += uy*f;
    l.target.vx -= ux*f; l.target.vy -= uy*f;
  }
  // centering + integrate
  const cx = W()/2, cy = H()/2;
  for (const n of nodes) {
    n.vx += (cx - n.x) * 0.0009 * k;
    n.vy += (cy - n.y) * 0.0009 * k;
    if (n === dragging) continue;
    n.vx *= 0.85; n.vy *= 0.85;
    n.x += n.vx; n.y += n.vy;
  }
  render();
  if (alpha > 0.005) requestAnimationFrame(tick);
}

function render() {
  for (let i = 0; i < links.length; i++) {
    const l = links[i], e = edgeSel[i];
    e.setAttribute('x1', l.source.x); e.setAttribute('y1', l.source.y);
    e.setAttribute('x2', l.target.x); e.setAttribute('y2', l.target.y);
  }
  for (const g of nodeSel) {
    g.setAttribute('transform', `translate(${g._node.x},${g._node.y})`);
  }
}

// --- interaction -------------------------------------------------------
let dragging = null, view = { x: 0, y: 0, k: 1 }, panning = false, panStart = null;

function applyView() {
  root.setAttribute('transform', `translate(${view.x},${view.y}) scale(${view.k})`);
}

function toWorld(ev) {
  return { x: (ev.clientX - view.x) / view.k, y: (ev.clientY - view.y) / view.k };
}

function bindNode(g, n) {
  g.addEventListener('pointerdown', ev => {
    ev.stopPropagation();
    dragging = n; g.setPointerCapture(ev.pointerId);
    alpha = Math.max(alpha, 0.3); requestAnimationFrame(tick);
  });
  g.addEventListener('pointermove', ev => {
    if (dragging !== n) return;
    const p = toWorld(ev); n.x = p.x; n.y = p.y; n.vx = n.vy = 0;
  });
  g.addEventListener('pointerup', ev => { dragging = null; });
  g.addEventListener('click', () => window.open(n.url, '_blank', 'noopener'));
  g.addEventListener('pointerenter', ev => hover(n, ev));
  g.addEventListener('pointerleave', () => unhover());
}

const neighbors = new Map(nodes.map(n => [n.id, new Set()]));
for (const l of DATA.links) {
  neighbors.get(l.s)?.add(l.t);
  neighbors.get(l.t)?.add(l.s);
}

function hover(n, ev) {
  // Defensa en profundidad: la URL nunca se interpola en innerHTML. Se
  // construye el DOM y se asigna vía textContent (inerte ante markup).
  tip.textContent = '';
  const urlLine = document.createElement('div');
  urlLine.textContent = n.url;
  const infoLine = document.createElement('div');
  infoLine.className = 's';
  infoLine.textContent =
    `${n.status ?? (n.error_detail ?? n.outcome)} · profundidad ${n.depth} · ` +
    `${n.indeg} entrantes / ${n.outdeg} salientes`;
  tip.appendChild(urlLine);
  tip.appendChild(infoLine);
  tip.style.opacity = 1;
  moveTip(ev);
  const nb = neighbors.get(n.id);
  nodeSel.forEach(g => g.classList.toggle('dim', g._node.id !== n.id && !nb.has(g._node.id)));
  edgeSel.forEach((e, i) => {
    const l = DATA.links[i];
    e.classList.toggle('hl', l.s === n.id || l.t === n.id);
  });
}
function unhover() {
  tip.style.opacity = 0;
  nodeSel.forEach(g => g.classList.remove('dim'));
  edgeSel.forEach(e => e.classList.remove('hl'));
}
function moveTip(ev) {
  tip.style.left = (ev.clientX + 14) + 'px';
  tip.style.top = (ev.clientY + 14) + 'px';
}
svg.addEventListener('pointermove', ev => { if (tip.style.opacity == 1) moveTip(ev); });

svg.addEventListener('pointerdown', ev => {
  panning = true; panStart = { x: ev.clientX - view.x, y: ev.clientY - view.y };
  svg.classList.add('grabbing');
});
svg.addEventListener('pointermove', ev => {
  if (!panning) return;
  view.x = ev.clientX - panStart.x; view.y = ev.clientY - panStart.y; applyView();
});
svg.addEventListener('pointerup', () => { panning = false; svg.classList.remove('grabbing'); });
svg.addEventListener('wheel', ev => {
  ev.preventDefault();
  const s = Math.exp(-ev.deltaY * 0.0015);
  const mx = ev.clientX, my = ev.clientY;
  view.x = mx - (mx - view.x) * s;
  view.y = my - (my - view.y) * s;
  view.k *= s; applyView();
}, { passive: false });

// --- legend / filters --------------------------------------------------
const LABELS = { '2xx':'2xx OK', '3xx':'3xx redirección', '4xx':'4xx cliente',
  '5xx':'5xx servidor', 'err':'error de red', 'pending':'no rastreada' };
const legend = document.getElementById('legend');
const active = new Set(Object.keys(LABELS));
for (const key of Object.keys(LABELS)) {
  if (!nodes.some(n => n.cls === key)) continue;
  const s = document.createElement('span');
  s.innerHTML = `<i style="background:${CLASS_COLOR[key]}"></i>${LABELS[key]}`;
  s.onclick = () => {
    active.has(key) ? active.delete(key) : active.add(key);
    s.classList.toggle('off');
    nodeSel.forEach(g => g.style.display = active.has(g._node.cls) ? '' : 'none');
    edgeSel.forEach((e, i) => {
      const l = DATA.links[i];
      const ok = active.has(byId.get(l.s).cls) && active.has(byId.get(l.t).cls);
      e.style.display = ok ? '' : 'none';
    });
  };
  legend.appendChild(s);
}

applyView();
requestAnimationFrame(tick);
"""


def _metadata_panel(metadata: ExecutionMetadata | None) -> str:
    """Panel de resumen visible con los parámetros exactos del escaneo."""
    if metadata is None:
        return ""
    rows: list[tuple[str, object]] = [
        ("versión", metadata.tool_version),
        ("inicio", metadata.started_at),
        ("fin", metadata.finished_at or "—"),
    ]
    rows += sorted(metadata.config.items())
    body = "".join(
        f"<tr><td>{html.escape(str(key))}</td>"
        f"<td>{html.escape(str(value))}</td></tr>"
        for key, value in rows
    )
    return (
        '<details id="meta" open><summary>Parámetros del escaneo</summary>'
        f"<table>{body}</table></details>"
    )


def _short_label(url: str, root_host: str) -> str:
    parsed = urlparse(url)
    path = parsed.path or "/"
    if parsed.query:
        path += "?…"
    if parsed.hostname and parsed.hostname != root_host:
        return f"{parsed.hostname}{path}"
    return path


class HtmlReporter:
    """Reporter que dibuja el mapa de conexiones entre rutas."""

    extension = "html"

    def render(self, result: CrawlResult) -> str:
        by_url = {p.url: p for p in result.pages}

        # El conjunto de nodos es la unión de páginas y extremos de aristas.
        urls: list[str] = list(by_url)
        seen = set(urls)
        for src, dst in result.edges:
            for u in (src, dst):
                if u not in seen:
                    seen.add(u)
                    urls.append(u)

        index = {u: i for i, u in enumerate(urls)}
        indeg = dict.fromkeys(range(len(urls)), 0)
        outdeg = dict.fromkeys(range(len(urls)), 0)
        for src, dst in result.edges:
            outdeg[index[src]] += 1
            indeg[index[dst]] += 1

        nodes = []
        for i, url in enumerate(urls):
            page = by_url.get(url)
            nodes.append(
                {
                    "id": i,
                    "url": url,
                    "label": _short_label(url, result.domain),
                    "status": page.status if page else None,
                    "outcome": page.outcome.value if page else FetchOutcome.SKIPPED.value,
                    "error_detail": page.error_detail if page else None,
                    "depth": page.depth if page else -1,
                    "indeg": indeg[i],
                    "outdeg": outdeg[i],
                }
            )
        links = [{"s": index[s], "t": index[d]} for s, d in sorted(result.edges)]

        # El JSON se inyecta en <script type="application/json">. Escapamos los
        # caracteres de control HTML para que ninguna URL pueda cerrar la etiqueta
        # <script> ni inyectar markup (Stored XSS contra el analista).
        data = (
            json.dumps({"nodes": nodes, "links": links}, ensure_ascii=False)
            .replace("<", "\\u003c")
            .replace(">", "\\u003e")
            .replace("&", "\\u0026")
        )
        summary = result.summary()
        return _TEMPLATE.format(
            domain=html.escape(result.domain),
            total=summary["total"],
            edges=summary["edges"],
            broken=summary["broken"],
            data=data,
            script=_SCRIPT,
            meta=_metadata_panel(result.metadata),
        )
