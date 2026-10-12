//! OS settings are local to this PC and independent of a restored lead database.
use serde::{Deserialize, Serialize};
use std::{
    fs,
    io::Write,
    path::{Path, PathBuf},
    sync::{
        atomic::{AtomicBool, Ordering},
        Mutex,
    },
};
use tauri::{AppHandle, Manager, State};

#[derive(Clone, Default, Deserialize, Serialize)]
#[serde(default, deny_unknown_fields)]
pub(crate) struct Preferences {
    pub close_to_tray: bool,
    pub native_notifications: bool,
    pub notifications_enabled_at: Option<String>,
}

pub(crate) struct Desktop {
    pub prefs: Mutex<Preferences>,
    pub dir: PathBuf,
    pub load_error: Option<String>,
    pub tray_available: AtomicBool,
}

impl Desktop {
    pub fn load(dir: PathBuf) -> Self {
        let loaded = read_json::<Preferences>(&dir.join("desktop-settings.json"));
        let (prefs, load_error) = match loaded {
            Ok(Some(p)) => (p, None),
            Ok(None) => (Preferences::default(), None),
            Err(_) => (Preferences::default(), Some("데스크톱 설정 파일을 읽지 못했습니다. 데이터 폴더의 desktop-settings.json을 확인하세요.".into())),
        };
        Self {
            prefs: Mutex::new(prefs),
            dir,
            load_error,
            tray_available: AtomicBool::new(false),
        }
    }

    pub fn should_hide_on_close(&self) -> bool {
        self.tray_available.load(Ordering::SeqCst)
            && self.prefs.lock().expect("prefs lock").close_to_tray
    }
}

pub(crate) fn read_json<T: serde::de::DeserializeOwned>(path: &Path) -> Result<Option<T>, String> {
    match fs::read(path) {
        Ok(bytes) => serde_json::from_slice(&bytes)
            .map(Some)
            .map_err(|_| "파일 형식 오류".into()),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(_) => Err("파일 읽기 오류".into()),
    }
}

pub(crate) fn write_json<T: Serialize>(path: &Path, value: &T) -> Result<(), String> {
    let parent = path.parent().ok_or("저장 경로 오류")?;
    fs::create_dir_all(parent).map_err(|_| "데스크톱 데이터 폴더를 만들지 못했습니다")?;
    let temp = parent.join(format!(
        ".desktop-{}-{}.tmp",
        std::process::id(),
        chrono::Utc::now().timestamp_nanos_opt().unwrap_or_default()
    ));
    let result = (|| {
        let bytes = serde_json::to_vec(value).map_err(|_| "데스크톱 데이터 변환 실패")?;
        let mut file = fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&temp)
            .map_err(|_| "데스크톱 데이터 저장 실패")?;
        file.write_all(&bytes)
            .and_then(|_| file.sync_all())
            .map_err(|_| "데스크톱 데이터 저장 실패")?;
        drop(file);
        #[cfg(windows)]
        {
            use std::os::windows::ffi::OsStrExt;
            use windows::{
                core::PCWSTR,
                Win32::Storage::FileSystem::{
                    MoveFileExW, MOVEFILE_REPLACE_EXISTING, MOVEFILE_WRITE_THROUGH,
                },
            };
            let from: Vec<u16> = temp.as_os_str().encode_wide().chain(Some(0)).collect();
            let to: Vec<u16> = path.as_os_str().encode_wide().chain(Some(0)).collect();
            unsafe {
                MoveFileExW(
                    PCWSTR(from.as_ptr()),
                    PCWSTR(to.as_ptr()),
                    MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH,
                )
            }
            .map_err(|_| "데스크톱 데이터 교체 실패")?;
        }
        #[cfg(not(windows))]
        fs::rename(&temp, path).map_err(|_| "데스크톱 데이터 교체 실패")?;
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_file(temp);
    }
    result
}

