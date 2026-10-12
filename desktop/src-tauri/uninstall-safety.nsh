; Worklead's current-user uninstaller. No pathname-recursive deletion is used.
; All entries are checked before deletion; handles pin their identity and directories
; deny rename/delete sharing until cleanup. A later I/O failure is reported as partial.
!ifndef WORKLEAD_UNINSTALL_SAFETY
!define WORKLEAD_UNINSTALL_SAFETY
!ifndef WORKLEAD_BACKEND_NAME
!define WORKLEAD_BACKEND_NAME "worklead-backend.exe"
!endif

Var WorkleadPurgeStatus
Var WorkleadPurgeNodes
Var WorkleadPinNodes
Var WorkleadInstallHandle
Var WorkleadLastPin

LangString WorkleadDeleteLabel 1042 "데이터도 삭제 (되돌릴 수 없음)"
LangString WorkleadDeleteLabel 1033 "Delete saved data (cannot undo)"
LangString WorkleadDeleteConfirm 1042 "이 PC의 Worklead 데이터도 삭제하시겠습니까?$\r$\n$\r$\n리드, 메모, 영업 기록, 설정, 오늘 요청 사용량, AI 비용 기록, 로그, 내보내기 파일, 백업과 데스크톱 설정을 삭제합니다. 아래 Worklead 폴더에 연결된 Windows 저장소의 AI 키도 삭제합니다.$\r$\n대상: 현재 사용자의 LocalAppData\\Worklead 및 LocalAppData/AppData\\app.worklead.desktop 폴더.$\r$\n별도 WORKLEAD_DATA_DIR와 그 키, 다른 위치에 복사한 파일은 삭제하지 않습니다. 저장장치의 복구 불가능한 소거는 보장하지 않습니다.$\r$\n$\r$\n유지하려면 '아니요'를 누르고 삭제 선택을 해제하세요."
LangString WorkleadDeleteConfirm 1033 "Also delete Worklead data on this PC?$\r$\n$\r$\nThis deletes leads, notes, sales history, settings, today's request usage, AI cost records, logs, exports, backups and desktop preferences. It also deletes the Windows AI key scoped to the Worklead folder below.$\r$\nTargets: the current user's LocalAppData\\Worklead and LocalAppData/AppData\\app.worklead.desktop folders.$\r$\nCustom WORKLEAD_DATA_DIR locations and their keys, and copies elsewhere are retained. Secure erasure of storage is not guaranteed.$\r$\n$\r$\nChoose No and uncheck data deletion to keep it."
LangString WorkleadKeyDeleteFailed 1042 "Windows 저장소의 AI 키 삭제를 확인하지 못해 제거를 중단했습니다. 실행 파일과 데이터 파일은 유지됩니다. 앱 설정에서 키 상태를 확인하세요."
LangString WorkleadKeyDeleteFailed 1033 "Uninstall stopped because Windows AI key deletion was not confirmed. Program and data files are retained. Check the key status in app settings."
LangString WorkleadCloseFirst 1042 "Worklead 또는 수집 서비스가 실행 중입니다. 트레이 메뉴에서 '완전히 종료'한 뒤 설치 또는 제거를 다시 실행하세요. 데이터와 실행 파일은 변경하지 않았습니다."
LangString WorkleadCloseFirst 1033 "Worklead or its collection service is running. Fully exit from the tray, then run install or uninstall again. Data and program files were not changed."
LangString WorkleadUnsafeData 1042 "안전한 경로 또는 파일 잠금을 확인하지 못해 제거를 중단했습니다 (코드 $WorkleadPurgeStatus). 연결 폴더, 읽기 전용 파일과 실행 중인 프로그램을 확인하세요. 데이터와 실행 파일은 변경하지 않았습니다."
LangString WorkleadUnsafeData 1033 "Uninstall stopped because a safe path or file lock could not be verified (code $WorkleadPurgeStatus). Check linked folders, read-only files and running programs. Data and program files were not changed."
LangString WorkleadPartialData 1042 "데이터 삭제 중 오류가 발생했습니다 (코드 $WorkleadPurgeStatus). 일부 파일이 남아 있을 수 있습니다. 제거와 데이터 삭제가 완전히 끝난 것으로 간주하지 마세요."
LangString WorkleadPartialData 1033 "Data deletion encountered an error (code $WorkleadPurgeStatus). Some files may remain. Uninstall and data deletion must not be treated as complete."

