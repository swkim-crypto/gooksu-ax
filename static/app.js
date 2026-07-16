/* 국수 하수처리 GIS+AI — 3패널 프론트
 * 지도: KAKAO_JS_KEY 있으면 카카오맵(지도/스카이뷰 전환), 없으면 Leaflet+OSM/위성
 * 점 객체는 클릭 가능한 오버레이로 렌더, 질의 결과는 지도 하이라이트 */

const STYLE = {
  pipes: f => ({
    color: { "자연유하추정": "#1f77b4", "압송추정": "#d62728" }[f.properties.kind] || "#888",
    weight: 2.2,
  }),
  manholes: { radius: 3, color: "#333", fillColor: "#ffb300", fillOpacity: 0.9, weight: 1 },
  pumpstations: { radius: 8, color: "#1b5e20", fillColor: "#4caf50", fillOpacity: 0.95, weight: 2 },
  equipment: { radius: 5, color: "#4a148c", fillColor: "#ab47bc", fillOpacity: 0.9, weight: 1 },
  facility: f => f.properties.geom === "polygon"
    ? { color: "#e65100", fillColor: "#ffcc80", fillOpacity: 0.45, weight: 1 }
    : f.properties.geom === "label"
    ? { radius: 4, color: "#37474f", fillColor: "#eceff1", fillOpacity: 0.95, weight: 1.5 }
    : { color: "#8d6e63", weight: 1 },
};
const SWATCH = { pipes: "#1f77b4", manholes: "#ffb300", pumpstations: "#4caf50", facility: "#ffcc80", equipment: "#ab47bc" };
const DOT_PX = { manholes: 7, pumpstations: 14, equipment: 11 };  // 카카오 점 크기(px)

let map, cfg, kakaoMode = false;
const leafletLayers = {};
const kakaoObjects = {};
const geojsonCache = {};
let highlightObjs = [];   // 질의 하이라이트 (양쪽 모드 공용 컨테이너)
let kakaoInfo = null;     // 카카오 정보창 (단일 재사용)

init();

async function init() {
  cfg = await (await fetch("/api/config")).json();
  const layers = await (await fetch("/api/layers")).json();

  if (cfg.kakao_js_key) await initKakao(cfg.kakao_js_key);
  else initLeaflet();

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
  const base = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    { attribution: "&copy; OpenStreetMap", maxZoom: 19 }).addTo(map);
  const sat = L.tileLayer(
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    { attribution: "Esri World Imagery", maxZoom: 19 });
  L.control.layers({ "일반지도": base, "항공사진": sat }, null,
    { position: "topright" }).addTo(map);
}

/* ---------- 카카오맵 ---------- */
function initKakao(key) {
  return new Promise(res => {
    const s = document.createElement("script");
    s.src = `https://dapi.kakao.com/v2/maps/sdk.js?appkey=${key}&autoload=false`;
    s.onload = () => kakao.maps.load(() => {
      map = new kakao.maps.Map(document.getElementById("map"), {
        center: new kakao.maps.LatLng(cfg.site_center.lat, cfg.site_center.lng), level: 4,
      });
      map.addControl(new kakao.maps.MapTypeControl(), kakao.maps.ControlPosition.TOPRIGHT); // 지도|스카이뷰
      map.addControl(new kakao.maps.ZoomControl(), kakao.maps.ControlPosition.RIGHT);
      kakaoInfo = new kakao.maps.CustomOverlay({ yAnchor: 1.4, zIndex: 30 });
      kakao.maps.event.addListener(map, "click", () => kakaoInfo.setMap(null));
      kakaoMode = true; res();
    });
    document.head.appendChild(s);
  });
}

function showKakaoInfo(latlng, text) {
  const div = document.createElement("div");
  div.className = "kk-info";
  div.textContent = text;
  kakaoInfo.setContent(div);
  kakaoInfo.setPosition(latlng);
  kakaoInfo.setMap(map);
}

function kakaoDot(lon, lat, st, sizePx, text, zIndex = 10) {
  const div = document.createElement("div");
  div.className = "kk-dot";
  div.style.cssText = `width:${sizePx}px;height:${sizePx}px;background:${st.fillColor};
    border:1.5px solid ${st.color};`;
  if (text) div.title = text;
  const pos = new kakao.maps.LatLng(lat, lon);
  const ov = new kakao.maps.CustomOverlay({ position: pos, content: div, zIndex });
  if (text) div.addEventListener("click", ev => { ev.stopPropagation(); showKakaoInfo(pos, text); });
  return ov;
}

