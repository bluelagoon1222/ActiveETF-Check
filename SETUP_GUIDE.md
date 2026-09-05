# 액티브 ETF CHECK — 설치 안내 (5단계, 약 15분)

Finviz-Korea 와 같은 방식입니다. 아래 순서대로 링크를 클릭해 진행하시면 됩니다.
저장소 이름은 `ActiveETF-Check` 로 가정했습니다. 다른 이름을 쓰시면 링크의 `ActiveETF-Check` 부분만 바꿔 주세요.

---

## 1단계. 새 저장소 만들기

1. 링크 열기: https://github.com/new
2. **Repository name** 에 입력:
   ```
   ActiveETF-Check
   ```
3. **Public** 선택 (사이트를 공개하려면 필수)
4. 다른 체크박스는 모두 건드리지 말고 맨 아래 초록색 **Create repository** 클릭

---

## 2단계. 파일 업로드

1. 받으신 `ActiveETF-Check.zip` 을 압축 해제합니다. 폴더 안에 `index.html`, `README.md`, `scripts`, `data`, `.github` 등이 보여야 합니다.
2. 링크 열기: https://github.com/bluelagoon1222/ActiveETF-Check/upload/main
3. 압축 해제한 폴더를 열고 **안에 있는 파일과 폴더 전부를 선택(Ctrl+A)** 해서 브라우저 업로드 영역에 끌어다 놓습니다.
   - `.github` 폴더가 함께 올라가야 자동 갱신이 됩니다. 폴더가 보이지 않으면 윈도우 탐색기에서 **보기 → 숨긴 항목 표시** 를 켜 주세요.
   - `.nojekyll` 파일도 함께 올라가야 합니다.
4. 업로드 목록이 다 뜨면 아래 초록색 **Commit changes** 클릭

---

## 3단계. GitHub Pages(공개 사이트) 켜기

1. 링크 열기: https://github.com/bluelagoon1222/ActiveETF-Check/settings/pages
2. **Build and deployment → Source** 를 `Deploy from a branch` 로 두고,
   **Branch** 를 `main` / `/ (root)` 로 선택 후 **Save**
3. 1~2분 후 아래 주소로 사이트가 열립니다. (처음에는 "샘플 데이터" 노란 띠가 보입니다 — 정상입니다)
   ```
   https://bluelagoon1222.github.io/ActiveETF-Check/
   ```

---

## 4단계. 자동 수집 권한 확인 (한 번만)

1. 링크 열기: https://github.com/bluelagoon1222/ActiveETF-Check/settings/actions
2. 맨 아래 **Workflow permissions** 에서 **Read and write permissions** 선택 → **Save**
   (이미 선택되어 있으면 그대로 두시면 됩니다)

---

## 5단계. 첫 수집 실행

1. 링크 열기: https://github.com/bluelagoon1222/ActiveETF-Check/actions/workflows/update.yml
2. 오른쪽 **Run workflow** 버튼 → 다시 초록색 **Run workflow** 클릭 (날짜 칸은 비워 두세요)
3. 5~10분 정도 기다리면 목록에 실행 결과가 나타납니다.
   - **초록 체크** : 성공. 1~2분 후 사이트를 새로고침하면 샘플 띠가 사라지고 실제 데이터가 보입니다.
   - **빨간 X** : 실패. 실행 항목 클릭 → `update` → **Collect data from KRX** 단계를 펼쳐 마지막 20줄 정도를 복사해서 저에게 붙여 주세요. 바로 수정본을 드리겠습니다.

이후에는 평일 18:40(KST) 에 자동으로 갱신되고, 실패하면 21:10 에 한 번 더 시도합니다.

---

## 자주 묻는 것

- **첫날에는 "전일 대비 변경"이 표시되나요?** 예. 첫 실행 때 전 영업일 PDF도 함께 받아 하루치 비교가 바로 표시됩니다. "최근 편입·제외 타임라인"은 스냅샷이 쌓이면서 길어집니다.
- **어떤 ETF가 들어가나요?** KRX 기본정보에서 복제방법이 "액티브", 기초자산 "주식", 시장 "국내"인 ETF 전부입니다. 상장·폐지가 있어도 매일 자동 반영됩니다.
- **그룹 분류가 이상하면?** `scripts/collect.py` 의 `GROUP_RULES` 키워드만 고치면 됩니다. 원하시는 분류를 말씀해 주시면 수정본을 드리겟습니다.
- **해외 주식형 액티브도 넣고 싶으면?** `scripts/collect.py` 의 `INCLUDE_MARKETS = ("국내",)` 를 `("국내", "해외", "국내&해외")` 로 바꾸면 됩니다.
