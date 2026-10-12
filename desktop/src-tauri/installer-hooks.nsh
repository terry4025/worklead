; The pinned template includes the helper after language/product declarations.
!define WORKLEAD_UNINSTALL_INCLUDE "${__FILEDIR__}\uninstall-safety.nsh"

; Installation/update must not force-kill an old GUI before its uninstaller can
; protect the database. Used in .onInit and again immediately before uninstall.
!macro WORKLEAD_ENSURE_EXITED
  Push $0
  nsis_tauri_utils::FindProcessCurrentUser "${MAINBINARYNAME}.exe"
  Pop $0
  ${If} $0 != 1
    StrCpy $WorkleadPurgeStatus 17
  ${EndIf}
  nsis_tauri_utils::FindProcessCurrentUser "${WORKLEAD_BACKEND_NAME}"
  Pop $0
  ${If} $0 != 1
    StrCpy $WorkleadPurgeStatus 17
  ${EndIf}
  Pop $0
  ${If} $WorkleadPurgeStatus = 17
    ${IfNot} ${Silent}
    ${AndIf} $PassiveMode != 1
      MessageBox MB_OK|MB_ICONEXCLAMATION "$(WorkleadCloseFirst)"
    ${EndIf}
    SetErrorLevel 17
    Abort
  ${EndIf}
!macroend

; Remove only this installation's exact command. Updates preserve the user's opt-in.
!macro WORKLEAD_REMOVE_AUTOSTART value_name expected_command
  Push $0
  ReadRegStr $0 HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "${value_name}"
  StrCmpS $0 '${expected_command}' 0 +3
    DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "${value_name}"
    DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run" "${value_name}"
  Pop $0
!macroend

!macro WORKLEAD_AFTER_UNINSTALL value_name expected_command
  ${If} $UpdateMode != 1
    !insertmacro WORKLEAD_REMOVE_AUTOSTART "${value_name}" '${expected_command}'
  ${EndIf}
!macroend

!macro NSIS_HOOK_POSTUNINSTALL
  !insertmacro WORKLEAD_AFTER_UNINSTALL "Worklead" '$\"$INSTDIR\${MAINBINARYNAME}.exe$\" --autostart'
  Call un.WorkleadFinishData
  ${If} $WorkleadPurgeStatus != 0
    IfSilent +2
      MessageBox MB_OK|MB_ICONEXCLAMATION "$(WorkleadPartialData)"
    SetErrorLevel $WorkleadPurgeStatus
    Abort
  ${EndIf}
  ${If} $DeleteAppDataCheckboxState = 1
  ${AndIf} $UpdateMode != 1
    ; Remove only this installation's known installer preferences, preserving
    ; foreign values and a newer installation's stored location.
    ReadRegStr $0 SHCTX "${MANUPRODUCTKEY}" ""
    ${If} $0 == $INSTDIR
      DeleteRegValue SHCTX "${MANUPRODUCTKEY}" ""
      DeleteRegValue SHCTX "${MANUPRODUCTKEY}" "Installer Language"
      DeleteRegKey /ifempty SHCTX "${MANUPRODUCTKEY}"
      DeleteRegKey /ifempty SHCTX "${MANUKEY}"
    ${EndIf}
  ${EndIf}
  ; A legacy install root can also contain retained data. Never recurse here.
  RMDir "$INSTDIR"
  ; Remaining retained data is expected. Checked file/data failures already abort
  ; above; this optional empty-folder cleanup must not stall update auto-close.
  ClearErrors
!macroend

!macro NSIS_HOOK_PREUNINSTALL
  Call un.WorkleadPrepareUninstall
  ${If} $DeleteAppDataCheckboxState = 1
  ${AndIf} $UpdateMode != 1
    ; The confirmed data deletion also covers this fixed current-user scope's
    ; OS key. Retain/update paths never invoke the credential helper.
    Push $0
    Push $1
    nsExec::ExecToStack '$\"$INSTDIR\${WORKLEAD_BACKEND_NAME}$\" --remove-ai-credential --confirm-key-delete --data-dir $\"$LOCALAPPDATA\Worklead$\"'
    Pop $0
    Pop $1
    ${If} $0 != 0
      ${IfNot} ${Silent}
        MessageBox MB_OK|MB_ICONEXCLAMATION "$(WorkleadKeyDeleteFailed)"
      ${EndIf}
      SetErrorLevel 18
      Pop $1
      Pop $0
      Abort
    ${EndIf}
    Pop $1
    Pop $0
  ${EndIf}
!macroend