; Put an already opened handle on the pin list (no deletion).
Function un.WorkleadRememberPin
  Exch $0
  Push $1
  System::Call '*(p r0, p $WorkleadPinNodes) p .r1'
  ${If} $1 = 0
    StrCpy $WorkleadPurgeStatus 18
    System::Call 'kernel32::CloseHandle(p r0)'
  ${Else}
  StrCpy $WorkleadPinNodes $1
  ${EndIf}
  Pop $1
  Pop $0
FunctionEnd

; Verify a plain absolute DOS path and pin every ancestor. No UNC, extended path,
; path aliases, reparse points, or shortened/redirected path spellings are accepted.
Function un.WorkleadPinPath
  Exch $0
  Push $1
  Push $2
  Push $3
  Push $4
  Push $5
  Push $6
  Push $7
  Push $8
  Push $9
  StrLen $1 $0
  ${If} $1 < 4
    StrCpy $WorkleadPurgeStatus 10
    Goto pin_done
  ${EndIf}
  StrCpy $2 $0 2 1
  ${If} $2 != ":\"
    StrCpy $WorkleadPurgeStatus 10
    Goto pin_done
  ${EndIf}
  GetFullPathName $2 $0
  ${If} $2 != $0
    StrCpy $WorkleadPurgeStatus 10
    Goto pin_done
  ${EndIf}
  StrCpy $2 $0 3
  StrCpy $3 3
  pin_next:
    ; Walk from the drive root to the final directory. Pin each before opening
    ; the next component so a junction/rename cannot swap an ancestor.
    System::Call 'kernel32::CreateFileW(w r2, i 0x80, i 3, p 0, i 3, i 0x02200000, p 0) p .r4'
    ${If} $4 = -1
      StrCpy $WorkleadPurgeStatus 12
      Goto pin_done
    ${EndIf}
    System::Alloc 52
    Pop $5
    ${If} $5 = 0
      StrCpy $WorkleadPurgeStatus 18
      System::Call 'kernel32::CloseHandle(p r4)'
      Goto pin_done
    ${EndIf}
    System::Call 'kernel32::GetFileInformationByHandle(p r4, p r5) i .r6'
    System::Call '*$5(i .r7)'
    System::Free $5
    IntOp $8 $7 & 0x410
    ${If} $6 = 0
    ${OrIf} $8 != 16
      StrCpy $WorkleadPurgeStatus 11
      System::Call 'kernel32::CloseHandle(p r4)'
      Goto pin_done
    ${EndIf}
    System::Call 'kernel32::GetFinalPathNameByHandleW(p r4, w .r5, i ${NSIS_MAX_STRLEN}, i 0) i .r6'
    StrCpy $7 "\\?\$2"
    ${If} $6 = 0
    ${OrIf} $6 >= ${NSIS_MAX_STRLEN}
    ${OrIf} $5 != $7
      StrCpy $WorkleadPurgeStatus 15
      System::Call 'kernel32::CloseHandle(p r4)'
      Goto pin_done
    ${EndIf}
    Push $4
    Call un.WorkleadRememberPin
    ${If} $WorkleadPurgeStatus != 0
      Goto pin_done
    ${EndIf}
    ${If} $3 >= $1
      StrCpy $WorkleadLastPin $4
      Goto pin_done
    ${EndIf}
    ; Find the next separator; the drive root already ends in one.
    IntOp $3 $3 + 1
    pin_component:
      ${If} $3 >= $1
        StrCpy $2 $0
        Goto pin_next
      ${EndIf}
      StrCpy $9 $0 1 $3
      ${If} $9 == "\"
        StrCpy $2 $0 $3
        Goto pin_next
      ${EndIf}
      IntOp $3 $3 + 1
      Goto pin_component
  pin_done:
  Pop $9
  Pop $8
  Pop $7
  Pop $6
  Pop $5
  Pop $4
  Pop $3
  Pop $2
  Pop $1
  Pop $0
FunctionEnd

