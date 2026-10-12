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

mod autostart;
mod desktop;
mod notifications;
use desktop::{desktop_exit, desktop_hide, get_desktop_settings, update_desktop_settings};

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
    transition: Mutex<()>,
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
            transition: Mutex::new(()),
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

    fn is_current(&self, generation: u32) -> bool {
        !self.shutting_down.load(Ordering::SeqCst)
            && self.generation.load(Ordering::SeqCst) == generation
    }

    fn prepare_restart(&self) -> Result<u32, String> {
        let _transition = self.transition.lock().expect("transition lock");
        if self.shutting_down.load(Ordering::SeqCst) {
            return Err("앱이 종료 중입니다".into());
        }
        let generation = self.generation.fetch_add(1, Ordering::SeqCst) + 1;
        self.state.send_replace(BackendState::Starting);
        self.kill_child();
        self.restarts.store(0, Ordering::SeqCst);
        Ok(generation)
    }

    fn shutdown(&self) {
        let _transition = self.transition.lock().expect("transition lock");
        self.shutting_down.store(true, Ordering::SeqCst);
        self.generation.fetch_add(1, Ordering::SeqCst);
        self.kill_child();
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
            (Some(port), Some(token)) if port > 0 && !token.is_empty() => {
                backend.state.send_replace(BackendState::Ready(Connection {
                    base_url: format!("http://127.0.0.1:{port}"),
                    token,
                }));
                log::info!("backend ready on port {port}");
            }
            _ => {
                backend.state.send_replace(BackendState::Failed(
                    "백엔드 준비 정보가 올바르지 않습니다".into(),
                ));
            }
        },
        "error" => {
            let msg = parsed
                .message
                .unwrap_or_else(|| "백엔드를 시작하지 못했습니다".into());
            let code = parsed.code.unwrap_or_default();
            log::error!("backend startup error code={code}");
            backend
                .state
                .send_replace(BackendState::Failed(format!("{msg} ({code})")));
        }
        _ => {}
    }
    true
}

fn spawn_backend(app: &AppHandle, expected_generation: Option<u32>) {
    let backend = app.state::<Arc<Backend>>().inner().clone();
    // Starting a replacement and processing old child events share this lock.
    // In particular, a delayed automatic restart must not replace a manual one.
    let transition = backend.transition.lock().expect("transition lock");
    if backend.shutting_down.load(Ordering::SeqCst)
        || expected_generation.is_some_and(|g| !backend.is_current(g))
        || backend.child.lock().expect("child lock").is_some()
    {
        return;
    }
    backend.state.send_replace(BackendState::Starting);
    let generation = backend.generation.fetch_add(1, Ordering::SeqCst) + 1;

    let command = match app.shell().sidecar("worklead-backend") {
        Ok(cmd) => cmd.args(["--sidecar"]),
        Err(err) => {
            backend.state.send_replace(BackendState::Failed(format!(
                "백엔드 실행 파일을 찾지 못했습니다: {err}"
            )));
            return;
        }
    };
    let (mut rx, child) = match command.spawn() {
        Ok(pair) => pair,
        Err(err) => {
            backend.state.send_replace(BackendState::Failed(format!(
                "백엔드를 실행하지 못했습니다: {err}"
            )));
            return;
        }
    };
    *backend.child.lock().expect("child lock") = Some(child);
    drop(transition);

    let app = app.clone();
    tauri::async_runtime::spawn(async move {
        let mut buf = String::new();
        while let Some(event) = rx.recv().await {
            let restart = process_event(&backend, generation, &mut buf, event);
            match restart {
                ChildAction::Continue => {}
                ChildAction::Stop => break,
                ChildAction::Restart(attempt) => {
                    tokio::time::sleep(Duration::from_secs(2u64.pow(attempt))).await;
                    spawn_backend(&app, Some(generation));
                    break;
                }
            }
        }
    });
}

enum ChildAction {
    Continue,
    Stop,
    Restart(u32),
}

