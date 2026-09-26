# Worklead

원격으로 수행할 수 있는 개발 외주·업무 자동화 의뢰를 발견하고 검토하는 Windows 데스크톱 제품입니다.

현재 상태: 백엔드(수집 엔진·분석·로컬 API), 공통 계약, 데스크톱 화면, Tauri 셸이 구현되어 있습니다. **당근알바 자동 수집은 사이트 조사·정책 확인 전이라 잠겨 있으며**(`permission_pending`), 수동 입력으로 분석할 수 있습니다. 구현·검증·미검증·차단 항목은 [docs/STATUS.md](docs/STATUS.md)에 구분해 두었습니다.

## 제품 범위

- 웹사이트, 풀스택 서비스, 랜딩페이지, 소프트웨어, VBA 및 업무 자동화 의뢰 검토
- 전국 게시 지역 탐색을 목표로 하되, 실제 재택·온라인 수행 조건과 모집 상태는 원문 근거로 별도 판단
- 수동 텍스트/CSV/JSON 입력과 사이트별 어댑터 확장
- 검토 메모, 문의 초안, 영업 진행 및 실제 수금 기록

첫 조사 대상은 당근알바의 허용된 공개 구인글입니다. 자동 수집 권한·정책과 사이트 기능은 아직 검증되지 않았으며, 당근 자동 수집의 초기 상태는 `permission_pending`으로 설계합니다. 자동 지원·메시지 발송·계약 확정·결제는 제품 범위에 포함하지 않습니다.

## 기술 구성 및 소유권

| 경로 | 담당 및 개발 기준 |
| --- | --- |
| `backend/` | 백엔드: Python, FastAPI, Pydantic, SQLite, SQLAlchemy, Alembic |
| `desktop/src/` | 프론트엔드: React, TypeScript, Vite; 별도 프론트엔드 AI 담당 |
| `desktop/src-tauri/` | 데스크톱 통합: Tauri 2, Python sidecar 생명주기 및 Windows 패키징 |
| `contracts/` | 백엔드 관리 공통 스키마, OpenAPI, TypeScript 클라이언트 및 테스트 예제 |
| `scripts/` | 재현 가능한 개발·빌드·패키징 스크립트 |
| `docs/` | 요구사항, 설계, 조사 근거 및 검증 기록 |

최종 사용자는 Python·Node.js·Docker·DB 서버를 별도로 설치하지 않고 Windows 설치 프로그램으로 실행하도록 개발합니다. 라이브러리 버전은 `backend/uv.lock`, `desktop/package-lock.json`, `desktop/src-tauri/Cargo.lock` 으로 고정합니다.

## 개발·검증 명령

```bash
# 백엔드 (Python 3.11–3.13, uv)
cd backend && uv sync && uv run pytest

# 화면 (Node 22+)
cd desktop && npm ci && npm run typecheck && npm test
npm run dev                 # 브라우저 데모 모드: http://127.0.0.1:5173 (?scenario=partial 등)

# 실제 백엔드와 함께 (데모 DB)
scripts/dev.sh

# 계약 재생성 (OpenAPI · 스키마 · fixture · TS 타입)
scripts/gen-contracts.sh

# Windows 설치 파일 (Windows 개발 PC, Rust MSVC 필요)
powershell -ExecutionPolicy Bypass -File scripts\build-windows.ps1
```

## 문서

| 문서 | 내용 |
| --- | --- |
| [docs/STATUS.md](docs/STATUS.md) | 구현 완료 / 검증 / 미검증 / 외부 제약 |
| [docs/BACKEND_PRD.md](docs/BACKEND_PRD.md) | 범위·판정 규칙·완료 기준 |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 구성·수집 흐름·전국 순환·ERD·분석 |
| [docs/INTEGRATION.md](docs/INTEGRATION.md) | 화면 ↔ 로컬 API 계약 |
| [docs/SOURCE_RESEARCH.md](docs/SOURCE_RESEARCH.md) | 당근알바 조사 기록과 미확인 항목 |
| [docs/SOURCE_ADAPTER_GUIDE.md](docs/SOURCE_ADAPTER_GUIDE.md) | 새 사이트 추가 방법 |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | 데이터 위치·장애 대응·백업·복원 |
| [docs/DESIGN.md](docs/DESIGN.md) | 화면 설계 |

## 개발 순서

1. **M0**: 소스 정책·기능 조사, PRD, 기술 설계, 공통 스키마, OpenAPI, 클라이언트, mock 및 프론트엔드 연동 문서
2. **M1**: 로컬 DB, 영속 작업 큐, 수동 입력, 검증된 범위의 소스 어댑터 및 탐색 범위 기록
3. **M2**: 구매 의도·재택·온라인 판정, 근거 기반 추천, 피드백 및 검색
4. **M3**: 선택적 AI, 수익성 시나리오, 문의 초안, 영업 기록, 알림 및 비용 제한
5. **M4**: Windows 패키지, 복구·백업, 계약·통합·패키지 검증 및 운영 안내

사용자가 제공한 상세 요구사항은 [docs/REQUIREMENTS.md](docs/REQUIREMENTS.md)에 보존합니다. 문서에 포함된 과거 관찰은 현재 환경에서 재검증한 결과가 아닙니다. 구현 완료, 로컬 검증, 실제 사이트 검증, Windows 패키지 검증은 별도로 기록합니다.

## 저장소 사용

```powershell
git clone https://github.com/terry4025/worklead.git
cd worklead
git switch -c feat/your-change
```

기본 브랜치는 `main`입니다. 기능 변경은 작업 브랜치에서 진행하고 PR에 변경 범위와 실제 검증 결과를 기록합니다. CI 는 아직 없습니다 (위 검증 명령을 직접 실행).

## 보안 및 데이터 관리 기준

- API 키, 토큰, 인증서, `.env`, 로컬 DB, 로그, 수집 원문 및 백업은 커밋하지 않습니다.
- `.gitignore`는 실수 방지 수단이며, 커밋 전 실제 변경 내용을 검토해야 합니다.
- 런타임 비밀값은 Windows 자격 증명 저장소 등 OS 보안 저장소를 사용하도록 구현합니다.
- 수집 허용 여부가 확인되지 않은 소스는 자동 실행하지 않습니다.
- 로컬 API는 loopback 바인딩과 실행별 인증을 적용하도록 설계합니다.
- 테스트용 합성 데이터와 실제 수집 데이터·성과 통계를 구분합니다.

라이선스는 아직 지정하지 않았습니다. 공개 저장소라는 사실만으로 별도 오픈소스 라이선스를 부여하지 않습니다.
