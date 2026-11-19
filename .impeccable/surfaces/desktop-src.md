---
version: 1
slug: "desktop-src"
primary_target: "desktop/src"
related_targets: []
---

# Worklead 데스크톱 작업 화면

Scope: desktop/src 전체 작업 화면 (리드, 지원 준비·메시지, 카페, 설정). Mode: Operate.
Audience: 1인 개발 프리랜서 본인. 주로 자동 지원 결과를 확인하고, 검토 필요 공고만 직접 판단한다.

## Direction contract

THESIS: 공고 하나하나와 자동 지원은 정해진 역을 지나는 노선이다. 흰 카드 격자와 회색 대시보드 대신, 지하철 노선도의 굵은 선과 역 표시로 '지금 어디까지 왔는지'를 읽게 한다.
OWN-WORLD: 1호선 남색 상단 노선 띠, 차가운 회청 바탕과 흰 작업면, 상태색은 2호선 초록(지원·추천), 3호선 주황(검토·보류), 신분당선 빨강(문제)만. 역은 흰 속 원과 굵은 테, 선은 4px 둥근 끝. Pretendard, 숫자는 tabular.
STORY: 앱을 열면 오늘 자동 지원이 몇 건 나갔고 무엇이 막혔는지 먼저 알고, 그 아래 검토할 공고를 판단한다.
FIRST VIEWPORT: 남색 띠(브랜드·작업 공간 탭·수집 상태) → 자동 지원 노선 띠(운행 상태, 대기 후보, 오늘 지원 n/상한, 보류, 결과 미확인, 최근 도착 지원 3건) → 보기 탭·필터 → 목록 | 상세. 상세 머리에 공고 여정 노선(발견→판정→지원→진행).
FORM: 서울 지하철 노선도, 근거 목록 4번째, seed 5a50a0d5. Raise(탑승권·게이트 보드): 바뀐 행은 자리를 옮기지 않고 강조를 유지한다.
FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, DESIGN.md, and every shipping raster carrying its provenance
