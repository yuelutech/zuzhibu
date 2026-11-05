; 安装程序的补充规则（electron-builder 的 nsis.include）：
; 1. 这台电脑上已装了更新的版本时，不许装回旧版（静默安装以退出码 2 结束）。先在“设置 → 应用”里卸载再装。
; 2. --no-shortcuts：不放开始菜单和桌面快捷方式（静默部署、后台试装用），例如 setup.exe /S --no-shortcuts /D=目录
; 3. electron-builder 会把安装程序复制一份到 %LOCALAPPDATA%\<包名>-updater，留给自动增量更新；本软件不用自动更新，
;    装完、卸载时都删掉这份副本（百兆上下），不在 C 盘留东西。
; 升级时由安装程序先关掉安装目录里在运行的程序（含内置的 Python 本地服务），再卸旧版、装新版；本机数据和设置不动。
!include "WordFunc.nsh"
!include "FileFunc.nsh"

!ifdef APP_INSTALLER_STORE_FILE
  !searchreplace ZZB_INSTALLER_COPY_DIR "${APP_INSTALLER_STORE_FILE}" "\installer.exe" ""
!endif

!macro zzbRemoveInstallerCopy
  !ifdef APP_INSTALLER_STORE_FILE
    Delete "$LOCALAPPDATA\${APP_INSTALLER_STORE_FILE}"
    RMDir "$LOCALAPPDATA\${ZZB_INSTALLER_COPY_DIR}"
  !endif
!macroend

!macro customUnInstall
  !insertmacro zzbRemoveInstallerCopy
!macroend

!macro customInit
  ReadRegStr $0 HKCU "${UNINSTALL_REGISTRY_KEY}" "DisplayVersion"
  ${If} $0 != ""
    ${VersionCompare} "$0" "${VERSION}" $1
    ${If} $1 == "1"
      MessageBox MB_OK|MB_ICONEXCLAMATION "这台电脑上已经装了更新版本（$0）的 ${PRODUCT_NAME}。$\r$\n要换回这个版本，请先在“设置 → 应用”里卸载。" /SD IDOK
      SetErrorLevel 2
      Quit
    ${EndIf}
  ${EndIf}
!macroend

!macro customInstall
  !insertmacro zzbRemoveInstallerCopy
  ${GetParameters} $R0
  ClearErrors
  ${GetOptions} $R0 "--no-shortcuts" $R1
  ${IfNot} ${Errors}
    Delete "$newStartMenuLink"
    Delete "$newDesktopLink"
  ${EndIf}
!macroend
