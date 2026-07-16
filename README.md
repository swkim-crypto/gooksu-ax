# 국수 하수처리 GIS+AI (AX 프로젝트)

온톨로지(merged_guksu.ttl, 4,020트리플) 기반 설계정보 질의 + GIS 3패널 뷰어.

## 로컬 실행
```
pip install -r requirements.txt
python app.py          # http://localhost:5000
```

## Render 배포 (깃-렌더)
1. 이 폴더를 GitHub 리포로 push
2. Render → New Web Service → 리포 연결 (render.yaml 자동 인식)
3. 환경변수(선택):
   - `KAKAO_JS_KEY` — 카카오맵 JavaScript 키. 넣으면 배경지도가 카카오로 전환.
     카카오 개발자콘솔에 `https://<서비스명>.onrender.com` 도메인 등록 필요.
   - `ANTHROPIC_API_KEY` — 템플릿 밖 질문을 LLM이 SPARQL로 변환 (김프로님 키).
   - 둘 다 없어도 OSM 배경 + 템플릿 질의로 완전 동작.

## 구조
```
app.py            Flask: UI 서빙 + /api/query, /api/sparql, /api/layers
query_engine.py   rdflib 로드, 자연어→SPARQL (템플릿 7종 + 키워드 + LLM 폴백)
data/merged_guksu.ttl
static/geojson/   pipes/manholes/pumpstations/facility (WGS84)
static/           index.html, app.js, style.css (3패널)
```

## 검증된 질의
계통별 장비 수 / 슬러지탈수 흐름 / 약품 주입 경로 / {장비} 어느 계통 /
M-009 도면 문서화 대상 / 실좌표 보유 객체 / {장비} 사양

## 데이터 검역 이력
- 2026-07-16: C-002 프레임 보정 — 진위치 부지경계(New_block.dxf, 12각형)와 꼭지점
  정합으로 회전 57.541° + 평행이동 확정(잔차 0.000m). 시설·장비 GeoJSON 및
  TTL WKT 51건 변환. 장비 48종 전량 부지경계 내부 확인. C-002는 도곽 정렬을
  위해 57.5° 회전 작도된 도면이었음(수치지도 XREF 포함 전체가 회전 프레임).
- 2026-07-16: 처리시설 레이어를 C-002 원본 DXF에서 재구축(폴리곤51+선285+시설명21).
  기존 SHP판은 주석·범례 심볼이 실좌표에 섞여 축척 왜곡처럼 보이는 문제 → 폐기.
  부지는 공원 복개형(육상트랙·족구장) 지하화 시설로 확인 — 위성사진과 다르게 보이는 이유.
- 2026-07-16: 남한강 위에 표시되던 관로18·맨홀15건을 `*_quarantine.geojson`으로 분리.
  블록 SB003/013/018/105(본망에 부재), SB109 일부. 원본 DXF에서 좌표 이탈된
  잔재로 추정 — `국수처리장_및_관로1227.dxf` 원본 재확인 시 진위 판정 필요.
  merged_guksu.ttl에는 영향 없음(검역구역 WKT 0건).

## 데이터 갱신
merged_guksu.ttl 교체 후 재배포. GeoJSON은 shp_to_geojson.py(별도 산출물)로 재생성.
