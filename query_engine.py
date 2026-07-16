#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AX 국수 하수처리 — 자연어→SPARQL 질의 엔진
대상: data/merged_guksu.ttl (4,020 트리플, rdflib 인메모리)

동작 방식:
 1) 템플릿 매칭 — 검증된 질의 유형(계통현황/소속계통/흐름체인/약품경로/도면/좌표/사양)
 2) 키워드 폴백 — 라벨 전문 검색
 3) LLM 폴백(선택) — ANTHROPIC_API_KEY 환경변수 있으면 질문→SPARQL 생성
"""
import os
import re
import json
from rdflib import Graph, Namespace
from pyproj import Transformer

AX = "http://samaneng.com/ax/onto#"
PREFIXES = """PREFIX ax: <http://samaneng.com/ax/onto#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX geo: <http://www.opengis.net/ont/geosparql#>
"""

SYSTEMS = {
    "SYS_PRETREAT": ["침사", "유량조정"],
    "SYS_BIO": ["생물반응조", "생물반응", "MBR", "공유조", "막분리"],
    "SYS_IPR": ["총인", "IPR", "ipr"],
    "SYS_DEWATER": ["슬러지", "탈수"],
    "SYS_DEODOR": ["탈취"],
    "SYS_UTILITY": ["용수"],
}


class GuksuEngine:
    def __init__(self, ttl_path="data/merged_guksu.ttl"):
        self.g = Graph()
        self.g.parse(ttl_path, format="turtle")
        # 라벨 인덱스 (Asset 위주)
        self.assets = {}  # uri -> {label, tag}
        q = PREFIXES + """SELECT ?a ?l ?t WHERE {
            ?a a ax:Asset ; rdfs:label ?l . OPTIONAL { ?a ax:hasTag ?t } }"""
        for r in self.g.query(q):
            self.assets[str(r.a)] = {"label": str(r.l), "tag": str(r.t) if r.t else ""}
        # WKT(EPSG:5186) → WGS84 캐시
        self._tf = Transformer.from_crs("EPSG:5186", "EPSG:4326", always_xy=True)
        self.coords = {}
        for r in self.g.query(PREFIXES + "SELECT ?s ?w WHERE { ?s geo:asWKT ?w }"):
            m = re.match(r"POINT\(([-\d.]+) ([-\d.]+)\)", str(r.w))
            if m:
                lon, lat = self._tf.transform(float(m.group(1)), float(m.group(2)))
                self.coords[str(r.s)] = [round(lon, 7), round(lat, 7)]

    def _edge_features(self, pairs):
        """[(uriA, labelA, uriB, labelB, edgeLabel)] → 지도 하이라이트 피처"""
        feats = []
        for ua, la, ub, lb, el in pairs:
            ca, cb = self.coords.get(ua), self.coords.get(ub)
            if ca:
                feats.append({"kind": "point", "coord": ca, "label": la})
            if cb:
                feats.append({"kind": "point", "coord": cb, "label": lb})
            if ca and cb:
                feats.append({"kind": "edge", "from": ca, "to": cb,
                              "label": el or f"{la}→{lb}"})
        # 중복 point 제거
        seen, out = set(), []
        for f in feats:
            k = (f["kind"], tuple(f.get("coord") or ()), f.get("label"), tuple(f.get("from") or ()))
            if k in seen: continue
            seen.add(k); out.append(f)
        return out

    # ---------- 공용 ----------
    def _run(self, sparql):
        return [
            {str(v): (str(row[v]) if row[v] is not None else None) for v in row.labels}
            for row in self.g.query(PREFIXES + sparql)
        ]

    def _find_system(self, text):
        for sid, kws in SYSTEMS.items():
            if any(k in text for k in kws):
                return sid
        return None

    def _find_asset(self, text):
        # 태그 직접 매칭 (M-502, DO-203 등)
        m = re.search(r"\b([A-Z]{1,3}-\d{3}(?:-\d)?)\b", text)
        if m:
            tag = m.group(1)
            for uri, a in self.assets.items():
                if a["tag"].startswith(tag) or tag in a["tag"]:
                    return uri, a
        # 라벨 부분 매칭 (긴 라벨 우선)
        cands = [(uri, a) for uri, a in self.assets.items() if a["label"] and a["label"] in text]
        if not cands:
            # 질문 안 명사가 라벨에 포함되는 역방향
            cands = [(uri, a) for uri, a in self.assets.items()
                     if len(a["label"]) >= 3 and any(tok in text for tok in [a["label"]])]
        if not cands:
            for uri, a in self.assets.items():
                core = re.sub(r"[\s()·]", "", a["label"])
                if core and core in re.sub(r"[\s()·]", "", text):
                    cands.append((uri, a))
        if cands:
            return max(cands, key=lambda c: len(c[1]["label"]))
        return None, None

    # ---------- 템플릿들 ----------
    def q_system_counts(self, _):
        sp = """SELECT ?sys ?label (COUNT(?a) AS ?n) WHERE {
            ?a ax:partOf ?sys . ?sys rdfs:label ?label }
            GROUP BY ?sys ?label ORDER BY DESC(?n)"""
        rows = self._run(sp)
        lines = [f"- {r['label']}: {r['n']}대" for r in rows]
        total = sum(int(r["n"]) for r in rows)
        return f"계통별 장비 현황 (총 {total}대):\n" + "\n".join(lines), rows, sp

    def q_asset_system(self, text):
        uri, a = self._find_asset(text)
        if not uri:
            return None
        sp = f"""SELECT ?sysLabel ?tag ?spec WHERE {{
            <{uri}> ax:partOf ?sys . ?sys rdfs:label ?sysLabel .
            OPTIONAL {{ <{uri}> ax:hasTag ?tag }} OPTIONAL {{ <{uri}> ax:spec ?spec }} }}"""
        rows = self._run(sp)
        if not rows:
            return f"'{a['label']}'의 소속 계통 정보가 그래프에 없습니다.", [], sp
        r = rows[0]
        ans = f"{a['label']}({r.get('tag') or a['tag']})은(는) **{r['sysLabel']}** 계통 소속입니다."
        if r.get("spec"):
            ans += f"\n사양: {r['spec']}"
        feats = ([{"kind": "point", "coord": self.coords[uri], "label": a["label"]}]
                 if uri in self.coords else [])
        return ans, rows, sp, feats

    def q_flow_chain(self, text):
        sid = self._find_system(text)
        filt = f"?a ax:partOf <http://samaneng.com/ax/data/guksu/system/{sid}> ." if sid else ""
        sp = f"""SELECT ?a ?b ?al ?bl ?m ?conf WHERE {{
            ?a ax:feeds ?b . ?a rdfs:label ?al . ?b rdfs:label ?bl .
            {filt}
            OPTIONAL {{ ?a ax:conveys ?m }} OPTIONAL {{ ?a ax:readConfidence ?conf }} }}"""
        rows = self._run(sp)
        if not rows:
            return None
        # 동일 엣지(high/med 중복) dedupe — high 우선
        best = {}
        for r in rows:
            k = (r["al"], r["bl"], r.get("m"))
            if k not in best or (r.get("conf") == "high" and best[k].get("conf") != "high"):
                best[k] = r
        rows = list(best.values())
        lines = []
        for r in rows:
            s = f"- {r['al']} → {r['bl']}"
            if r.get("m"):
                s += f" [{r['m']}]"
            if r.get("conf"):
                s += f" ({r['conf']})"
            lines.append(s)
        scope = dict((k, v[0]) for k, v in SYSTEMS.items()).get(sid, "전체")
        feats = self._edge_features([(r["a"], r["al"], r["b"], r["bl"], r.get("m")) for r in rows])
        return f"{scope} 흐름 관계 {len(rows)}건:\n" + "\n".join(lines), rows, sp, feats

    def q_chemical(self, _):
        sp = """SELECT ?a ?b ?al ?bl ?m WHERE {
            ?a ax:feeds ?b ; ax:conveys ?m . ?a rdfs:label ?al . ?b rdfs:label ?bl }"""
        rows = self._run(sp)
        lines = [f"- {r['m']}: {r['al']} → {r['bl']}" for r in rows]
        feats = self._edge_features([(r["a"], r["al"], r["b"], r["bl"], r["m"]) for r in rows])
        located = sum(1 for f in feats if f["kind"] == "edge")
        ans = f"약품/매체 주입 경로 {len(rows)}건:\n" + "\n".join(lines)
        if located:
            ans += f"\n\n(지도에 좌표 확보된 {located}개 경로를 표시했습니다)"
        return ans, rows, sp, feats

    def q_drawing(self, text):
        m = re.search(r"\b([A-Z]{1,2}P?-\d{3})\b", text)
        if not m:
            return None
        no = m.group(1)
        sp = f"""SELECT ?d ?title ?disc ?kind ?page ?docLabel WHERE {{
            ?d ax:drawingNo "{no}" . OPTIONAL {{ ?d rdfs:label ?title }}
            OPTIONAL {{ ?d ax:discipline ?disc }} OPTIONAL {{ ?d ax:drawingKind ?kind }}
            OPTIONAL {{ ?d ax:pdfPage ?page }}
            OPTIONAL {{ ?d ax:documents ?x . ?x rdfs:label ?docLabel }} }}"""
        rows = self._run(sp)
        if not rows:
            return f"도면 {no}이(가) 레지스트리에 없습니다.", [], sp
        r = rows[0]
        ans = f"도면 {no} — {r.get('title') or ''}\n분야 {r.get('disc')}, 성격 {r.get('kind')}, PDF p.{r.get('page')}"
        docs = [x["docLabel"] for x in rows if x.get("docLabel")]
        if docs:
            ans += "\n문서화 대상: " + ", ".join(sorted(set(docs)))
        return ans, rows, sp

    def q_coords(self, _):
        sp = """SELECT ?c (COUNT(?s) AS ?n) (SAMPLE(?l) AS ?ex) WHERE {
            ?s geo:asWKT ?w ; a ?c . OPTIONAL { ?s rdfs:label ?l } } GROUP BY ?c"""
        rows = self._run(sp)
        names = {AX + "Asset": "장비", AX + "Facility": "처리시설", AX + "Manhole": "맨홀", AX + "PipeStation": "관로측점"}
        lines = [f"- {names.get(r['c'], r['c'])}: {r['n']}개 (예: {r.get('ex')})" for r in rows]
        return "실좌표(EPSG:5186 WKT) 보유 객체:\n" + "\n".join(lines), rows, sp

    def q_spec(self, text):
        uri, a = self._find_asset(text)
        if not uri:
            return None
        sp = f"""SELECT ?tag ?spec ?qty ?kw ?sysLabel ?status WHERE {{
            OPTIONAL {{ <{uri}> ax:hasTag ?tag }} OPTIONAL {{ <{uri}> ax:spec ?spec }}
            OPTIONAL {{ <{uri}> ax:quantity ?qty }} OPTIONAL {{ <{uri}> ax:powerKW ?kw }}
            OPTIONAL {{ <{uri}> ax:partOf ?s . ?s rdfs:label ?sysLabel }}
            OPTIONAL {{ <{uri}> ax:tagStatus ?status }} }}"""
        rows = self._run(sp)
        if not rows:
            return None
        r = rows[0]
        parts = [f"{a['label']} ({r.get('tag')})"]
        if r.get("spec"):
            parts.append(f"사양: {r['spec']}")
        if r.get("qty"):
            parts.append(f"수량: {r['qty']}")
        if r.get("kw"):
            parts.append(f"동력: {r['kw']} kW")
        if r.get("sysLabel"):
            parts.append(f"계통: {r['sysLabel']}")
        if r.get("status"):
            parts.append(f"검증상태: {r['status']}")
        feats = ([{"kind": "point", "coord": self.coords[uri], "label": a["label"]}]
                 if uri in self.coords else [])
        return "\n".join(parts), rows, sp, feats

    def q_keyword(self, text):
        toks = [t for t in re.split(r"[\s?？.,]+", text) if len(t) >= 2][:5]
        if not toks:
            return None
        filt = " || ".join(f'CONTAINS(?l, "{t}")' for t in toks)
        sp = f"""SELECT ?s ?l ?c WHERE {{ ?s rdfs:label ?l ; a ?c .
            FILTER({filt}) }} LIMIT 20"""
        rows = self._run(sp)
        if not rows:
            return None
        lines = [f"- {r['l']} ({r['c'].split('#')[-1]})" for r in rows]
        return f"관련 객체 {len(rows)}건:\n" + "\n".join(lines), rows, sp

    # ---------- 라우터 ----------
    TEMPLATES = [
        (r"계통별|계통 현황|장비.*(몇|수|현황)", "q_system_counts"),
        (r"어느 계통|무슨 계통|소속", "q_asset_system"),
        (r"약품|주입", "q_chemical"),
        (r"흐름|체인|경로|순서", "q_flow_chain"),
        (r"[A-Z]{1,2}P?-\d{3}.*(도면|문서|뭐|무엇|내용)|도면.*[A-Z]{1,2}P?-\d{3}", "q_drawing"),
        (r"좌표|위치.*(보유|객체)|실좌표", "q_coords"),
        (r"사양|스펙|동력|용량|수량", "q_spec"),
    ]

    def answer(self, question):
        def pack(out, route):
            if len(out) == 4:
                ans, rows, sp, feats = out
            else:
                ans, rows, sp = out; feats = []
            d = {"answer": ans, "rows": rows, "sparql": sp, "route": route}
            if feats: d["map"] = {"features": feats}
            return d
        for pat, fn in self.TEMPLATES:
            if re.search(pat, question):
                out = getattr(self, fn)(question)
                if out:
                    return pack(out, fn)
        # 키워드 폴백
        out = self.q_keyword(question)
        if out:
            return pack(out, "keyword")
        # LLM 폴백
        llm = self.llm_fallback(question)
        if llm:
            return llm
        return {"answer": "해당 질문에 맞는 질의 템플릿을 찾지 못했습니다. 장비 태그(M-502 등)나 계통명(생물반응조 등)을 포함해 다시 질문해 주세요.",
                "rows": [], "sparql": None, "route": "none"}

    def llm_fallback(self, question):
        """ANTHROPIC_API_KEY 있으면 질문→SPARQL 생성 (없으면 None)"""
        key = os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            return None
        import urllib.request
        schema_hint = ("클래스: ax:Asset(장비, hasTag/spec/quantity/powerKW/partOf/tagStatus), "
                       "ax:Drawing(drawingNo/discipline/drawingKind/pdfPage/documents), "
                       "ax:Manhole, ax:PipeStation, ax:Facility(geo:asWKT), ax:Process. "
                       "관계: ax:feeds(흐름), ax:conveys(매체), ax:partOf(계통), ax:documents. "
                       "계통 URI: http://samaneng.com/ax/data/guksu/system/SYS_{PRETREAT|BIO|IPR|DEWATER|UTILITY|DEODOR}")
        body = json.dumps({
            "model": "claude-sonnet-4-6", "max_tokens": 1000,
            "messages": [{"role": "user", "content":
                f"다음 RDF 스키마에 대한 SPARQL SELECT 쿼리만 출력(설명·백틱 금지). 스키마: {schema_hint}\nPREFIX는 ax:/rdfs:/geo: 사용 가능.\n질문: {question}"}],
        }).encode()
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages", data=body,
            headers={"content-type": "application/json", "x-api-key": key,
                     "anthropic-version": "2023-06-01"})
        try:
            resp = json.load(urllib.request.urlopen(req, timeout=30))
            sparql = resp["content"][0]["text"].strip()
            rows = self._run(sparql)
            lines = [", ".join(f"{k}={v}" for k, v in r.items()) for r in rows[:20]]
            return {"answer": f"LLM 생성 질의 결과 {len(rows)}건:\n" + "\n".join(lines),
                    "rows": rows, "sparql": sparql, "route": "llm"}
        except Exception as e:
            return {"answer": f"LLM 폴백 실패: {e}", "rows": [], "sparql": None, "route": "llm_error"}


if __name__ == "__main__":
    eng = GuksuEngine()
    tests = [
        "계통별 장비 수 알려줘",
        "슬러지탈수 흐름 체인 보여줘",
        "약품 주입 경로는?",
        "공기압축기는 어느 계통이야?",
        "M-009 도면은 무엇을 문서화해?",
        "실좌표 보유 객체는?",
        "협잡물종합처리기 사양 알려줘",
    ]
    for t in tests:
        r = eng.answer(t)
        print(f"\nQ: {t}  [route={r['route']}]")
        print(r["answer"])
