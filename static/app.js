/* 국수 하수처리 GIS+AI — 3패널 프론트
 * 지도: KAKAO_JS_KEY 있으면 카카오맵, 없으면 Leaflet+OSM 폴백
 * GeoJSON은 전부 WGS84(EPSG:4326)라 양쪽 다 좌표변환 불필요 */

const STYLE = {
  pipes: f => ({
    color: { "자연유하추정": "#1f77b4", "압송추정": "#d62728" }[f.properties.kind] || "#888",
    weight: 2.2,
  }),
  manholes: { radius: 3, color: "#333", fillColor: "#ffb300", fillOpacity: 0.9, weight: 1 },
  pumpstations: { radius: 8, color: "#1b5e20", fillColor: "#4caf50", fillOpacity: 0.95, weight: 2 },
  equipment: { radius: 4, color: "#4a148c", fillColor: "#ab47bc", fillOpacity: 0.9, weight: 1 },
  facility: f => f.properties.geom === "polygon"
    ? { color: "#e65100", fillColor: "#ffcc80", fillOpacity: 0.55, weight: 1 }
    : { color: "#9e9e9e", weight: 0.8 },
};
const SWATCH = { pipes: "#1f77b4", manholes: "#ffb300", pumpstations: "#4caf50", facility: "#ffcc80", equipment: "#ab47bc" };

let map, cfg, kakaoMode = false;
const leafletLayers = {};   // id -> L.geoJSON
const kakaoObjects = {};    // id -> [kakao overlay...]
const geojsonCache = {};

init();

async function init() {
  cfg = await (await fetch("/api/config")).json();
  const layers = await (await fetch("/api/layers")).json();

  if (cfg.kakao_js_key) {
    await initKakao(cfg.kakao_js_key);
  } else {
    initLeaflet();
  }
  document.getElementById("map-status").innerHTML =
    `지도: ${kakaoMode ? "카카오맵" : "OSM (카카오 키 대기)"}<br>그래프: ${cfg.triples.toLocaleString()} 트리플` +
    `<br>LLM 폴백: ${cfg.llm_enabled ? "on" : "off"}`;

  const list = document.getElementById("layer-list");
  for (const ly of layers) {
    const el = document.createElement("label");
    el.className = "layer-item";
    el.innerHTML = `<input type="checkbox" ${ly.default ? "checked" : ""} data-id="${ly.id}">
      <span class="swatch" style="background:${SWATCH[ly.id] || "#999"}"></span>${ly.name}`;
    el.querySelector("input").addEventListener("change", e => toggleLayer(ly, e.target.checked));
    list.appendChild(el);
    if (ly.default) toggleLayer(ly, true);
  }

  document.getElementById("chat-form").addEventListener("submit", onAsk);
}

/* ---------- Leaflet (키 없이 즉시 동작) ---------- */
function initLeaflet() {
  map = L.map("map").setView([cfg.site_center.lat, cfg.site_center.lng], 15);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    { attribution: "&copy; OpenStreetMap", maxZoom: 19 }).addTo(map);
}

/* ---------- 카카오맵 (키 투입 시) ---------- */
function initKakao(key) {
  return new Promise(res => {
    const s = document.createElement("script");
    s.src = `https://dapi.kakao.com/v2/maps/sdk.js?appkey=${key}&autoload=false`;
    s.onload = () => kakao.maps.load(() => {
      map = new kakao.maps.Map(document.getElementById("map"), {
        center: new kakao.maps.LatLng(cfg.site_center.lat, cfg.site_center.lng), level: 4,
      });
      kakaoMode = true; res();
    });
    document.head.appendChild(s);
  });
}

async function loadGeojson(file) {
  if (!geojsonCache[file])
    geojsonCache[file] = await (await fetch(`/static/geojson/${file}`)).json();
  return geojsonCache[file];
}

async function toggleLayer(ly, on) {
  const gj = await loadGeojson(ly.file);
  if (!kakaoMode) {
    if (on) {
      if (!leafletLayers[ly.id]) {
        const styler = STYLE[ly.id];
        leafletLayers[ly.id] = L.geoJSON(gj, {
          style: typeof styler === "function" ? styler : () => styler,
          pointToLayer: (f, latlng) =>
            L.circleMarker(latlng, typeof styler === "function" ? styler(f) : styler),
          onEachFeature: (f, l) => {
            const p = f.properties;
            const txt = p.label ? `${p.label} (${p.tag})` : (p.name || p.kind || p.block || p.layer || f.id);
            if (txt) l.bindPopup(String(txt));
          },
        });
      }
      leafletLayers[ly.id].addTo(map);
    } else if (leafletLayers[ly.id]) map.removeLayer(leafletLayers[ly.id]);
  } else {
    if (on) {
      if (!kakaoObjects[ly.id]) kakaoObjects[ly.id] = buildKakao(gj, ly.id);
      kakaoObjects[ly.id].forEach(o => o.setMap(map));
    } else if (kakaoObjects[ly.id]) kakaoObjects[ly.id].forEach(o => o.setMap(null));
  }
}

function buildKakao(gj, id) {
  const objs = [];
  const styler = STYLE[id];
  for (const f of gj.features) {
    const st = typeof styler === "function" ? styler(f) : styler;
    const g = f.geometry;
    const toLL = c => new kakao.maps.LatLng(c[1], c[0]);
    if (g.type === "LineString") {
      objs.push(new kakao.maps.Polyline({
        path: g.coordinates.map(toLL), strokeColor: st.color, strokeWeight: st.weight || 2,
      }));
    } else if (g.type === "Polygon") {
      objs.push(new kakao.maps.Polygon({
        path: g.coordinates[0].map(toLL), strokeColor: st.color, strokeWeight: st.weight || 1,
        fillColor: st.fillColor, fillOpacity: st.fillOpacity || 0.5,
      }));
    } else if (g.type === "Point") {
      objs.push(new kakao.maps.Circle({
        center: toLL(g.coordinates), radius: (st.radius || 4),
        strokeColor: st.color, fillColor: st.fillColor, fillOpacity: st.fillOpacity || 0.9,
      }));
    }
  }
  return objs;
}

/* ---------- AI 질의 ---------- */
async function onAsk(e) {
  e.preventDefault();
  const input = document.getElementById("chat-input");
  const q = input.value.trim();
  if (!q) return;
  input.value = "";
  addMsg("user", q);
  const btn = e.target.querySelector("button");
  btn.disabled = true;
  try {
    const r = await (await fetch("/api/query", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: q }),
    })).json();
    addBotMsg(r);
  } catch (err) {
    addMsg("bot", "질의 실패: " + err);
  }
  btn.disabled = false;
}

function addMsg(cls, text) {
  const log = document.getElementById("chat-log");
  const d = document.createElement("div");
  d.className = "msg " + cls;
  d.textContent = text;
  log.appendChild(d);
  log.scrollTop = log.scrollHeight;
}

function addBotMsg(r) {
  const log = document.getElementById("chat-log");
  const d = document.createElement("div");
  d.className = "msg bot";
  d.textContent = r.answer || JSON.stringify(r);
  if (r.sparql) {
    const t = document.createElement("span");
    t.className = "toggle-sparql";
    t.textContent = `SPARQL 보기 (${r.route})`;
    const pre = document.createElement("div");
    pre.className = "sparql";
    pre.textContent = r.sparql;
    t.onclick = () => pre.classList.toggle("open");
    d.appendChild(t); d.appendChild(pre);
  }
  log.appendChild(d);
  log.scrollTop = log.scrollHeight;
}