#[derive(Serialize)]
pub(crate) struct DesktopSettings {
    close_to_tray: bool,
    native_notifications: bool,
    autostart_enabled: bool,
    tray_available: bool,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct DesktopPatch {
    close_to_tray: Option<bool>,
    native_notifications: Option<bool>,
    autostart_enabled: Option<bool>,
}

fn view(desktop: &Desktop, prefs: &Preferences) -> Result<DesktopSettings, String> {
    Ok(DesktopSettings {
        close_to_tray: prefs.close_to_tray,
        native_notifications: prefs.native_notifications,
        autostart_enabled: crate::autostart::is_enabled()?,
        tray_available: desktop.tray_available.load(Ordering::SeqCst),
    })
}

#[tauri::command]
pub(crate) fn get_desktop_settings(desktop: State<'_, Desktop>) -> Result<DesktopSettings, String> {
    if let Some(err) = &desktop.load_error {
        return Err(err.clone());
    }
    view(&desktop, &desktop.prefs.lock().expect("prefs lock"))
}

#[tauri::command]
pub(crate) fn update_desktop_settings(
    desktop: State<'_, Desktop>,
    patch: DesktopPatch,
) -> Result<DesktopSettings, String> {
    if let Some(err) = &desktop.load_error {
        return Err(err.clone());
    }
    let mut prefs = desktop.prefs.lock().expect("prefs lock");
    let mut next = prefs.clone();
    if let Some(value) = patch.close_to_tray {
        if value && !desktop.tray_available.load(Ordering::SeqCst) {
            return Err("트레이를 사용할 수 없어 창 닫기 시 숨김을 켤 수 없습니다".into());
        }
        next.close_to_tray = value;
    }
    if let Some(value) = patch.native_notifications {
        if value && !cfg!(windows) {
            return Err("Windows 네이티브 알림만 지원합니다".into());
        }
        if value && !next.native_notifications {
            // Validate the durable ledger before allowing notifications.
            super::notifications::load_ledger(&desktop.dir)?;
            next.notifications_enabled_at = Some(chrono::Utc::now().to_rfc3339());
        }
        next.native_notifications = value;
    }
    let old_auto = crate::autostart::is_enabled()?;
    let change_auto = patch.autostart_enabled.filter(|wanted| *wanted != old_auto);
    let path = desktop.dir.join("desktop-settings.json");
    // Write preferences first: disk failure must not change Windows startup registration.
    write_json(&path, &next)?;
    if let Some(wanted) = change_auto {
        if let Err(error) = crate::autostart::set_enabled(wanted) {
            if write_json(&path, &*prefs).is_err() {
                *prefs = next;
                return Err("자동 시작 변경에 실패했고 데스크톱 설정을 되돌리지 못했습니다. 설정을 다시 불러오세요.".into());
            }
            return Err(error);
        }
    }
    *prefs = next;
    view(&desktop, &prefs)
}

pub(crate) fn show_window(app: &AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.unminimize();
        let _ = window.set_focus();
    }
}

#[tauri::command]
pub(crate) fn desktop_hide(app: AppHandle, desktop: State<'_, Desktop>) -> Result<(), String> {
    if !desktop.tray_available.load(Ordering::SeqCst) {
        return Err("트레이를 사용할 수 없습니다".into());
    }
    app.get_webview_window("main")
        .ok_or("앱 창을 찾지 못했습니다")?
        .hide()
        .map_err(|_| "앱 창을 숨기지 못했습니다".into())
}

#[tauri::command]
pub(crate) fn desktop_exit(app: AppHandle) {
    app.exit(0);
}

pub(crate) fn setup_tray(app: &AppHandle) -> tauri::Result<()> {
    use tauri::{
        menu::{Menu, MenuItem},
        tray::{MouseButton, TrayIconBuilder, TrayIconEvent},
    };
    let open = MenuItem::with_id(app, "open", "Worklead 열기", true, None::<&str>)?;
    let hide = MenuItem::with_id(app, "hide", "트레이로 숨기기", true, None::<&str>)?;
    let exit = MenuItem::with_id(app, "exit", "완전히 종료", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&open, &hide, &exit])?;
    let mut builder = TrayIconBuilder::with_id("worklead-tray")
        .menu(&menu)
        .tooltip("Worklead")
        .show_menu_on_left_click(false);
    if let Some(icon) = app.default_window_icon() {
        builder = builder.icon(icon.clone());
    }
    builder
        .on_menu_event(|app, event| match event.id.as_ref() {
            "open" => show_window(app),
            "hide" => {
                let _ = desktop_hide(app.clone(), app.state::<Desktop>());
            }
            "exit" => desktop_exit(app.clone()),
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            if matches!(
                event,
                TrayIconEvent::DoubleClick {
                    button: MouseButton::Left,
                    ..
                }
            ) {
                show_window(tray.app_handle());
            }
        })
        .build(app)?;
    app.state::<Desktop>()
        .tray_available
        .store(true, Ordering::SeqCst);
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    fn temp_dir(name: &str) -> PathBuf {
        std::env::temp_dir().join(format!(
            "worklead-{name}-{}-{}",
            std::process::id(),
            chrono::Utc::now().timestamp_nanos_opt().unwrap()
        ))
    }
    #[test]
    fn settings_default_to_explicit_opt_in_and_no_invisible_close() {
        let dir = temp_dir("defaults");
        let desktop = Desktop::load(dir);
        assert!(!desktop.should_hide_on_close());
        assert!(!desktop.prefs.lock().unwrap().native_notifications);
        desktop.prefs.lock().unwrap().close_to_tray = true;
        assert!(!desktop.should_hide_on_close());
        desktop.tray_available.store(true, Ordering::SeqCst);
        assert!(desktop.should_hide_on_close());
    }
    #[test]
    fn atomic_replace_preserves_latest_settings_and_corruption_fails_closed() {
        let dir = temp_dir("replace");
        let path = dir.join("desktop-settings.json");
        write_json(&path, &Preferences::default()).unwrap();
        let next = Preferences {
            close_to_tray: true,
            native_notifications: true,
            notifications_enabled_at: Some("2026-09-30T05:00:00Z".into()),
        };
        write_json(&path, &next).unwrap();
        assert!(
            Desktop::load(dir.clone())
                .prefs
                .lock()
                .unwrap()
                .native_notifications
        );
        assert_eq!(fs::read_dir(&dir).unwrap().count(), 1);
        fs::write(&path, b"{broken").unwrap();
        let broken = Desktop::load(dir.clone());
        assert!(broken.load_error.is_some());
        assert!(!broken.prefs.lock().unwrap().native_notifications);
        fs::remove_dir_all(dir).unwrap();
    }
}
