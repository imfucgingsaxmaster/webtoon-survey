# Webtoon Radar

네이버웹툰과 네이버시리즈의 **허용된 조사 자료**를 저장·탐색·분석하는 Python 프로젝트입니다. Streamlit 대시보드, SQLite 저장소, Excel 및 PDF 요약을 제공합니다.

## 현재 데이터와 구현 범위

- 윤승준·김예은 제공 조사 PDF 4~6쪽에서 전사한 네이버웹툰 **105개 작품·요일 항목, 고유 작품 102개**를 포함합니다.
- 원문 관측일과 정확한 순위가 명시되지 않아 날짜·순위는 비워 두었습니다. 기재 순서는 순위가 아닙니다.
- 네이버시리즈는 같은 입력 형식과 화면 필터를 지원합니다. **실측 자료 0건**이며 아직 수집 연결은 없습니다.
- 실시간 크롤러, 비공개 API, 로그인 우회, 조회수·매출 추정 기능은 없습니다.
- 원문 제목 표기를 보존합니다. 띄어쓰기 제거·대소문자 통일은 고유 작품 집계에만 적용합니다. 장르 `무협사극`만 `무협/사극`으로 통일했습니다.

## 실행

Python 3.12 이상에서:

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python -m unittest tests -v
python radar.py
streamlit run app.py
```

`outputs/`에 SQLite, Excel, PDF, 원시 JSON 및 집계 JSON을 생성합니다. 대시보드의 다운로드 버튼은 현재 필터 결과를 내보냅니다. Linux에서 한글 PDF는 `fonts-nanum`을 설치하거나 `RADAR_FONT`에 사용 가능한 한글 TTF 경로를 지정하세요.

## 매주 자동 실행

GitHub Actions는 매주 **월요일 09:17 한국시간**(UTC 월요일 00:17), main 게시 시, 또는 수동 실행 시 동작합니다. 실행 후 Actions의 Artifacts에서 결과를 받습니다. 플랫폼 새 데이터를 스스로 수집하는 작업이 아니라 등록된 승인 자료를 다시 분석하는 작업입니다. 새 입력이 없으면 기존 자료를 재집계하며 새 관측값을 만들지 않습니다.

각 실행의 데이터베이스는 저장소에 있는 전체 CSV 이력으로 재구성됩니다. 이력을 보존하려면 이전 스냅샷 행을 CSV에서 삭제하지 마세요. 동일 스냅샷 재입력은 중복 없이 처리하며, 기존 내용을 수정하려면 새 snapshot_id를 사용합니다. 공개 저장소의 예약 작업은 GitHub 정책상 장기간 활동이 없으면 비활성화될 수 있고 예약 시각이 지연될 수 있습니다.

## 허용 자료 등록

1. 직접 제공한 조사 자료 또는 이용·분석 권한이 확인된 내보내기 파일만 사용합니다.
2. `sources.json`에 출처, 허용 플랫폼, 구체적인 이용 근거, 승인 여부를 등록합니다. 허락이 없는 자료는 승인하지 마세요.
3. `input_template.csv` 형식에 맞게 CSV에 추가합니다. source_id, snapshot_id, 작품명, 요일, 장르, 원문 위치를 유지합니다.
4. 실제 관측일·순위를 확인한 경우에만 해당 값을 입력합니다. 네이버시리즈 자료도 같은 절차를 따릅니다.
5. 공개 저장소에 올리는 모든 CSV와 PDF는 공개됩니다. API 키, 개인정보, 비공개 계약 자료는 넣지 마세요.

현재 허용 범위는 사용자가 제공한 연구 자료입니다. 사이트의 공개 열람 가능 여부나 robots.txt만으로 수집·재배포 허가를 판단하지 않습니다. 네이버 데이터랩은 검색 관심도의 상대값을 제공하며 웹툰 조회수·매출·공식 순위와 다른 지표입니다. 향후 연결 시 별도 이용 조건과 인증을 검토합니다.

## 역할분담

| 담당 | 역할 |
|---|---|
| 허영은·지수민 | 유사 조사·분석 시스템 조사 |
| 윤승준·김예은 | 인기작 중심 웹툰 트렌드 폭넓게 조사 |
| 주민재 | UI/UX 사례 조사 및 탐색 화면 제안 |
| 김주현 | 자료 총합·정리 및 통합 보고서 작성 |
| 이의강 | 첨부 자료 기준 Python 기초 처리·시각화 코드 조사 |

## 조사 원본

- [윤승준·김예은 트렌드 조사](trend_research.pdf)
- [이의강 기초 코드 보고서](coding_research.pdf)
- [주민재 UI/UX 조사](ux_research.pdf)
- [허영은·지수민 유사 시스템 조사](systems_research.pdf)

원본 문서와 포함된 제3자 콘텐츠의 권리는 각 권리자에게 있습니다. 학습·과제 자료의 출처 확인을 위한 원본입니다.

## 참고

- [WEBTOON Advertising Data Center](https://advertising.webtoon.com/en/data-center): 서비스·이용자 규모를 보는 유사 대시보드
- [네이버 데이터랩 공식 안내](https://developers.naver.com/products/service-api/datalab/datalab.md): 검색 관심도와 실제 조회수의 차이
- [GitHub 예약 실행 안내](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule)
- [Streamlit 문서](https://docs.streamlit.io/)
