//! One shell-owned consumer; hidden webviews and SSE reconnects never dispatch OS notifications.
use crate::{
    desktop::{read_json, write_json, Desktop},
    Backend, BackendState,
};
use serde::{Deserialize, Serialize};
use std::{collections::BTreeMap, path::Path, sync::atomic::Ordering, time::Duration};
use tauri::{AppHandle, Manager};

#[derive(Clone, Deserialize, Serialize, PartialEq, Debug)]
#[serde(rename_all = "snake_case")]
enum DeliveryState {
    Accepted,
    Failed,
    Unknown,
}

#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Delivery {
    claim_token: String,
    state: DeliveryState,
    synced: bool,
}

type Ledger = BTreeMap<String, Delivery>;

pub(crate) fn load_ledger(dir: &Path) -> Result<(), String> {
    read_ledger(dir).map(|_| ())
}

fn read_ledger(dir: &Path) -> Result<Ledger, String> {
    read_json(&dir.join("native-notifications.json"))
        .map(|value| value.unwrap_or_default())
        .map_err(|_| "알림 중복 방지 파일을 읽지 못했습니다. 데이터 폴더의 native-notifications.json을 확인하세요.".into())
}

fn save_ledger(dir: &Path, ledger: &Ledger) -> Result<(), String> {
    write_json(&dir.join("native-notifications.json"), ledger)
}

// Persist before the OS call. An interrupted attempt remains unknown and is never replayed.
fn reserve(dir: &Path, ledger: &mut Ledger, id: &str, token: &str) -> Result<bool, String> {
    if ledger.contains_key(id) {
        return Ok(false);
    }
    let entry = Delivery {
        claim_token: token.into(),
        state: DeliveryState::Unknown,
        synced: false,
    };
    ledger.insert(id.into(), entry);
    if let Err(error) = save_ledger(dir, ledger) {
        ledger.remove(id);
        return Err(error);
    }
    Ok(true)
}

#[derive(Deserialize)]
struct Item {
    id: String,
    kind: String,
}
#[derive(Deserialize)]
struct Pending {
    items: Vec<Item>,
}
#[derive(Deserialize)]
struct Claim {
    allowed: bool,
    claim_token: Option<String>,
}

pub(crate) fn start(app: AppHandle) {
    tauri::async_runtime::spawn(async move {
        let Ok(client) = reqwest::Client::builder()
            .no_proxy()
            .redirect(reqwest::redirect::Policy::none())
            .connect_timeout(Duration::from_secs(1))
            .timeout(Duration::from_secs(3))
            .build()
        else {
            log::error!("native notification client initialization failed");
            return;
        };
        loop {
            let backend = app.state::<std::sync::Arc<Backend>>();
            if backend.shutting_down.load(Ordering::SeqCst) {
                break;
            }
            let connection = match backend.state.borrow().clone() {
                BackendState::Ready(c) => Some(c),
                _ => None,
            };
            if let Some(connection) = connection {
                if let Err(error) = consume(&app, &client, &connection).await {
                    // Fixed messages only; never log the token, payload, or response body.
                    log::warn!("native notification consumer: {error}");
                }
            }
            tokio::time::sleep(Duration::from_secs(5)).await;
        }
    });
}

async fn sync_results(
    client: &reqwest::Client,
    conn: &crate::Connection,
    dir: &Path,
    ledger: &mut Ledger,
) -> Result<(), String> {
    let pending: Vec<(String, Delivery)> = ledger
        .iter()
        .filter(|(_, entry)| !entry.synced)
        .map(|(id, entry)| (id.clone(), entry.clone()))
        .collect();
    for (id, entry) in pending {
        let response = client
            .post(format!(
                "{}/v1/native-notifications/{id}/result",
                conn.base_url
            ))
            .bearer_auth(&conn.token)
            .json(&serde_json::json!({"state": entry.state, "claim_token": entry.claim_token}))
            .send()
            .await
            .map_err(|_| "result connection unavailable")?;
        if response.status().is_success() || matches!(response.status().as_u16(), 404 | 409) {
            // 404/409 after a restore never permits sending the old event again.
            ledger.get_mut(&id).expect("ledger entry").synced = true;
            save_ledger(dir, ledger)?;
        } else {
            return Err("result not acknowledged".into());
        }
    }
    Ok(())
}

