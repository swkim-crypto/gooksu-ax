#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
장비 52종 georef — C-002 원본 DXF 대조로 확정된 변환 (2026-07-15)

근거:
  처리동(생물반응조 건물) C-002 실좌표: X 235357.42~235382.62 (25.20m),
  Y 545805.90~545832.80 (26.90m). M-013 300mm 벽면 외곽 26.9×25.2m와 축 스왑
  → 회전 -90°(CW). 내부 격벽 3개 독립 대응으로 방향 확정:
    M-013 x+6.61m ↔ C-002 y+20.30m (26.9-6.61=20.29)
    M-013 y+10.20m ↔ C-002 x+10.20/10.70m
    M-013 y+15.00m ↔ C-002 x+14.50/15.00m
변환식 (M-013 로컬 mm → EPSG:5186 m):
  X = 0.001*y + 235435.98
  Y = -0.001*x + 546259.79
시트 오프셋 (→M-013 좌표계, 벽길이 매칭·양면 검산):
  M-012: dX=+84100, dY=0 / M-014: dX=-84600, dY=0
"""
import json
from rdflib import Graph, Namespace, Literal
from pyproj import Transformer

TTL = "data/merged_guksu.ttl"
AX = Namespace("http://samaneng.com/ax/onto#")
GEO = Namespace("http://www.opengis.net/ont/geosparql#")

SHEET_OFFSET = {"DXF:M-012": (84100.0, 0.0),
                "DXF:M-013": (0.0, 0.0),
                "DXF:M-014": (-84600.0, 0.0)}
TX, TY = 235435.98, 546259.79
BLDG = (235357.42, 545805.90, 235382.62, 545832.80)  # 처리동 rect (검증용)


def to5186(x_mm, y_mm, src):
    dx, dy = SHEET_OFFSET[src]
    x, y = x_mm + dx, y_mm + dy
    return 0.001 * y + TX, -0.001 * x + TY


def main():
    g = Graph()
    g.parse(TTL, format="turtle")
    tf = Transformer.from_crs("EPSG:5186", "EPSG:4326", always_xy=True)
    q = """PREFIX ax: <http://samaneng.com/ax/onto#>
    PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
    SELECT ?a ?xy ?src ?l ?tag WHERE {
      ?a ax:localXY ?xy ; ax:coordSource ?src ; a ax:Asset .
      OPTIONAL { ?a rdfs:label ?l } OPTIONAL { ?a ax:hasTag ?tag } }"""
    feats, inside, margin = [], 0, 3.0
    for r in g.query(q):
        x, y = map(float, str(r.xy).split(","))
        X, Y = to5186(x, y, str(r.src))
        g.set((r.a, GEO.asWKT, Literal(f"POINT({X:.2f} {Y:.2f})")))
        g.set((r.a, AX.coordStatus, Literal("georeferenced")))
        ok = (BLDG[0] - margin <= X <= BLDG[2] + margin and
              BLDG[1] - margin <= Y <= BLDG[3] + margin)
        inside += ok
        lon, lat = tf.transform(X, Y)
        feats.append({"type": "Feature", "id": str(r.a).split("/")[-1],
                      "properties": {"label": str(r.l or ""), "tag": str(r.tag or ""),
                                     "sheet": str(r.src), "in_bldg": bool(ok)},
                      "geometry": {"type": "Point",
                                   "coordinates": [round(lon, 7), round(lat, 7)]}})
    # 도면참조 오추출 잔재(M_023~026): 장비 아님 표기
    from rdflib import URIRef
    for t in ["M_023", "M_024", "M_025", "M_026"]:
        u = URIRef(f"http://samaneng.com/ax/data/guksu/asset/{t}")
        g.set((u, AX.coordStatus, Literal("excluded-drawing-ref")))
    g.serialize(TTL, format="turtle")
    with open("static/geojson/equipment.geojson", "w", encoding="utf-8") as fp:
        json.dump({"type": "FeatureCollection", "features": feats}, fp,
                  ensure_ascii=False, separators=(",", ":"))
    print(f"변환 {len(feats)}종, 처리동 ±{margin}m 내 {inside}종")
    for f in feats:
        if not f["properties"]["in_bldg"]:
            print("  건물 밖:", f["id"], f["properties"]["label"], f["geometry"]["coordinates"])


if __name__ == "__main__":
    main()
