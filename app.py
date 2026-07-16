#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AX 국수 하수처리 GIS+AI — Flask 백엔드
깃-렌더 배포용. 엔드포인트:
  GET  /                     3패널 UI
  GET  /api/config           프론트 설정 (카카오 키 유무 등)
  GET  /api/layers           레이어 목록
  GET  /static/geojson/<f>   GeoJSON (Flask static)
  POST /api/query            {"question": "..."} → 질의 엔진
  POST /api/sparql           {"sparql": "..."} → raw SPARQL (검증용)
"""
import os
from flask import Flask, jsonify, request, send_from_directory
from query_engine import GuksuEngine, PREFIXES

app = Flask(__name__, static_folder="static")
engine = GuksuEngine(os.path.join(os.path.dirname(__file__), "data", "merged_guksu.ttl"))

LAYERS = [
    {"id": "pipes", "name": "오수관로", "type": "line",
     "file": "pipes.geojson", "default": True},
    {"id": "manholes", "name": "맨홀", "type": "point",
     "file": "manholes.geojson", "default": False},
    {"id": "pumpstations", "name": "중계펌프장", "type": "point",
     "file": "pumpstations.geojson", "default": True},
    {"id": "facility", "name": "처리시설", "type": "mixed",
     "file": "facility.geojson", "default": True},
    {"id": "equipment", "name": "장비(52종)", "type": "point",
     "file": "equipment.geojson", "default": True},
]


@app.route("/")
def index():
    return send_from_directory("static", "index.html")


@app.route("/api/config")
def config():
    return jsonify({
        "kakao_js_key": os.environ.get("KAKAO_JS_KEY") or None,
        "llm_enabled": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "triples": len(engine.g),
        "site_center": {"lat": 37.5114, "lng": 127.3999},  # 국수 처리장
    })


@app.route("/api/layers")
def layers():
    return jsonify(LAYERS)


@app.route("/api/query", methods=["POST"])
def query():
    q = (request.get_json(silent=True) or {}).get("question", "").strip()
    if not q:
        return jsonify({"error": "question 필드가 비었습니다."}), 400
    return jsonify(engine.answer(q))


@app.route("/api/sparql", methods=["POST"])
def sparql():
    sp = (request.get_json(silent=True) or {}).get("sparql", "").strip()
    if not sp:
        return jsonify({"error": "sparql 필드가 비었습니다."}), 400
    try:
        rows = engine._run(sp if "PREFIX" in sp else sp)
        return jsonify({"rows": rows, "count": len(rows)})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
