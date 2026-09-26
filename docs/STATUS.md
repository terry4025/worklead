# 구현·검증 상태 (2026-09-26)

"검증"은 이 저장소의 자동 테스트나 이 개발 환경에서 실제로 실행해 확인한 것만 적는다.

## 구현 완료 + 이 환경에서 검증

| 영역 | 내용 | 근거 |
| --- | --- | --- |
| 백엔드 API | `/v1` 전 엔드포인트, 토큰·Host·Origin 검사, 오류 형식, 커서, 대기열, SSE 이어받기/리셋 | `backend/tests/test_api.py` |
| 수집 엔진 | 허용 호스트, robots.txt, 예산, 403/CAPTCHA 정지, 429 유예, 파싱 실패 구분, 전국 순환·주기·이어받기, 강제 종료 복구, 예약 멱등 | `backend/tests/test_engine.py` (합성 사이트) |
| 분석 | 축별 판정·근거 구간, 보수·날짜, 위험 신호, 점수·미평가, 적격성, 수익성, 내 확인 반영 | `backend/tests/test_analysis.py` |
| 저장 | 마이그레이션(사전 백업), 온라인 백업, 무결성 확인 복원 | `test_backup_restore_roundtrip` |
| 계약 | OpenAPI·스키마·엔진 생성 fixture·TS 클라이언트, fixture 재검증 | `test_contracts.py`, `desktop/src/__tests__/contract-mapper.test.ts` |
| 화면 | 목록·상세·수집·설정·단축키·상태별 화면, 데모 시나리오 7종 | Vitest 24건, Playwright 스크린샷(1024–1536px, 다크 모드, 좁은 창 겹침) |
| 화면 ↔ 실제 백엔드 | live 모드로 목록·상세·메모 저장·관심 표시·수동 입력·재확인 거부·SSE 새 리드 배너 | Playwright + `--dev --demo` 백엔드 |
| 패키징(리눅스로 대체 확인) | PyInstaller sidecar 가 마이그레이션·준비 줄·인증·이중 실행 거부·stdin 종료 동작 | `/tmp` 수동 테스트 기록 (커밋 메시지) |
| Tauri 셸 | `cargo check/build` (Linux), Xvfb 에서 앱 실행 → sidecar 시작 → 화면 live 연결(실시간), 앱 종료 시 백엔드 프로세스 0개 | 수동 확인 |

## 구현했으나 미검증

| 항목 | 이유 |
| --- | --- |
| Windows 설치 파일(NSIS)·WebView2 부트스트랩·한글/공백 경로·비관리자 설치 | Windows 빌드 환경 없음. `scripts/build-windows.ps1` 로 수행 필요 |
| Windows 에서 sidecar 창 숨김·종료 정리 | Linux 에서만 확인 |
| 성능 목표 (10만 리드 p95 500ms) | 측정하지 않음 |
| 품질(추천 정밀도·재택/구매 오탐) | 사람이 라벨링한 검수 표본이 없음 → 미검증 |

## 미구현

| 항목 | 메모 |
| --- | --- |
| 실제 외부 AI 제공자 | 인터페이스·캐시·실패 처리만 있음 (현재 규칙 분석만) |
| API 키 저장(Windows 자격 증명 저장소) | 설정은 `key_configured=false` 고정 |
| 네이티브 알림 소비자, 트레이 상주, 자동 시작 | 알림은 앱 안 토스트만. 창을 닫으면 완전 종료 |
| 화면의 백업·복원 버튼 | 백엔드 함수만 존재 |
| 설치 제거 시 데이터 삭제 선택 | 폴더 수동 삭제 |
| 유사 공고 연결의 사용자 확인·병합 해제 화면 | 후보 관계만 표시 |

## 외부 제약으로 차단

| 항목 | 상태 |
| --- | --- |
| 당근알바 조사(약관·robots·검색·상세 구조·지역 목록) | 개발 환경 네트워크 정책이 `jobs.daangn.com`·`www.daangn.com`·`cs.kr.karrotmarket.com` 을 차단 → 미확인. 검색 엔진 색인의 주소 형태만 간접 기록 ([SOURCE_RESEARCH.md](SOURCE_RESEARCH.md)) |
| 당근알바 자동 수집 | `permission_pending` + 조사 프로필 미검증 → 실행 불가(의도된 잠금). **당근 자동 수집 완료를 주장하지 않는다** |
| 전국 탐색 범위 검증 | 지역 목록을 확인하지 못해 미검증. 엔진의 전국 순환은 합성 사이트로만 검증 |