async fn consume(
    app: &AppHandle,
    client: &reqwest::Client,
    conn: &crate::Connection,
) -> Result<(), String> {
    let desktop = app.state::<Desktop>();
    if desktop.load_error.is_some() {
        return Ok(());
    }
    let enabled_at = {
        let prefs = desktop.prefs.lock().expect("prefs lock");
        if !prefs.native_notifications {
            return Ok(());
        }
        prefs
            .notifications_enabled_at
            .clone()
            .ok_or("notification activation time missing")?
    };
    let mut ledger = read_ledger(&desktop.dir)?;
    sync_results(client, conn, &desktop.dir, &mut ledger).await?;
    let response = client
        .get(format!("{}/v1/native-notifications", conn.base_url))
        .bearer_auth(&conn.token)
        .query(&[("limit", "20"), ("created_after", enabled_at.as_str())])
        .send()
        .await
        .map_err(|_| "pending connection unavailable")?;
    if !response.status().is_success() {
        return Err("pending unavailable".into());
    }
    let pending: Pending = response
        .json()
        .await
        .map_err(|_| "pending format invalid")?;
    for item in pending.items {
        if app
            .state::<std::sync::Arc<Backend>>()
            .shutting_down
            .load(Ordering::SeqCst)
        {
            break;
        }
        let response = client
            .post(format!(
                "{}/v1/native-notifications/{}/claim",
                conn.base_url, item.id
            ))
            .bearer_auth(&conn.token)
            .send()
            .await
            .map_err(|_| "claim connection unavailable")?;
        if !response.status().is_success() {
            return Err("claim unavailable".into());
        }
        let claim: Claim = response.json().await.map_err(|_| "claim format invalid")?;
        if !claim.allowed {
            continue;
        }
        let token = claim.claim_token.ok_or("claim token missing")?;
        if !reserve(&desktop.dir, &mut ledger, &item.id, &token)? {
            // DB restored an already attempted ID. Resolve its new claim without another OS call.
            ledger.insert(
                item.id.clone(),
                Delivery {
                    claim_token: token,
                    state: DeliveryState::Unknown,
                    synced: false,
                },
            );
            save_ledger(&desktop.dir, &ledger)?;
        } else {
            let state = {
                // The opt-out command and the OS call share this lock: once opt-out completes,
                // no in-flight request may post a notification using the earlier setting.
                let prefs = desktop.prefs.lock().expect("prefs lock");
                if !prefs.native_notifications
                    || prefs.notifications_enabled_at.as_ref() != Some(&enabled_at)
                    || app
                        .state::<std::sync::Arc<Backend>>()
                        .shutting_down
                        .load(Ordering::SeqCst)
                {
                    DeliveryState::Failed
                } else {
                    post_to_windows(&app.config().identifier, &item.id, &item.kind)
                }
            };
            ledger.get_mut(&item.id).expect("reserved entry").state = state;
            save_ledger(&desktop.dir, &ledger)?;
        }
        sync_results(client, conn, &desktop.dir, &mut ledger).await?;
    }
    Ok(())
}

fn notification_body(kind: &str) -> Option<&'static str> {
    match kind {
        "new_lead" => Some("새 추천 리드가 있습니다. Worklead에서 확인하세요."),
        "new_review_lead" => Some("새 제작 의뢰에 확인할 사항이 있습니다. Worklead에서 지금 검토하세요."),
        "cafe_request" => Some("새 카페 문의가 있습니다. Worklead에서 확인하세요."),
        "daangn_message" => Some("당근알바 읽지 않은 메시지가 늘었습니다. Worklead의 전용 Edge에서 확인하세요."),
        "lead_changed" => {
            Some("관심·진행 중 리드의 조건이 변경되었습니다. Worklead에서 확인하세요.")
        }
        "source_issue" => Some("수집 소스에 확인할 문제가 있습니다. Worklead에서 확인하세요."),
        _ => None,
    }
}

