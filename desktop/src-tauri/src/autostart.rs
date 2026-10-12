//! User-selected Windows startup registration. Reading never creates keys.
use std::path::Path;

const VALUE_NAME: &str = "Worklead";
const PATH_ERROR: &str = "자동 시작에 사용할 실행 경로를 확인하지 못했습니다.";
const READ_ERROR: &str = "Windows 자동 시작 설정을 읽지 못했습니다.";
const WRITE_ERROR: &str = "Windows 자동 시작 설정을 변경하지 못했습니다.";
const OWNERSHIP_ERROR: &str =
    "자동 시작 설정이 현재 앱의 실행 경로·형식과 다릅니다. 기존 값을 변경하지 않았습니다.";
const APPROVAL_ERROR: &str =
    "Windows 시작프로그램의 사용 상태를 확인하지 못했습니다. 기존 값을 변경하지 않았습니다.";
const CHANGED_ERROR: &str = "자동 시작 설정이 변경되었습니다. 다시 불러오세요.";
const RECOVERY_ERROR: &str =
    "자동 시작 설정 복원에 실패했습니다. Windows 시작프로그램 설정을 확인하세요.";

fn expected_command(executable: &Path) -> Result<String, String> {
    let path = executable.to_str().ok_or(PATH_ERROR)?;
    if path.is_empty() || path.chars().any(|c| c == '"' || c.is_control()) {
        return Err(PATH_ERROR.into());
    }
    let command = format!("\"{path}\" --autostart");
    // Run command lines are limited to 260 characters by Windows.
    if command.encode_utf16().count() > 260 {
        return Err(PATH_ERROR.into());
    }
    Ok(command)
}

#[cfg(windows)]
static LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

pub(crate) fn is_enabled() -> Result<bool, String> {
    #[cfg(windows)]
    {
        let _guard = LOCK.lock().map_err(|_| READ_ERROR)?;
        let executable = std::env::current_exe().map_err(|_| PATH_ERROR)?;
        windows::is_enabled_for(VALUE_NAME, &executable)
    }
    #[cfg(not(windows))]
    Err("자동 시작은 Windows에서만 지원합니다.".into())
}

pub(crate) fn set_enabled(value: bool) -> Result<(), String> {
    #[cfg(windows)]
    {
        let _guard = LOCK.lock().map_err(|_| WRITE_ERROR)?;
        let executable = std::env::current_exe().map_err(|_| PATH_ERROR)?;
        windows::set_enabled_for(VALUE_NAME, &executable, value)
    }
    #[cfg(not(windows))]
    {
        let _ = value;
        Err("자동 시작은 Windows에서만 지원합니다.".into())
    }
}

#[cfg(windows)]
mod windows {
    use super::*;
    use std::io::ErrorKind;
    use winreg::{enums::*, RegKey, RegValue};

    const RUN_KEY: &str = r"Software\Microsoft\Windows\CurrentVersion\Run";
    const APPROVED_KEY: &str =
        r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run";

    #[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
    enum Slot {
        Run,
        Approved,
    }

