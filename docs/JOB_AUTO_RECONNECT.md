# 자동 지원 연결 자동 복구 (2026-10-26)

사용자가 반복 코드 복사·수동 연결을 제거하도록 승인했다. 최초 확장 설치·Chrome 권한·로그인·계정 선택은 한 번 필요하며 브라우저 보안 설정을 대신 변경하지 않는다. 이 변경은 자동 지원 연결에 한정한다. 메시지 감시의 연결 허가는 별도다.

## 동작과 중지

- 자동 연결 기억을 켠 최초 시작은 한 번의 연결 코드를 제공한다. 확장 0.4.0에서 연결하면 계정에 묶인 허가를 기억한다.
- Worklead를 다시 켜면 기억한 설정으로 대기하고, 확장은 같은 계정으로 임시 연결을 다시 받는다. Chrome을 다시 켜면 확장이 전용 비활성 당근 탭 하나를 복구한다. 반복 실패는 최대 4분까지 대기한다. 실제 탐색·제출 지연 보장치는 아니다.
- Worklead의 중지는 자동 재개 의사도 끈다. 다시 시작하면 기억한 허가를 이용하므로 코드가 필요 없다. 저장 연결 해제는 PC 허가를 폐기한다. 확장의 연결 해제도 해당 확장 허가를 삭제한다.
- 사용자가 전용 탭을 닫거나 다른 사이트로 이동하면 무한 탭 생성 없이 멈춘다. 확장 팝업에서 저장 연결 재개를 누른다. 로그인은 사용자가 직접 한다.
- 계정·기본 소개 변경은 영구 보류 상태로 전환한다. DB 복원은 연결을 폐기한다. 사용자가 상황을 확인하고 명시적으로 새 연결을 시작하기 전에는 재개하지 않는다.

## 로컬 연결 계약

기존 주 API·실제 제출 API와 분리된 `127.0.0.1:58347`에서 두 단계 nonce/HMAC 교환을 한다. Host와 고정 확장 Origin을 검사하고, JSON 크기·형식·중복 키를 제한한다. 포트 충돌이나 자격 증명 접근 오류는 연결 실패로 표시한다.

`/hello`에는 client nonce와 보이는 계정 표식의 SHA256만 보낸다. 서버는 짧은 수명의 일회용 server nonce와 서버 proof를 돌려준다. 확장은 서버 proof를 확인한 뒤 `/resume`으로 client proof를 보낸다. 응답의 실행별 URL·토큰도 HMAC으로 확인한다. 장기 연결키는 HTTP, SQLite, 로그, 공개 상태 응답에 포함하지 않는다.

장기 허가는 현재 Windows 사용자의 자격 증명과 확장의 `storage.local`에만 보관한다. Chrome 저장소는 `TRUSTED_CONTEXTS` 접근만 허용한다. 계정 해시는 화면 메뉴·이미지 기반이며 공식 서버 계정 ID의 증거는 아니다. 계정·소개 변경 시 보수적으로 정지할 수 있다.

앱·확장 재시작은 새 작업 연결만 복구한다. 제출 lease·receipt·permit는 세션을 넘겨 복원하지 않는다. 이미 지원·보류·결과 미확인·journal 기록이 있는 공고는 다시 제출하지 않는다. 실제 완료는 같은 공고의 공식 지원 취소 표시로만 판정한다.

## 검증 구분

앱/브라우저 각각 재시작, 동시 복구 이벤트, 잘못된 서버 proof, replay/nonce 만료, 사용자 정지/해제 후 늦은 비동기 완료, 계정 변경·복원·포트 충돌, permit 후 재시작에서 재제출 없음이 검사 대상이다. 합성 loopback/확장 테스트와 실제 로그인 Chrome 연결·지원 완료는 구분한다. 최종 검사·설치·실사이트 상태는 [STATUS](STATUS.md)에 기록한다.

Chrome 공식 근거: [storage](https://developer.chrome.com/docs/extensions/reference/api/storage), [service worker lifecycle](https://developer.chrome.com/docs/extensions/develop/concepts/service-workers/lifecycle), [tabs](https://developer.chrome.com/docs/extensions/reference/api/tabs).
