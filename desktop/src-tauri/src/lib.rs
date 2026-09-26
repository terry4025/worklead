//! Worklead 데스크톱 셸.
//!
//! 책임: 백엔드 sidecar 시작 → 준비 줄 수신 → 웹뷰에 연결 정보 제공, 비정상 종료 시 제한된 재시작,
//! 앱 종료 시 자식 프로세스 정리. 백엔드는 --sidecar 로 실행되어 stdin 이 닫히면 스스로 종료한다.
//!
//! 보안: 토큰은 메모리에만 두고 로그에 쓰지 않는다. 웹뷰에는 프로세스 실행(shell) 권한을 주지 않는다.

use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Manager, RunEvent, State};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;
use tokio::sync::watch;

const MAX_RESTARTS: u32 = 3;
const READY_TIMEOUT: Duration = Duration::from_secs(45);

#[derive(Clone, Serialize)]
pub struct Connection {
    base_url: String,
    token: String,
}

#[derive(Clone)]
enum BackendState {
    Starting,
    Ready(Connection),
    Failed(String),
}

pub struct Backend {
    child: Mutex<Option<CommandChild>>,
    state: watch::Sender<BackendState>,
    restarts: AtomicU32,
    shutting_down: AtomicBool,
    generation: AtomicU32,
}

impl Backend {
    fn new() -> Self {
        let (tx, _rx) = watch::channel(BackendState::Starting);
        Self {
            child: Mutex::new(None),
            state: tx,
            restarts: AtomicU32::new(0),
            shutting_down: AtomicBool::new(false),
            generation: AtomicU32::new(0),
        }
    }

    fn kill_child(&self) {
        if let Some(child) = self.child.lock().expect("child lock").take() {
            let _ = child.kill();
        }
    }
}

/// 백엔드 stdout 의 한 줄 (JSON). 준비 줄에만 포트·토큰이 있다.
#[derive(Deserialize)]
struct ReadyLine {
    event: String,
    port: Option<u16>,
    token: Option<String>,
    code: Option<String>,
    message: Option<String>,
}

fn handle_line(backend: &Backend, line: &str) -> bool {
    let Ok(parsed) = serde_json::from_str::<ReadyLine>(line) else {
        return false;
    };
    match parsed.event.as_str() {
        "ready" => match (parsed.port, parsed.token) {
            (Some(port), Some(token)) => {
                backend.restarts.store(0, Ordering::SeqCst);
                backend.state.send_replace(BackendState::Ready(Connection {
                    base_url: format!("http://127.0.0.1:{port}"),
                    token,
                }));
                log::info!("backend ready on port {port}");
            }
            _ => {
                backend
                    .state
                    .send_replace(BackendState::Failed("백엔드 준비 정보가 올바르지 않습니다".into()));
            }
        },
        "error" => {
            let msg = parsed.message.unwrap_or_else(|| "백엔드를 시작하지 못했습니다".into());
            let code = parsed.code.unwrap_or_default();
            log::error!("backend startup error code={code}");
            backend.state.send_replace(BackendState::Failed(format!("{msg} ({code})")));
        }
        _ => {}
    }
    true
}