    impl Slot {
        fn path(self) -> &'static str {
            match self {
                Self::Run => RUN_KEY,
                Self::Approved => APPROVED_KEY,
            }
        }
    }

    trait Store {
        fn read(&self, slot: Slot, name: &str) -> Result<Option<RegValue>, String>;
        fn write(&self, slot: Slot, name: &str, value: &RegValue) -> Result<(), String>;
        fn remove(&self, slot: Slot, name: &str) -> Result<(), String>;
    }

    struct Registry;

    pub(super) fn is_enabled_for(name: &str, executable: &Path) -> Result<bool, String> {
        enabled(&Registry, name, executable)
    }

    pub(super) fn set_enabled_for(
        name: &str,
        executable: &Path,
        enable: bool,
    ) -> Result<(), String> {
        change(&Registry, name, executable, enable)
    }

    impl Store for Registry {
        fn read(&self, slot: Slot, name: &str) -> Result<Option<RegValue>, String> {
            let key = match RegKey::predef(HKEY_CURRENT_USER)
                .open_subkey_with_flags(slot.path(), KEY_QUERY_VALUE)
            {
                Ok(key) => key,
                Err(error) if error.kind() == ErrorKind::NotFound => return Ok(None),
                Err(_) => return Err(READ_ERROR.into()),
            };
            match key.get_raw_value(name) {
                Ok(value) => Ok(Some(value)),
                Err(error) if error.kind() == ErrorKind::NotFound => Ok(None),
                Err(_) => Err(READ_ERROR.into()),
            }
        }

        fn write(&self, slot: Slot, name: &str, value: &RegValue) -> Result<(), String> {
            let (key, _) = RegKey::predef(HKEY_CURRENT_USER)
                .create_subkey_with_flags(slot.path(), KEY_SET_VALUE)
                .map_err(|_| WRITE_ERROR)?;
            key.set_raw_value(name, value)
                .map_err(|_| WRITE_ERROR.into())
        }

        fn remove(&self, slot: Slot, name: &str) -> Result<(), String> {
            let key = match RegKey::predef(HKEY_CURRENT_USER)
                .open_subkey_with_flags(slot.path(), KEY_SET_VALUE)
            {
                Ok(key) => key,
                Err(error) if error.kind() == ErrorKind::NotFound => return Ok(()),
                Err(_) => return Err(WRITE_ERROR.into()),
            };
            match key.delete_value(name) {
                Ok(()) => Ok(()),
                Err(error) if error.kind() == ErrorKind::NotFound => Ok(()),
                Err(_) => Err(WRITE_ERROR.into()),
            }
        }
    }

    fn run_value(command: &str) -> RegValue {
        RegValue {
            vtype: REG_SZ,
            bytes: command
                .encode_utf16()
                .chain(Some(0))
                .flat_map(u16::to_le_bytes)
                .collect(),
        }
    }

    fn approved_value(state: u32) -> RegValue {
        let mut bytes = vec![0; 12];
        bytes[..4].copy_from_slice(&state.to_le_bytes());
        RegValue {
            vtype: REG_BINARY,
            bytes,
        }
    }

    fn validate_run(value: &RegValue, expected: &RegValue) -> Result<(), String> {
        // Exact canonical bytes also reject expandable/multiple strings,
        // missing terminators, unquoted paths, extra arguments and bad UTF-16.
        if value == expected {
            Ok(())
        } else {
            Err(OWNERSHIP_ERROR.into())
        }
    }

    fn approval_enabled(value: Option<&RegValue>) -> Result<bool, String> {
        let Some(value) = value else { return Ok(true) };
        if value.vtype != REG_BINARY || value.bytes.len() < 4 {
            return Err(APPROVAL_ERROR.into());
        }
        match u32::from_le_bytes(value.bytes[..4].try_into().expect("checked length")) {
            2 | 6 => Ok(true),
            3 | 7 => Ok(false),
            _ => Err(APPROVAL_ERROR.into()),
        }
    }

    fn enabled(store: &impl Store, name: &str, executable: &Path) -> Result<bool, String> {
        let expected = run_value(&expected_command(executable)?);
        let Some(value) = store.read(Slot::Run, name)? else {
            return Ok(false);
        };
        validate_run(&value, &expected)?;
        approval_enabled(store.read(Slot::Approved, name)?.as_ref())
    }

    fn restore(
        store: &impl Store,
        name: &str,
        slot: Slot,
        original: Option<&RegValue>,
    ) -> Result<(), String> {
        // Avoid a needless write if a failing registry operation changed nothing.
        let current = store.read(slot, name)?;
        if current.as_ref() == original {
            return Ok(());
        }
        match original {
            Some(value) => store.write(slot, name, value),
            None => store.remove(slot, name),
        }
    }

    fn change(
        store: &impl Store,
        name: &str,
        executable: &Path,
        enable: bool,
    ) -> Result<(), String> {
        let expected = run_value(&expected_command(executable)?);
        let old_run = store.read(Slot::Run, name)?;
        if let Some(value) = &old_run {
            validate_run(value, &expected)?;
        }
        if !enable {
            // Disabling does not edit Windows' approval record or another app's value.
            if old_run.is_none() {
                return Ok(());
            }
            if store.read(Slot::Run, name)? != old_run {
                return Err(CHANGED_ERROR.into());
            }
            return store.remove(Slot::Run, name);
        }
        let old_approval = store.read(Slot::Approved, name)?;
        approval_enabled(old_approval.as_ref())?;
        if store.read(Slot::Run, name)? != old_run
            || store.read(Slot::Approved, name)? != old_approval
        {
            return Err(CHANGED_ERROR.into());
        }
        let result = store
            .write(Slot::Run, name, &expected)
            .and_then(|_| store.write(Slot::Approved, name, &approved_value(2)));
        if result.is_err() {
            // Attempt both restorations even when one fails.
            let run_restored = restore(store, name, Slot::Run, old_run.as_ref());
            let approval_restored = restore(store, name, Slot::Approved, old_approval.as_ref());
            if run_restored.is_err() || approval_restored.is_err() {
                return Err(RECOVERY_ERROR.into());
            }
        }
        result
    }

    #[cfg(test)]
    mod tests {
        use super::*;
        use std::{
            cell::{Cell, RefCell},
            collections::HashMap,
        };

        fn copy(value: &RegValue) -> RegValue {
            RegValue {
                vtype: value.vtype.clone(),
                bytes: value.bytes.clone(),
            }
        }

        #[derive(Default)]
        struct Memory {
            values: RefCell<HashMap<Slot, RegValue>>,
            writes: Cell<usize>,
            fail_writes: Vec<usize>,
            fail_after_writes: Vec<usize>,
            fail_reads: bool,
        }

        impl Memory {
            fn mutate(&self) -> Result<(), String> {
                let step = self.writes.get() + 1;
                self.writes.set(step);
                if self.fail_writes.contains(&step) {
                    Err(WRITE_ERROR.into())
                } else {
                    Ok(())
                }
            }
        }

        impl Store for Memory {
            fn read(&self, slot: Slot, _name: &str) -> Result<Option<RegValue>, String> {
                if self.fail_reads {
                    return Err(READ_ERROR.into());
                }
                Ok(self.values.borrow().get(&slot).map(copy))
            }
            fn write(&self, slot: Slot, _name: &str, value: &RegValue) -> Result<(), String> {
                self.mutate()?;
                self.values.borrow_mut().insert(slot, copy(value));
                if self.fail_after_writes.contains(&self.writes.get()) {
                    Err(WRITE_ERROR.into())
                } else {
                    Ok(())
                }
            }
            fn remove(&self, slot: Slot, _name: &str) -> Result<(), String> {
                self.mutate()?;
                self.values.borrow_mut().remove(&slot);
                Ok(())
            }
        }

        fn executable() -> &'static Path {
            Path::new(r"C:\검증 폴더\Worklead.exe")
        }
        fn expected() -> RegValue {
            run_value(&expected_command(executable()).unwrap())
        }

        #[test]
        fn missing_registration_is_false_without_writes() {
            let store = Memory::default();
            assert!(!enabled(&store, VALUE_NAME, executable()).unwrap());
            assert_eq!(store.writes.get(), 0);
            change(&store, VALUE_NAME, executable(), false).unwrap();
            assert_eq!(store.writes.get(), 0);
        }

        #[test]
        fn read_errors_are_not_disabled() {
            let store = Memory {
                fail_reads: true,
                ..Memory::default()
            };
            assert_eq!(
                enabled(&store, VALUE_NAME, executable()).unwrap_err(),
                READ_ERROR
            );
            assert_eq!(
                change(&store, VALUE_NAME, executable(), true).unwrap_err(),
                READ_ERROR
            );
            assert_eq!(store.writes.get(), 0);
        }

        #[test]
        fn foreign_or_malformed_registration_is_never_overwritten_or_deleted() {
            let mut malformed = expected();
            malformed.bytes.pop();
            for value in [
                run_value(r"C:\검증 폴더\Worklead.exe --autostart"),
                run_value(r#""C:\다른 앱\Worklead.exe" --autostart"#),
                run_value(r#""C:\검증 폴더\Worklead.exe" --autostart --extra"#),
                RegValue {
                    vtype: REG_EXPAND_SZ,
                    bytes: expected().bytes,
                },
                malformed,
            ] {
                let store = Memory::default();
                store.values.borrow_mut().insert(Slot::Run, copy(&value));
                assert_eq!(
                    enabled(&store, VALUE_NAME, executable()).unwrap_err(),
                    OWNERSHIP_ERROR
                );
                for enable in [true, false] {
                    assert_eq!(
                        change(&store, VALUE_NAME, executable(), enable).unwrap_err(),
                        OWNERSHIP_ERROR
                    );
                }
                assert!(store.read(Slot::Run, VALUE_NAME).unwrap().as_ref() == Some(&value));
                assert_eq!(store.writes.get(), 0);
            }
        }

        #[test]
        fn approval_states_use_the_first_little_endian_u32() {
            assert!(approval_enabled(None).unwrap());
            for state in [2, 6] {
                assert!(approval_enabled(Some(&approved_value(state))).unwrap());
            }
            for state in [3, 7] {
                assert!(!approval_enabled(Some(&approved_value(state))).unwrap());
            }
            for state in [0, 1, 4, 5, 8, 258, u32::MAX] {
                assert_eq!(
                    approval_enabled(Some(&approved_value(state))).unwrap_err(),
                    APPROVAL_ERROR
                );
            }
        }

        #[test]
        fn invalid_approval_is_not_overwritten() {
            for value in [
                RegValue {
                    vtype: REG_SZ,
                    bytes: vec![2, 0, 0, 0],
                },
                RegValue {
                    vtype: REG_BINARY,
                    bytes: vec![2, 0, 0],
                },
                approved_value(9),
            ] {
                let store = Memory::default();
                store.values.borrow_mut().insert(Slot::Run, expected());
                store
                    .values
                    .borrow_mut()
                    .insert(Slot::Approved, copy(&value));
                assert_eq!(
                    enabled(&store, VALUE_NAME, executable()).unwrap_err(),
                    APPROVAL_ERROR
                );
                assert_eq!(
                    change(&store, VALUE_NAME, executable(), true).unwrap_err(),
                    APPROVAL_ERROR
                );
                assert_eq!(store.writes.get(), 0);
                assert!(store.read(Slot::Approved, VALUE_NAME).unwrap().as_ref() == Some(&value));
            }
        }

        #[test]
        fn explicit_enable_sets_approval_two_and_disable_preserves_approval() {
            let store = Memory::default();
            store
                .values
                .borrow_mut()
                .insert(Slot::Approved, approved_value(7));
            change(&store, VALUE_NAME, executable(), true).unwrap();
            assert!(enabled(&store, VALUE_NAME, executable()).unwrap());
            assert!(
                store.read(Slot::Approved, VALUE_NAME).unwrap().as_ref()
                    == Some(&approved_value(2))
            );
            change(&store, VALUE_NAME, executable(), false).unwrap();
            assert!(!enabled(&store, VALUE_NAME, executable()).unwrap());
            assert!(
                store.read(Slot::Approved, VALUE_NAME).unwrap().as_ref()
                    == Some(&approved_value(2))
            );
            change(&store, VALUE_NAME, executable(), false).unwrap();
        }

        #[test]
        fn partial_enable_failure_restores_absent_run_and_original_approval_bytes() {
            let store = Memory {
                fail_writes: vec![2],
                ..Memory::default()
            };
            let mut approval = approved_value(3);
            approval.bytes[4..].copy_from_slice(&[9, 8, 7, 6, 5, 4, 3, 2]);
            store
                .values
                .borrow_mut()
                .insert(Slot::Approved, copy(&approval));
            assert_eq!(
                change(&store, VALUE_NAME, executable(), true).unwrap_err(),
                WRITE_ERROR
            );
            assert!(store.read(Slot::Run, VALUE_NAME).unwrap().is_none());
            assert!(store.read(Slot::Approved, VALUE_NAME).unwrap().as_ref() == Some(&approval));
        }

        #[test]
        fn reported_failure_after_both_writes_restores_both_original_values() {
            let store = Memory {
                fail_after_writes: vec![2],
                ..Memory::default()
            };
            let mut approval = approved_value(7);
            approval.bytes[4..].copy_from_slice(&[1, 2, 3, 4, 5, 6, 7, 8]);
            store
                .values
                .borrow_mut()
                .insert(Slot::Approved, copy(&approval));
            assert_eq!(
                change(&store, VALUE_NAME, executable(), true).unwrap_err(),
                WRITE_ERROR
            );
            assert!(store.read(Slot::Run, VALUE_NAME).unwrap().is_none());
            assert!(store.read(Slot::Approved, VALUE_NAME).unwrap().as_ref() == Some(&approval));
            assert_eq!(store.writes.get(), 4);
        }

        #[test]
        fn rollback_failure_is_reported() {
            let store = Memory {
                fail_writes: vec![2, 3],
                ..Memory::default()
            };
            assert_eq!(
                change(&store, VALUE_NAME, executable(), true).unwrap_err(),
                RECOVERY_ERROR
            );
        }

        fn valid_uuid(value: &str) -> bool {
            value.len() == 36
                && value.bytes().enumerate().all(|(i, byte)| {
                    if [8, 13, 18, 23].contains(&i) {
                        byte == b'-'
                    } else {
                        byte.is_ascii_hexdigit()
                    }
                })
        }

        struct TestCleanup {
            name: String,
        }
        impl TestCleanup {
            fn cleanup(&self) -> Result<(), String> {
                let run = Registry.remove(Slot::Run, &self.name);
                let approval = Registry.remove(Slot::Approved, &self.name);
                run.and(approval)
            }
        }
        impl Drop for TestCleanup {
            fn drop(&mut self) {
                let _ = self.cleanup();
            }
        }

        #[test]
        #[ignore = "opt-in: only a task UUID registry value; never launches an executable"]
        fn registry_round_trip_task_value_preserves_worklead() {
            let uuid = std::env::var("WORKLEAD_AUTOSTART_TEST_UUID")
                .expect("명시적인 테스트 UUID가 필요합니다.");
            assert!(valid_uuid(&uuid), "테스트 UUID 형식이 올바르지 않습니다.");
            let name = format!("Worklead-test-{uuid}");
            let executable = std::env::temp_dir()
                .join(format!("Worklead 검증 {uuid}"))
                .join("실행하지 않는 테스트.exe");
            let production_run = Registry.read(Slot::Run, VALUE_NAME).unwrap();
            let production_approval = Registry.read(Slot::Approved, VALUE_NAME).unwrap();
            // Refuse even an unlikely UUID collision before any mutation.
            assert!(Registry.read(Slot::Run, &name).unwrap().is_none());
            assert!(Registry.read(Slot::Approved, &name).unwrap().is_none());
            let cleanup = TestCleanup { name: name.clone() };
            assert!(!enabled(&Registry, &name, &executable).unwrap());
            change(&Registry, &name, &executable, true).unwrap();
            assert!(
                Registry.read(Slot::Run, &name).unwrap().as_ref()
                    == Some(&run_value(&expected_command(&executable).unwrap()))
            );
            assert!(
                Registry.read(Slot::Approved, &name).unwrap().as_ref() == Some(&approved_value(2))
            );
            assert!(enabled(&Registry, &name, &executable).unwrap());
            Registry
                .write(Slot::Approved, &name, &approved_value(3))
                .unwrap();
            assert!(!enabled(&Registry, &name, &executable).unwrap());
            change(&Registry, &name, &executable, true).unwrap();
            assert!(enabled(&Registry, &name, &executable).unwrap());
            change(&Registry, &name, &executable, false).unwrap();
            change(&Registry, &name, &executable, false).unwrap();
            assert!(!enabled(&Registry, &name, &executable).unwrap());
            cleanup.cleanup().unwrap();
            assert!(Registry.read(Slot::Run, &name).unwrap().is_none());
            assert!(Registry.read(Slot::Approved, &name).unwrap().is_none());
            assert!(Registry.read(Slot::Run, VALUE_NAME).unwrap() == production_run);
            assert!(Registry.read(Slot::Approved, VALUE_NAME).unwrap() == production_approval);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn quote_korean_and_space_executable_path_with_only_autostart_argument() {
        assert_eq!(
            expected_command(Path::new(r"C:\업무 폴더\Worklead.exe")).unwrap(),
            r#""C:\업무 폴더\Worklead.exe" --autostart"#
        );
    }

    #[test]
    fn invalid_or_too_long_command_is_rejected() {
        for path in [
            "",
            "C:\\bad\"name.exe",
            "C:\\bad\0name.exe",
            "C:\\bad\nname.exe",
        ] {
            assert_eq!(expected_command(Path::new(path)).unwrap_err(), PATH_ERROR);
        }
        assert_eq!(
            expected_command(Path::new(&"a".repeat(260))).unwrap_err(),
            PATH_ERROR
        );
    }
}