fn process_event(
    backend: &Backend,
    generation: u32,
    buf: &mut String,
    event: CommandEvent,
) -> ChildAction {
    let _transition = backend.transition.lock().expect("transition lock");
    if !backend.is_current(generation) {
        return ChildAction::Stop;
    }
    match event {
        CommandEvent::Stdout(bytes) => {
            buf.push_str(&String::from_utf8_lossy(&bytes));
            // 줄 단위(개행 포함/미포함 모두) 처리
            while let Some(idx) = buf.find('\n') {
                let line: String = buf.drain(..=idx).collect();
                handle_line(backend, line.trim());
            }
            if !buf.trim().is_empty() && handle_line(backend, buf.trim()) {
                buf.clear();
            }
        }
        CommandEvent::Stderr(bytes) => {
            // 백엔드가 비밀값을 가린 뒤 stderr 에 경고 이상만 쓴다
            log::warn!("backend: {}", String::from_utf8_lossy(&bytes).trim());
        }
        CommandEvent::Error(err) => log::error!("backend process error: {err}"),
        CommandEvent::Terminated(payload) => {
            backend.child.lock().expect("child lock").take();
            let attempt = backend.restarts.fetch_add(1, Ordering::SeqCst) + 1;
            if attempt > MAX_RESTARTS {
                backend.state.send_replace(BackendState::Failed(format!(
                            "백엔드가 반복해서 종료되어 재시작을 멈췄습니다 (종료 코드 {:?}). 로그 폴더를 확인하세요.",
                            payload.code
                        )));
                return ChildAction::Stop;
            }
            log::warn!(
                "backend exited (code {:?}); restart {attempt}/{MAX_RESTARTS}",
                payload.code
            );
            backend.state.send_replace(BackendState::Starting);
            return ChildAction::Restart(attempt);
        }
        _ => {}
    }
    ChildAction::Continue
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
    let generation = backend.prepare_restart()?;
    spawn_backend(&app, Some(generation));
    Ok(())
}