; Open one owned data tree. Parent directories must already be pinned.
; The prepend list gives postorder deletion: descendants precede parents.
Function un.WorkleadPreflightNode
  Exch $0
  Push $1
  Push $2
  Push $3
  Push $4
  Push $5
  Push $6
  Push $7
  Push $8
  Push $9
  ${If} $WorkleadPurgeStatus != 0
    Goto node_done
  ${EndIf}
  StrLen $1 $0
  ${If} $1 >= 1000
    StrCpy $WorkleadPurgeStatus 10
    Goto node_done
  ${EndIf}
  ; DELETE + read attributes, no reparse traversal; files allow the template's
  ; ordinary Delete to mark an executable, directories deny rename/delete sharing.
  System::Call 'kernel32::GetFileAttributesW(w r0) i .r2 ?e'
  Pop $3
  ${If} $2 = -1
    ${If} $3 = 2
    ${OrIf} $3 = 3
      Goto node_done
    ${EndIf}
    StrCpy $WorkleadPurgeStatus 12
    Goto node_done
  ${EndIf}
  IntOp $3 $2 & 0x400
  ${If} $3 != 0
    StrCpy $WorkleadPurgeStatus 11
    Goto node_done
  ${EndIf}
  IntOp $3 $2 & 0x10
  StrCpy $7 7
  ${If} $3 != 0
    StrCpy $7 3
  ${Else}
    IntOp $8 $2 & 1
    ${If} $8 != 0
      StrCpy $WorkleadPurgeStatus 12
      Goto node_done
    ${EndIf}
  ${EndIf}
  StrCpy $8 0x10080
  ; Opening a mapped program for write is refused by Windows. Hold these checked
  ; handles until uninstall finishes so a fresh GUI/backend launch is also blocked.
  ${If} $0 == "$INSTDIR\${MAINBINARYNAME}.exe"
  ${OrIf} $0 == "$INSTDIR\${WORKLEAD_BACKEND_NAME}"
    StrCpy $8 0xC0010080
  ${EndIf}
  ${GetFileName} $0 $9
  ${If} $9 == "backend.lock"
  ${OrIf} $9 == "backend-demo.lock"
    StrCpy $8 0xC0010080
    StrCpy $7 3
  ${EndIf}
  System::Call 'kernel32::CreateFileW(w r0, i r8, i r7, p 0, i 3, i 0x02200000, p 0) p .r1'
  ${If} $1 = -1
    StrCpy $WorkleadPurgeStatus 12
    Goto node_done
  ${EndIf}
  System::Alloc 52
  Pop $4
  ${If} $4 = 0
    StrCpy $WorkleadPurgeStatus 18
    Goto node_close
  ${EndIf}
  System::Call 'kernel32::GetFileInformationByHandle(p r1, p r4) i .r5'
  System::Call '*$4(i .r2)'
  System::Free $4
  IntOp $4 $2 & 0x400
  IntOp $6 $2 & 0x10
  ${If} $5 = 0
  ${OrIf} $4 != 0
  ${OrIf} $6 != $3
    StrCpy $WorkleadPurgeStatus 11
    Goto node_close
  ${EndIf}
  System::Call 'kernel32::GetFinalPathNameByHandleW(p r1, w .r4, i ${NSIS_MAX_STRLEN}, i 0) i .r5'
  StrCpy $6 "\\?\$0"
  ${If} $5 = 0
  ${OrIf} $5 >= ${NSIS_MAX_STRLEN}
  ${OrIf} $4 != $6
    StrCpy $WorkleadPurgeStatus 15
    Goto node_close
  ${EndIf}
  ${If} $9 == "backend.lock"
  ${OrIf} $9 == "backend-demo.lock"
    System::Call '*(i 0, i 0, i 0, i 0, i 0) p .r4'
    ${If} $4 = 0
      StrCpy $WorkleadPurgeStatus 18
      Goto node_close
    ${EndIf}
    System::Call 'kernel32::LockFileEx(p r1, i 3, i 0, i 1, i 0, p r4) i .r5'
    System::Free $4
    ${If} $5 = 0
      StrCpy $WorkleadPurgeStatus 16
      Goto node_close
    ${EndIf}
  ${EndIf}
  System::Call '*(p r1, p $WorkleadPurgeNodes, i r2) p .r4'
  ${If} $4 = 0
    StrCpy $WorkleadPurgeStatus 18
    Goto node_close
  ${EndIf}
  StrCpy $WorkleadPurgeNodes $4
  IntOp $3 $2 & 0x10
  ${If} $3 = 0
    Goto node_done
  ${EndIf}
  System::Alloc 592
  Pop $7
  ${If} $7 = 0
    StrCpy $WorkleadPurgeStatus 18
    Goto node_done
  ${EndIf}
  System::Call 'kernel32::FindFirstFileW(w "$0\*", p r7) p .r4 ?e'
  Pop $6
  ${If} $4 = -1
    ; Empty-directory enumeration is valid; any other failure is not.
    ${If} $6 != 2
    ${AndIf} $6 != 18
      StrCpy $WorkleadPurgeStatus 13
    ${EndIf}
    System::Free $7
    Goto node_done
  ${EndIf}
  node_each:
    IntOp $8 $7 + 44
    System::Call '*$8(&w260 .r5)'
    ${If} $5 == "."
    ${OrIf} $5 == ".."
      Goto node_next
    ${EndIf}
    Push "$0\$5"
    Call un.WorkleadPreflightNode
    ${If} $WorkleadPurgeStatus != 0
      Goto node_find_close
    ${EndIf}
    node_next:
    System::Call 'kernel32::FindNextFileW(p r4, p r7) i .r5 ?e'
    Pop $6
    ${If} $5 != 0
      Goto node_each
    ${EndIf}
    ${If} $6 != 18
      StrCpy $WorkleadPurgeStatus 13
    ${EndIf}
  node_find_close:
    System::Call 'kernel32::FindClose(p r4)'
    System::Free $7
    Goto node_done
  node_close:
    System::Call 'kernel32::CloseHandle(p r1)'
  node_done:
  Pop $9
  Pop $8
  Pop $7
  Pop $6
  Pop $5
  Pop $4
  Pop $3
  Pop $2
  Pop $1
  Pop $0