fn notification_tag(id: &str) -> String {
    use sha2::{Digest, Sha256};
    let digest = Sha256::digest(id.as_bytes());
    digest[..8]
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

#[cfg(windows)]
fn post_to_windows(app_id: &str, id: &str, kind: &str) -> DeliveryState {
    use windows::{
        core::HSTRING,
        Data::Xml::Dom::XmlDocument,
        Win32::System::WinRT::{RoInitialize, RoUninitialize, RO_INIT_MULTITHREADED},
        UI::Notifications::{NotificationSetting, ToastNotification, ToastNotificationManager},
    };
    let Some(body) = notification_body(kind) else {
        return DeliveryState::Failed;
    };
    // A fresh thread guarantees a matching WinRT apartment and lets Show return a real HRESULT.
    let app_id = app_id.to_owned();
    let id = id.to_owned();
    std::thread::spawn(move || {
        if unsafe { RoInitialize(RO_INIT_MULTITHREADED) }.is_err() { return DeliveryState::Failed; }
        let result = (|| -> windows::core::Result<()> {
            let notifier = ToastNotificationManager::CreateToastNotifierWithId(&HSTRING::from(app_id))?;
            // A failed settings query is not evidence of an OS block. Show still enforces Windows policy.
            if matches!(notifier.Setting(), Ok(setting) if setting != NotificationSetting::Enabled) { return Err(windows::core::Error::from_hresult(windows::core::HRESULT(0x80070005u32 as i32))); }
            let xml = XmlDocument::new()?;
            // All text is fixed; no listing titles, contacts, or untrusted XML enter the OS payload.
            xml.LoadXml(&HSTRING::from(format!("<toast><visual><binding template=\"ToastGeneric\"><text>Worklead</text><text>{body}</text></binding></visual></toast>")))?;
            let notification = ToastNotification::CreateToastNotification(&xml)?;
            // Windows tag has a 16-character limit. The durable ledger owns full-ID dedupe.
            notification.SetTag(&HSTRING::from(notification_tag(&id)))?;
            notification.SetGroup(&HSTRING::from("Worklead"))?;
            notifier.Show(&notification)
        })();
        unsafe { RoUninitialize(); }
        match result {
            Ok(()) => DeliveryState::Accepted,
            Err(error) => {
                eprintln!("native notification OS rejected hresult={:08x}", error.code().0);
                DeliveryState::Failed
            }
        }
    }).join().unwrap_or(DeliveryState::Unknown)
}

#[cfg(not(windows))]
fn post_to_windows(_app_id: &str, _id: &str, _kind: &str) -> DeliveryState {
    DeliveryState::Failed
}

#[cfg(test)]
mod tests {
    use super::*;
    fn temp_dir(name: &str) -> std::path::PathBuf {
        std::env::temp_dir().join(format!(
            "worklead-{name}-{}-{}",
            std::process::id(),
            chrono::Utc::now().timestamp_nanos_opt().unwrap()
        ))
    }
    #[test]
    fn crash_and_restored_database_never_repeat_an_os_attempt() {
        let dir = temp_dir("notification-crash");
        let mut ledger = read_ledger(&dir).unwrap();
        assert!(reserve(&dir, &mut ledger, "evt_unique", "original-claim").unwrap());
        let mut resumed = read_ledger(&dir).unwrap();
        assert_eq!(resumed["evt_unique"].state, DeliveryState::Unknown);
        assert!(!reserve(&dir, &mut resumed, "evt_unique", "restored-claim").unwrap());
        assert!(reserve(&dir, &mut resumed, "evt_new", "new-claim").unwrap());
        std::fs::remove_dir_all(dir).unwrap();
    }
    #[test]
    fn corrupted_ledger_or_failed_persistence_blocks_dispatch() {
        let dir = temp_dir("notification-corrupt");
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join("native-notifications.json"), b"broken").unwrap();
        assert!(read_ledger(&dir).is_err());
        std::fs::remove_dir_all(&dir).unwrap();
        std::fs::write(&dir, b"a file, not a directory").unwrap();
        let mut ledger = Ledger::new();
        assert!(reserve(&dir, &mut ledger, "evt_unique", "claim").is_err());
        assert!(ledger.is_empty());
        std::fs::remove_file(dir).unwrap();
    }
    #[test]
    fn only_known_kinds_have_fixed_private_os_payloads() {
        assert!(notification_body("arbitrary title or contact").is_none());
        for kind in ["new_lead", "lead_changed", "source_issue", "cafe_request", "daangn_message"] {
            assert!(notification_body(kind).unwrap().contains("Worklead"));
        }
    }

    #[test]
    fn windows_tags_use_the_full_event_id_and_fit_the_os_limit() {
        let one = notification_tag("evt_identical_prefix_0001");
        let two = notification_tag("evt_identical_prefix_0002");
        assert_eq!(one.len(), 16);
        assert_ne!(one, two);
        assert_eq!(one, notification_tag("evt_identical_prefix_0001"));
    }

    #[cfg(windows)]
    #[test]
    #[ignore = "read-only Windows notification preparation diagnostic; never posts"]
    fn windows_notification_preparation_without_posting() {
        use windows::{
            core::HSTRING,
            Data::Xml::Dom::XmlDocument,
            Win32::System::WinRT::{RoInitialize, RoUninitialize, RO_INIT_MULTITHREADED},
            UI::Notifications::{ToastNotification, ToastNotificationManager},
        };
        std::thread::spawn(|| {
            unsafe { RoInitialize(RO_INIT_MULTITHREADED) }.expect("WinRT init");
            let notifier = ToastNotificationManager::CreateToastNotifierWithId(&HSTRING::from("app.worklead.desktop")).expect("Notifier");
            match notifier.Setting() {
                Ok(value) => eprintln!("notification_setting={}", value.0),
                Err(error) => eprintln!("notification_setting_query_error={:08x}; not a permission decision", error.code().0),
            }
            let xml = XmlDocument::new().expect("XML document");
            xml.LoadXml(&HSTRING::from("<toast><visual><binding template=\"ToastGeneric\"><text>Worklead</text><text>Test</text></binding></visual></toast>")).expect("XML parse");
            let notification = ToastNotification::CreateToastNotification(&xml).expect("Toast construction");
            notification.SetTag(&HSTRING::from("0123456789abcdef")).expect("Tag");
            notification.SetGroup(&HSTRING::from("Worklead")).expect("Group");
            eprintln!("notification_preparation=ok; no Show call");
            drop(notification);
            drop(xml);
            drop(notifier);
            unsafe { RoUninitialize(); }
        }).join().unwrap();
    }
}