pub fn run() {
    std::panic::set_hook(Box::new(|info| {
        eprintln!("PANIC: {:?}", info);
    }));
    let backend = Arc::new(Backend::new());
    tauri::Builder::default()
        // 두 번째 실행은 기존 창을 앞으로 가져온다 (백엔드도 데이터 폴더 잠금으로 이중 실행을 막는다)
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| {
            eprintln!("Single instance triggered");
            desktop::show_window(app);
        }))
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_opener::init())
        .manage(backend)
        .invoke_handler(tauri::generate_handler![
            backend_connection,
            restart_backend,
            get_desktop_settings,
            update_desktop_settings,
            desktop_hide,
            desktop_exit
        ])
        .setup(|app| {
            eprintln!("Tauri setup called");
            let data_dir = std::env::var_os("WORKLEAD_DATA_DIR")
                .map(std::path::PathBuf::from)
                .unwrap_or(app.path().local_data_dir()?.join("Worklead"));
            app.manage(desktop::Desktop::load(data_dir));
            if desktop::setup_tray(app.handle()).is_err() {
                log::warn!("tray initialization failed; close will fully exit");
            }
            spawn_backend(app.handle(), None);
            notifications::start(app.handle().clone());
            if std::env::args().any(|arg| arg == "--autostart")
                && app.state::<desktop::Desktop>().should_hide_on_close()
            {
                let _ = desktop_hide(app.handle().clone(), app.state::<desktop::Desktop>());
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                if window
                    .app_handle()
                    .state::<desktop::Desktop>()
                    .should_hide_on_close()
                    && window.hide().is_ok()
                {
                    api.prevent_close();
                }
            }
        })
        .build(tauri::generate_context!())
        .expect("Worklead 셸을 시작하지 못했습니다")
        .run(|app, event| {
            eprintln!("Tauri event: {:?}", event);
            if matches!(event, RunEvent::Exit | RunEvent::ExitRequested { .. }) {
                let backend = app.state::<Arc<Backend>>();
                backend.shutdown();
            }
        });
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ready_does_not_reset_repeated_crash_budget() {
        let backend = Backend::new();
        for attempt in 1..=MAX_RESTARTS {
            backend.restarts.store(attempt, Ordering::SeqCst);
            handle_line(
                &backend,
                r#"{"event":"ready","port":41111,"token":"test-session"}"#,
            );
            assert_eq!(backend.restarts.load(Ordering::SeqCst), attempt);
        }
    }

    #[test]
    fn fourth_crash_stops_even_if_every_process_became_ready() {
        let backend = Backend::new();
        let mut buf = String::new();
        for attempt in 1..=MAX_RESTARTS + 1 {
            let generation = backend.generation.fetch_add(1, Ordering::SeqCst) + 1;
            process_event(
                &backend,
                generation,
                &mut buf,
                CommandEvent::Stdout(
                    br#"{"event":"ready","port":41111,"token":"test-session"}"#.to_vec(),
                ),
            );
            let action = process_event(
                &backend,
                generation,
                &mut buf,
                CommandEvent::Terminated(tauri_plugin_shell::process::TerminatedPayload {
                    code: Some(1),
                    signal: None,
                }),
            );
            if attempt <= MAX_RESTARTS {
                assert!(matches!(action, ChildAction::Restart(n) if n == attempt));
            } else {
                assert!(matches!(action, ChildAction::Stop));
                assert!(matches!(*backend.state.borrow(), BackendState::Failed(_)));
            }
        }
    }

    #[test]
    fn manual_restart_invalidates_old_output_and_pending_restart() {
        let backend = Backend::new();
        let old = backend.generation.load(Ordering::SeqCst);
        handle_line(
            &backend,
            r#"{"event":"ready","port":41111,"token":"old-session"}"#,
        );
        backend.restarts.store(MAX_RESTARTS, Ordering::SeqCst);
        let next = backend.prepare_restart().unwrap();
        assert_ne!(next, old);
        assert_eq!(backend.restarts.load(Ordering::SeqCst), 0);
        assert!(!backend.is_current(old));
        assert!(backend.is_current(next));
        let mut buf = String::new();
        assert!(matches!(
            process_event(
                &backend,
                old,
                &mut buf,
                CommandEvent::Stdout(
                    br#"{"event":"ready","port":41111,"token":"old-session"}"#.to_vec()
                )
            ),
            ChildAction::Stop
        ));
        assert!(matches!(*backend.state.borrow(), BackendState::Starting));
        assert!(matches!(
            process_event(
                &backend,
                old,
                &mut buf,
                CommandEvent::Terminated(tauri_plugin_shell::process::TerminatedPayload {
                    code: Some(1),
                    signal: None
                })
            ),
            ChildAction::Stop
        ));
        assert_eq!(backend.restarts.load(Ordering::SeqCst), 0);
    }

    #[test]
    fn shutdown_blocks_restart_and_late_output() {
        let backend = Backend::new();
        let old = backend.generation.load(Ordering::SeqCst);
        backend.shutdown();
        assert!(!backend.is_current(old));
        assert!(backend.prepare_restart().is_err());
        assert!(matches!(
            process_event(
                &backend,
                old,
                &mut String::new(),
                CommandEvent::Terminated(tauri_plugin_shell::process::TerminatedPayload {
                    code: Some(0),
                    signal: None
                })
            ),
            ChildAction::Stop
        ));
        assert_eq!(backend.restarts.load(Ordering::SeqCst), 0);
    }

    #[test]
    fn empty_token_and_zero_port_never_publish_a_connection() {
        for line in [
            r#"{"event":"ready","port":0,"token":"test-session"}"#,
            r#"{"event":"ready","port":41111,"token":""}"#,
        ] {
            let backend = Backend::new();
            handle_line(&backend, line);
            assert!(matches!(*backend.state.borrow(), BackendState::Failed(_)));
        }
    }
}