FunctionEnd

; Delete the very objects inspected, never a substituted pathname. Preflight
; failure skips all deletion. A commit failure skips remaining entries.
Function un.WorkleadFinishData
  Push $0
  Push $1
  Push $2
  Push $3
  Push $4
  finish_next:
    ${If} $WorkleadPurgeNodes = 0
      Goto finish_pins
    ${EndIf}
    StrCpy $0 $WorkleadPurgeNodes
    System::Call '*$0(p .r1, p .r2)'
    StrCpy $WorkleadPurgeNodes $2
    ${If} $WorkleadPurgeStatus = 0
      ; The template may already have called Delete on an opened program file.
      ; Windows then refuses a second disposition change. Query the same handle's
      ; FileStandardInfo.DeletePending (byte 20) and close an already queued file.
      System::Alloc 24
      Pop $3
      ${If} $3 = 0
        StrCpy $WorkleadPurgeStatus 18
      ${Else}
        System::Call 'kernel32::GetFileInformationByHandleEx(p r1, i 1, p r3, i 24) i .r4'
        ${If} $4 = 0
          StrCpy $WorkleadPurgeStatus 14
        ${Else}
          IntOp $4 $3 + 20
          System::Call '*$4(&i1 .r4)'
        ${EndIf}
        System::Free $3
        ${If} $WorkleadPurgeStatus = 0
        ${AndIf} $4 = 0
          System::Call '*(&i1 1) p .r3'
          ${If} $3 = 0
            StrCpy $WorkleadPurgeStatus 18
          ${Else}
            System::Call 'kernel32::SetFileInformationByHandle(p r1, i 4, p r3, i 1) i .r4'
            System::Free $3
            ${If} $4 = 0
              StrCpy $WorkleadPurgeStatus 14
            ${EndIf}
          ${EndIf}
        ${EndIf}
      ${EndIf}
    ${EndIf}
    System::Call 'kernel32::CloseHandle(p r1) i .r4'
    ${If} $4 = 0
    ${AndIf} $WorkleadPurgeStatus = 0
      StrCpy $WorkleadPurgeStatus 14
    ${EndIf}
    System::Free $0
    Goto finish_next
  finish_pins:
    ${If} $WorkleadPinNodes = 0
      Goto finish_done
    ${EndIf}
    StrCpy $0 $WorkleadPinNodes
    System::Call '*$0(p .r1, p .r2)'
    StrCpy $WorkleadPinNodes $2
    System::Call 'kernel32::CloseHandle(p r1)'
    System::Free $0
    Goto finish_pins
  finish_done:
  Pop $4
  Pop $3
  Pop $2
  Pop $1
  Pop $0
FunctionEnd

Function un.WorkleadSkipConfirm
  ${If} $PassiveMode = 1
  ${OrIf} $UpdateMode = 1
    StrCpy $DeleteAppDataCheckboxState 0
    Abort
  ${EndIf}
FunctionEnd

; Root must be a directory (or absent); never delete an unrelated file with the
; same name as an app folder. Only the fixed known-folder children call this.
Function un.WorkleadPreflightRoot
  Exch $0
  Push $1
  ${If} $WorkleadPurgeStatus = 0
    System::Call 'kernel32::GetFileAttributesW(w r0) i .r1'
    ${If} $1 != -1
      IntOp $1 $1 & 16
      ${If} $1 = 0
        StrCpy $WorkleadPurgeStatus 10
      ${EndIf}
    ${EndIf}
    Push $0
    Call un.WorkleadPreflightNode
  ${EndIf}
  Pop $1
  Pop $0
FunctionEnd

