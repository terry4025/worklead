# 당근 카페 M0 DOM 표본

2026-10-12 전용 시스템 Edge에서 실제로 읽은 공개 화면을 비식별화했다.
`docs/CAFE_RESEARCH.md`와 `provenance.json`에 URL·시각·범위를 기록한다.

- home: 카페 홈과 게시판 navigation
- board: 구인구직 게시판
- post: 기존 글 상세
- comments: 기존 댓글과 댓글 작성 UI
- editor: 로그인 후 빈 ProseMirror 글쓰기 편집기

태그·class·role은 관찰한 구조다. 사용자 콘텐츠·닉네임·카페 이름·숫자·링크는 합성 값이다. 모든 script와 이미지/리소스 URL·이벤트 속성·입력값을 제거했으므로 실제 사이트에 요청하지 않는다. 이 표본 자체는 로그인·게시가 동작하는 가짜 카페 서버가 아니다. 작성 화면을 읽었지만 제목·본문을 입력하거나 게시하지 않았다.
