# ACTIVE ETF CHECK — 운영 메모

이 파일은 이 저장소를 처음 보는 사람(또는 새 Claude 대화)이 5분 안에 상황을 파악하기 위한 문서입니다.
사이트를 수정할 때는 이 파일을 먼저 읽고, 구조나 정책이 바뀌면 이 파일도 함께 고칩니다.

## 무엇을 하는 사이트인가

이름에 '액티브'가 들어가는 국내 상장 주식형 ETF 155종(국내 86 · 해외 69)을 섹터 그룹으로 묶어
한 페이지에서 비교합니다. 목적은 PB가 "같은 섹터에서 어느 액티브 ETF의 매니저가 무엇에 베팅하고 있는가"를
한눈에 보는 것입니다. 핵심 화면은 세 가지입니다.

- 성과 비교표: 그룹 내 ETF의 기간 수익률과 비교지수 대비 초과수익
- 구성종목 비교 매트릭스: 행=종목, 열=ETF. 셀에 당일 비중, 지수 대비 초과·과소 비중, 전일 대비 변화
- 편입·제외 타임라인: 일별 스냅샷에서 감지한 NEW / OUT 이벤트

- 공개 주소: https://bluelagoon1222.github.io/ActiveETF-Check/
- 저장소: https://github.com/bluelagoon1222/ActiveETF-Check

## 파일 구조

| 경로 | 역할 |
| --- | --- |
| `index.html` | 단일 페이지 UI 전부 (HTML+CSS+JS). 외부 라이브러리 없음 |
| `scripts/collect.py` | 데이터 수집·가공. 결과를 `data/`에 기록 |
| `.github/workflows/update.yml` | 평일 08:50 / 18:40 / 21:10 (KST) 자동 실행 + 수동 실행 |
| `data/latest.json` | 화면이 읽는 최종 데이터 |
| `data/status.json` | 마지막 실행 요약 (성공 여부, 기준일, 수집 종수) |
| `data/run_log.txt` | 실행 로그. 문제 진단은 여기부터 |
| `data/pdf/YYYYMMDD.json` | 일별 구성종목 스냅샷 (70일 보관). 전일 대비 비교의 원천 |
| `data/cache/` | 야후 종목코드·가격 캐시, 벤치마크·추정 플래그 캐시 |

## 데이터 소스

- 네이버 금융: ETF 목록, 운용사·비교지수·보수, 기간 수익률, 추적오차, 섹터 비중
- WiseReport(FnGuide): CU 구성종목 전체, 일별 종가·NAV
- 야후 파이낸스: 해외 종목 가격 (비중 추정용)

KRX 데이터포털은 로그인이 필요하고, ETF CHECK는 비회원 20종목 제한·요청 제한이 있어 사용하지 않습니다.

## 알아둘 제약

1. **해외 ETF는 공시에 비중이 없습니다.** 주식수 × 야후 가격으로 추정하며 화면에 "추정 n/m"으로
   가격 확인 종목 수를 표시합니다. 추정치는 다음 실행에서 이어받아(주식수 변화율 반영) 빈칸으로
   돌아가지 않습니다. CU 주식수는 펀드 전체가 아니라 설정단위 기준이므로 순자산과 직접 대조할 수 없습니다.
2. **지수 대비 비중은 근사치입니다.** 같은 지수를 추종하는 대표 패시브 ETF의 구성종목을 '지수 대용'으로
   씁니다. 대용이 없는 테마형은 같은 그룹 평균 대비로 표시합니다.
3. **실행 시간 제한.** 스크립트 내부 33분, Actions 45분. 데이터 서버가 느릴 때는 단계별 시간 배분과
   fail-fast로 넘기고, 못 받은 항목은 직전 데이터를 유지하며 화면에 "전일자 유지"로 표기합니다.
4. **GitHub 무료 예약 실행은 지연됩니다.** 수십 분에서 수 시간까지 밀릴 수 있습니다. 급하면 Actions에서
   수동 실행(Run workflow)하세요.
5. **외부 CDN을 쓰지 않습니다.** 사내망에서 차단되어 글자가 깨지고 차트가 사라졌던 이력이 있습니다.
   폰트는 시스템 기본(맑은 고딕 / Apple SD Gothic Neo / 돋움), 차트는 인라인 SVG로 직접 그립니다.

## 화면 규칙

- 제목 `ACTIVE ETF CHECK` 중앙 정렬, 46px 굵게. 네이비 `#043B72` + ETF는 오렌지 `#F58220` (미래에셋 CI)
- 국내/해외 전환 버튼과 섹터 탭도 중앙 정렬, 크게. 종목 비중 히트맵은 파란색 농도 유지
- 글씨가 길면 크기를 줄이지 말고 두 줄로 넘김
- 하단 면책 문구 유지: 공시 데이터를 정리한 참고자료이며 투자 권유가 아님

## 수정하는 방법

1. 수정본을 받으면 GitHub 웹에서 덮어쓰기 업로드
   - 루트: https://github.com/bluelagoon1222/ActiveETF-Check/upload/main
   - scripts: https://github.com/bluelagoon1222/ActiveETF-Check/upload/main/scripts
2. 화면(`index.html`)만 고쳤다면 1~2분 뒤 사이트에 바로 반영 (브라우저 Ctrl+F5)
3. 수집(`collect.py`)을 고쳤다면 Actions에서 수동 실행
   - https://github.com/bluelagoon1222/ActiveETF-Check/actions/workflows/update.yml
4. 결과 확인은 `data/status.json`과 `data/run_log.txt`

## Claude에게 수정을 부탁할 때

Claude는 이 저장소를 `git clone`으로 읽을 수 있습니다(읽기 전용). 새 대화에서도 아래 한 줄이면 됩니다.

> https://github.com/bluelagoon1222/ActiveETF-Check 를 clone해서 PROJECT_NOTES.md 를 읽고, (요청 내용)

Claude 환경에서는 한국 데이터 사이트와 GitHub API·raw·Pages에 접근할 수 없고,
개인 액세스 토큰(PAT)도 쓸 수 없습니다. 따라서 커밋은 사람이 웹 업로드로 합니다.

## 변경 이력

- v4: 네이버 + WiseReport로 전환해 첫 정상 가동 (국내 85종, 약 6분)
- v5~v7: 해외 주식형 액티브 추가 (155종), 국내/해외 전환 UI, 08:50 실행 추가
- v8~v11: 해외 ETF 비중 추정(야후) 도입, 요청 실패와 검색 실패 구분, 가격 일괄 조회
- v12: 추정 비중을 실행 간 이어받아 공백 방지, 비중 없는 ETF 우선 계산
- v13: 응답 지연(느린 서버) 감지, 수집 단계별 시간 배분
