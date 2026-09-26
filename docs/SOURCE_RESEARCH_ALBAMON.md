# 소스 조사 기록 — 알바몬

> 이 문서는 수집 허가가 아니다. 현재 상태: **조사 완료(2026-09-26), 자동 수집 정책은 사용자 판단 전(`permission_pending`)**.

## 1. 확인 기록

요청 User-Agent: 조사용 `Mozilla/5.0 (compatible; Claude-User)` (AI 에이전트임을 밝힘) / 사이트맵·robots 는 `WorkleadLocal/0.1.0`. 요청 사이 3–4초.

| 시각 (KST) | 요청 | 결과 |
| --- | --- | --- |
| 9/26 오후 | `/robots.txt` | `User-agent: *` 는 `/jobs/detail/*?*keyword`, `/jobs/detail-content`, `/jobs/detail/content`, `/jobs/detail/manager`, `/jobs/detail/print`, `/jobs/detail/photos`, `/jobs/apply/`, `/jobs/town/apply/` 만 금지. Claude-User 그룹은 `/jobs`·`/service-center` 등 허용. `Sitemap: https://www.albamon.com/sitemap.xml` |
| 9/26 오후 | `/jobs/telecommuting` (재택 알바) | 200. 총 452건. 페이지에 목록 데이터(`__NEXT_DATA__`, 20건) 포함. 게시 시각 최신순(12분 전~2시간 전) |
| 9/26 오후 | `/jobs/part?parts=9005·9010·9060·9061·9063·9066·9070` | IT 업직종. 웹·콘텐츠기획 168, 사이트관리·기술지원 260, 프로그래머 256, HTML코딩 32, QA 158, 시스템·네트워크 180, PC 설치·관리 159건. 1쪽 20건이 대략 최근 5–10일 분량 |
| 9/26 오후 | `/jobs/part/sitemap.xml` | 업직종 목록 페이지 167개(코드만). IT 업직종 코드 9005, 9010, 9060, 9061, 9063, 9066, 9070 확인 |
| 9/26 19:06 | `/service-center/terms/member` (회원 이용약관) | 아래 3절 |
| 9/26 오후 | 상세 3건 `/jobs/detail/{공고번호}` | 아래 2절 |

## 2. 구조

**목록** (`__NEXT_DATA__` → `dehydratedState.queries[0].state.data.base`)
- `pagination.totalCount`, `normal.collection[]`(20건): `recruitNo`, `recruitTitle`, `postedDate`("12분전", "9/25"), `closingDate`, `payType`(시급·일급·주급·월급·연봉·**건별**), `pay`, `workingPeriod`, `parts`(업직종 이름), `workplaceArea`(예: **"재택근무"**), `applicationTypes`(온라인지원·간편문자지원·전화연락), `managerPhoneNumber`(**저장하지 않음**).
- 2쪽 이후는 페이지 주소가 아니라 화면 안의 별도 요청으로 불러온다 → **추측하지 않고 1쪽만 사용**. 최신순이라 1시간마다 확인하면 IT 업직종은 대부분, 재택 목록은 최근 약 2시간분을 본다 (부분 탐색으로 표시).

**상세** (`/jobs/detail/{recruitNo}`)
- schema.org `JobPosting` JSON-LD: `title`, `datePosted`, `validThrough`, `employmentType`(FREE_LANCER·CONTRACTOR·PART_TIME·FULL_TIME 등), `jobLocationType: "TELECOMMUTE"`(재택 공고), `jobLocation.address`(시·도, 시·군·구, 도로명), `baseSalary`(value, unitText), `description`(요약).
- `__NEXT_DATA__.props.pageProps.data.viewData`: `content`(상세 본문, HTML 또는 텍스트 — 페이지에 포함되어 있어 robots 가 금지한 `/jobs/detail-content` 를 따로 요청할 필요 없음), `employmentType[]`, `salaryType`, `workPeriod`, `workContentGroups`, `postStatus`(OPEN 등), `recruitStatus`.
- 담당자 연락처·주소 상세는 저장하지 않는다 (시·도, 시·군·구만).

## 3. 약관 (회원 이용약관, 직접 확인)

- 제18조 ④ "회원은 서비스를 이용하여 얻은 정보를 회사의 사전동의 없이 복사, 복제, 번역, 출판, 방송 기타의 방법으로 사용하거나 이를 타인에게 제공할 수 없다."
- 제18조 ⑤ "회원은 본 서비스를 건전한 취업 및 경력관리 이외의 목적으로 사용해서는 안되며" … "8. 사이트의 정보 및 서비스를 이용한 영리 행위"
- 크롤링·자동화 수단을 직접 언급한 조항은 찾지 못했다.
- 해석(판단 아님): 공고를 보고 **그 공고에 지원·계약**하는 것은 서비스 목적에 맞는다. 공고 내용을 자동으로 복사해 모아 두는 것, 공고를 영업 목록으로 쓰는 것은 위 조항과 충돌할 수 있다. **자동 수집 허용 여부는 사용자가 이 조항을 보고 결정한다.**

## 4. 구현 결정 (`backend/worklead/sources/albamon/`)

| 항목 | 결정 |
| --- | --- |
| 탐색 | IT 업직종(9005 웹·콘텐츠기획, 9060 프로그래머, 9061 HTML코딩) + 재택 알바 목록의 **1쪽만** |
| 1차 선별 | 목록 데이터(제목·업직종·급여 형태·근무지)로 개발·자동화 관련만 상세 요청. 국비교육·교육생 모집 광고 제외 |
| 상세 | JSON-LD + 페이지 내 본문(`content`) |
| 저장 제외 | 담당자 전화번호, 도로명 주소, 로고·사진 |
| 요청 제한 | 하루 200회, 간격 5초, 기본 주기 60분 |
| 404/삭제 | 삭제로 확정하지 않음 |