async function loadGeojson(file) {
  if (!geojsonCache[file])
    geojsonCache[file] = await (await fetch(`/static/geojson/${file}`)).json();
  return geojsonCache[file];
}

function featureText(f) {
  const p = f.properties;
  if (p.label) return p.tag ? `${p.label} (${p.tag})` : p.label;
  return p.name || p.kind || p.block || p.layer || String(f.id || "");
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
          onEachFeature: (f, l) => { const t = featureText(f); if (t) l.bindPopup(t); },
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
    const text = featureText(f);
    if (g.type === "LineString") {
      const pl = new kakao.maps.Polyline({
        path: g.coordinates.map(toLL), strokeColor: st.color, strokeWeight: st.weight || 2 });
      kakao.maps.event.addListener(pl, "click", (e) => showKakaoInfo(e.latLng, text));
      objs.push(pl);
    } else if (g.type === "Polygon") {
      const pg = new kakao.maps.Polygon({
        path: g.coordinates[0].map(toLL), strokeColor: st.color, strokeWeight: st.weight || 1,
        fillColor: st.fillColor, fillOpacity: st.fillOpacity || 0.5 });
      kakao.maps.event.addListener(pg, "click", (e) => showKakaoInfo(e.latLng, text));
      objs.push(pg);
    } else if (g.type === "Point") {
      objs.push(kakaoDot(g.coordinates[0], g.coordinates[1], st,
        DOT_PX[id] || 9, text, id === "equipment" ? 15 : 10));
    }
  }
  return objs;
}

/* ---------- 질의 결과 지도 하이라이트 ---------- */
function clearHighlights() {
  for (const o of highlightObjs) {
    if (kakaoMode) o.setMap(null); else map.removeLayer(o);
  }
  highlightObjs = [];
}

function drawHighlights(features) {
  clearHighlights();
  const HL = { color: "#d500f9", weight: 4 };
  const pts = [];
  for (const f of features) {
    if (f.kind === "edge") {
      pts.push(f.from, f.to);
      if (kakaoMode) {
        const pl = new kakao.maps.Polyline({
          path: [new kakao.maps.LatLng(f.from[1], f.from[0]), new kakao.maps.LatLng(f.to[1], f.to[0])],
          strokeColor: HL.color, strokeWeight: HL.weight, strokeStyle: "shortdash", zIndex: 20 });
        kakao.maps.event.addListener(pl, "click", (e) => showKakaoInfo(e.latLng, f.label));
        pl.setMap(map); highlightObjs.push(pl);
      } else {
        const l = L.polyline([[f.from[1], f.from[0]], [f.to[1], f.to[0]]],
          { color: HL.color, weight: HL.weight, dashArray: "6 6" }).bindPopup(f.label).addTo(map);
        highlightObjs.push(l);
      }
    } else if (f.kind === "point") {
      pts.push(f.coord);
      if (kakaoMode) {
        const ov = kakaoDot(f.coord[0], f.coord[1],
          { fillColor: "#ffff00", color: "#d500f9" }, 13, f.label, 25);
        ov.setMap(map); highlightObjs.push(ov);
      } else {
        const m = L.circleMarker([f.coord[1], f.coord[0]],
          { radius: 7, color: "#d500f9", fillColor: "#ffff00", fillOpacity: 0.95, weight: 2 })
          .bindPopup(f.label).addTo(map);
        highlightObjs.push(m);
      }
    }
  }
  if (!pts.length) return;
  // 하이라이트 범위로 이동
  if (kakaoMode) {
    const b = new kakao.maps.LatLngBounds();
    pts.forEach(c => b.extend(new kakao.maps.LatLng(c[1], c[0])));
    map.setBounds(b, 80, 80, 80, 80);
  } else {
    map.fitBounds(pts.map(c => [c[1], c[0]]), { padding: [60, 60] });
  }
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
    if (r.map && r.map.features && r.map.features.length) drawHighlights(r.map.features);
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
  if (r.map && r.map.features && r.map.features.length) {
    const c = document.createElement("span");
    c.className = "toggle-sparql";
    c.textContent = "지도 표시 지우기";
    c.onclick = () => clearHighlights();
    d.appendChild(document.createElement("br"));
    d.appendChild(c);
  }
  if (r.sparql) {
    const t = document.createElement("span");
    t.className = "toggle-sparql";
    t.textContent = ` SPARQL 보기 (${r.route})`;
    const pre = document.createElement("div");
    pre.className = "sparql";
    pre.textContent = r.sparql;
    t.onclick = () => pre.classList.toggle("open");
    d.appendChild(t); d.appendChild(pre);
  }
  log.appendChild(d);
  log.scrollTop = log.scrollHeight;
}