Function un.WorkleadPreflightFile
  Exch $0
  Push $1
  ${If} $WorkleadPurgeStatus = 0
    System::Call 'kernel32::GetFileAttributesW(w r0) i .r1'
    ${If} $1 != -1
      IntOp $1 $1 & 16
      ${If} $1 != 0
        StrCpy $WorkleadPurgeStatus 10
      ${EndIf}
    ${EndIf}
    Push $0
    Call un.WorkleadPreflightNode
  ${EndIf}
  Pop $1
  Pop $0
FunctionEnd

Function un.WorkleadPrepareUninstall
  StrCpy $WorkleadPurgeStatus 0
  StrCpy $WorkleadPurgeNodes 0
  StrCpy $WorkleadPinNodes 0
  ${If} ${Silent}
  ${OrIf} $PassiveMode = 1
  ${OrIf} $UpdateMode = 1
    StrCpy $DeleteAppDataCheckboxState 0
  ${EndIf}
  ; Conservative name match protects the GUI even before its backend has started.
  ; Unlike the Tauri default, never force-kill an app or backend during uninstall.
  !insertmacro WORKLEAD_ENSURE_EXITED
  ; Resolve current-user shell folders; no environment override or command-line
  ; target is accepted. Pin the install path before the template deletes binaries.
  SetShellVarContext current
  Push $INSTDIR
  Call un.WorkleadPinPath
  StrCpy $WorkleadInstallHandle $WorkleadLastPin
  ; If the data tree will own this legacy install root, it checks the binaries
  ; once. Duplicate open handles would delay root removal until their last close.
  ${If} $DeleteAppDataCheckboxState != 1
    Push "$INSTDIR\${MAINBINARYNAME}.exe"
    Call un.WorkleadPreflightFile
    Push "$INSTDIR\${WORKLEAD_BACKEND_NAME}"
    Call un.WorkleadPreflightFile
  ${ElseIf} $INSTDIR != "$LOCALAPPDATA\Worklead"
  ${AndIf} $INSTDIR != "$LOCALAPPDATA\app.worklead.desktop"
  ${AndIf} $INSTDIR != "$APPDATA\app.worklead.desktop"
    Push "$INSTDIR\${MAINBINARYNAME}.exe"
    Call un.WorkleadPreflightFile
    Push "$INSTDIR\${WORKLEAD_BACKEND_NAME}"
    Call un.WorkleadPreflightFile
  ${EndIf}
  ${If} $DeleteAppDataCheckboxState = 1
  ${AndIf} $WorkleadPurgeStatus = 0
    Push $LOCALAPPDATA
    Call un.WorkleadPinPath
    Push $APPDATA
    Call un.WorkleadPinPath
    ; Release the install root pin when it is one of the owned data roots.
    ; DELETE-capable data handles replace it; ancestor pins stay in place.
    ${If} $INSTDIR == "$LOCALAPPDATA\Worklead"
    ${OrIf} $INSTDIR == "$LOCALAPPDATA\app.worklead.desktop"
    ${OrIf} $INSTDIR == "$APPDATA\app.worklead.desktop"
      Call un.WorkleadReleaseInstallPin
    ${EndIf}
    Push "$LOCALAPPDATA\Worklead"
    Call un.WorkleadPreflightRoot
    Push "$LOCALAPPDATA\app.worklead.desktop"
    Call un.WorkleadPreflightRoot
    Push "$APPDATA\app.worklead.desktop"
    Call un.WorkleadPreflightRoot
  ${EndIf}
  ${If} $WorkleadPurgeStatus != 0
    Call un.WorkleadFinishData
    IfSilent +2
      MessageBox MB_OK|MB_ICONEXCLAMATION "$(WorkleadUnsafeData)"
    SetErrorLevel $WorkleadPurgeStatus
    Abort
  ${EndIf}
FunctionEnd

; Remove the recorded install root pin, leaving all ancestor pins intact.
Function un.WorkleadReleaseInstallPin
  Push $0
  Push $1
  Push $2
  Push $3
  Push $4
  StrCpy $0 $WorkleadPinNodes
  StrCpy $3 0
  release_scan:
    ${If} $0 = 0
      Goto release_done
    ${EndIf}
    System::Call '*$0(p .r1, p .r2)'
    ${If} $1 = $WorkleadInstallHandle
      ${If} $3 = 0
        StrCpy $WorkleadPinNodes $2
      ${Else}
        System::Call '*$3(p .r4)'
        System::Call '*$3(p r4, p r2)'
      ${EndIf}
      System::Call 'kernel32::CloseHandle(p r1)'
      System::Free $0
      Goto release_done
    ${EndIf}
    StrCpy $3 $0
    StrCpy $0 $2
    Goto release_scan
  release_done:
  Pop $4
  Pop $3
  Pop $2
  Pop $1
  Pop $0
FunctionEnd
!endif