fn spawn_backend(app: &AppHandle) {
    let backend = app.state::<Arc<Backend>>().inner().clone();
    backend.state.send_replace(BackendState::Starting);
    let generation = backend.generation.fetch_add(1, Ordering::SeqCst) + 1;

    let command = match app.shell().sidecar("worklead-backend") {
        Ok(cmd) => cmd.args(["--sidecar"]),
        Err(err) => {
            backend
                .state
                .send_replace(BackendState::Failed(format!("백엔드 실행 파일을 찾지 못했습니다: {err}")));
            return;
        }
    };
    let (mut rx, child) = match command.spawn() {
        Ok(pair) => pair,
        Err(err) => {
            backend
                .state
                .send_replace(BackendState::Failed(format!("백엔드를 실행하지 못했습니다: {err}")));
            return;
        }
    };
    *backend.child.lock().expect("child lock") = Some(child);

    let app = app.clone();
    tauri::async_runtime::spawn(async move {
        let mut buf = String::new();
        while let Some(event) = rx.recv().await {
            match event {
                CommandEvent::Stdout(bytes) => {
                    buf.push_str(&String::from_utf8_lossy(&bytes));
                    // 줄 단위(개행 포함/미포함 모두) 처리
                    while let Some(idx) = buf.find('\n') {
                        let line: String = buf.drain(..=idx).collect();
                        handle_line(&backend, line.trim());
                    }
                    if !buf.trim().is_empty() && handle_line(&backend, buf.trim()) {
                        buf.clear();
                    }
                }
                CommandEvent::Stderr(bytes) => {
                    // 백엔드가 비밀값을 가린 뒤 stderr 에 경고 이상만 쓴다
                    log::warn!("backend: {}", String::from_utf8_lossy(&bytes).trim());
                }
                CommandEvent::Error(err) => log::error!("backend process error: {err}"),
                CommandEvent::Terminated(payload) => {
                    if backend.generation.load(Ordering::SeqCst) != generation {
                        break; // 재시작으로 교체된 이전 프로세스
                    }
                    backend.child.lock().expect("child lock").take();
                    if backend.shutting_down.load(Ordering::SeqCst) {
                        break;
                    }
                    let attempt = backend.restarts.fetch_add(1, Ordering::SeqCst) + 1;
                    if attempt > MAX_RESTARTS {
                        backend.state.send_replace(BackendState::Failed(format!(
                            "백엔드가 반복해서 종료되어 재시작을 멈췄습니다 (종료 코드 {:?}). 로그 폴더를 확인하세요.",
                            payload.code
                        )));
                        break;
                    }
                    log::warn!("backend exited (code {:?}); restart {attempt}/{MAX_RESTARTS}", payload.code);
                    backend.state.send_replace(BackendState::Starting);
                    tokio::time::sleep(Duration::from_secs(2u64.pow(attempt))).await;
                    if !backend.shutting_down.load(Ordering::SeqCst) {
                        spawn_backend(&app);
                    }
                    break;
                }
                _ => {}
            }
        }
    });
}

/// 웹뷰가 호출: 백엔드가 준비될 때까지 기다려 주소·토큰을 돌려준다.
#[tauri::command]
async fn backend_connection(backend: State<'_, Arc<Backend>>) -> Result<Connection, String> {
    let mut rx = backend.state.subscribe();
    let wait = async {
        loop {
            let current = rx.borrow_and_update().clone();
            match current {
                BackendState::Ready(conn) => return Ok(conn),
                BackendState::Failed(msg) => return Err(msg),
                BackendState::Starting => {}
            }
            if rx.changed().await.is_err() {
                return Err("백엔드 상태를 확인할 수 없습니다".to_string());
            }
        }
    };
    tokio::time::timeout(READY_TIMEOUT, wait)
        .await
        .map_err(|_| "로컬 서비스 시작 시간이 초과되었습니다".to_string())?
}

/// 웹뷰가 호출: 사용자가 "서비스 다시 시작" 을 눌렀을 때.
#[tauri::command]
async fn restart_backend(app: AppHandle, backend: State<'_, Arc<Backend>>) -> Result<(), String> {
    backend.generation.fetch_add(1, Ordering::SeqCst);
    backend.kill_child();
    backend.restarts.store(0, Ordering::SeqCst);
    spawn_backend(&app);
    Ok(())
}

pub fn run() {
    let backend = Arc::new(Backend::new());
    tauri::Builder::default()
        // 두 번째 실행은 기존 창을 앞으로 가져온다 (백엔드도 데이터 폴더 잠금으로 이중 실행을 막는다)
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            if let Some(window) = app.get_webview_window("main") {
                let _ = window.unminimize();
                let _ = window.set_focus();
            }
        }))
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_notification::init())
        .manage(backend)
        .invoke_handler(tauri::generate_handler![backend_connection, restart_backend])
        .setup(|app| {
            spawn_backend(app.handle());
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("Worklead 셸을 시작하지 못했습니다")
        .run(|app, event| {
            if let RunEvent::Exit = event {
                let backend = app.state::<Arc<Backend>>();
                backend.shutting_down.store(true, Ordering::SeqCst);
                backend.kill_child();
            }
        });
}
