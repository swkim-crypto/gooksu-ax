#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
C-002 유래 레이어(시설 facility + 장비 equipment + TTL WKT) 일괄 평행이동.
관로/맨홀/펌프장(관로 DXF 유래, 정위치)은 건드리지 않음.

사용법:
  1) 실제 지도(스카이뷰/구글맵)에서 '미니 육상트랙 중심'을 클릭해 위도,경도 확보
  2) 아래 REAL_TRACK_LATLON에 입력 후 실행
설계 프레임의 트랙 중심(EPSG:5186): (235340.0, 545812.4)
"""
import json, re
from pyproj import Transformer
from rdflib import Graph, Namespace, Literal

REAL_TRACK_LATLON = (None, None)   # (lat, lon) ← 여기 입력
DESIGN_TRACK_5186 = (235340.0, 545812.4)

TF_INV = Transformer.from_crs("EPSG:4326", "EPSG:5186", always_xy=True)
TF = Transformer.from_crs("EPSG:5186", "EPSG:4326", always_xy=True)
GEO = Namespace("http://www.opengis.net/ont/geosparql#")
AX = Namespace("http://samaneng.com/ax/onto#")


def main():
    lat, lon = REAL_TRACK_LATLON
    assert lat is not None, "REAL_TRACK_LATLON을 채우세요"
    rx, ry = TF_INV.transform(lon, lat)
    dx, dy = rx - DESIGN_TRACK_5186[0], ry - DESIGN_TRACK_5186[1]
    print(f"이동량: dX={dx:+.1f}m, dY={dy:+.1f}m")

    def shift_coords(c):
        if isinstance(c[0], (int, float)):
            x, y = TF_INV.transform(c[0], c[1])
            lo, la = TF.transform(x + dx, y + dy)
            return [round(lo, 7), round(la, 7)]
        return [shift_coords(i) for i in c]

    for name in ["facility", "equipment"]:
        p = f"static/geojson/{name}.geojson"
        d = json.load(open(p))
        for f in d["features"]:
            f["geometry"]["coordinates"] = shift_coords(f["geometry"]["coordinates"])
        json.dump(d, open(p, "w"), ensure_ascii=False, separators=(",", ":"))
        print(f"{name}: {len(d['features'])} 피처 이동")

    g = Graph(); g.parse("data/merged_guksu.ttl", format="turtle")
    n = 0
    for s, _, w in list(g.triples((None, GEO.asWKT, None))):
        # C-002 프레임 객체만: 장비(Asset) + 처리시설(Facility). 맨홀/측점은 관로 프레임이라 제외.
        if (s, None, AX.Manhole) in [(s, p2, o) for p2, o in g.predicate_objects(s)] or \
           str(s).find("/manhole/") >= 0 or str(s).find("/station/") >= 0:
            continue
        types = set(o for o in g.objects(s, None))
        m = re.match(r"POINT\(([\d.]+) ([\d.]+)\)", str(w))
        if not m: continue
        from rdflib import RDF
        tset = set(g.objects(s, RDF.type))
        if AX.Manhole in tset or AX.PipeStation in tset:
            continue
        X, Y = float(m.group(1)) + dx, float(m.group(2)) + dy
        g.set((s, GEO.asWKT, Literal(f"POINT({X:.2f} {Y:.2f})")))
        n += 1
    g.serialize("data/merged_guksu.ttl", format="turtle")
    print(f"TTL WKT {n}건 이동 (맨홀·관로측점 제외)")
    print("완료 — 재배포 후 스카이뷰에서 부지경계가 실부지와 겹치는지 확인")


if __name__ == "__main__":
    main()
