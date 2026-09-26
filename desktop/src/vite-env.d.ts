/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** demo | live. 비우면 Tauri 안에서는 live, 브라우저에서는 demo */
  readonly VITE_DATA_MODE?: string;
  /** 브라우저 개발용 백엔드 주소 (예: http://127.0.0.1:8765). Tauri 에서는 쓰지 않는다 */
  readonly VITE_WORKLEAD_API_URL?: string;
  /** 브라우저 개발용 토큰 (백엔드 --dev + WORKLEAD_DEV_TOKEN). 배포 빌드에 넣지 않는다 */
  readonly VITE_WORKLEAD_TOKEN?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
