@echo off
rem ---------------------------------------------------------------------------
rem adb wrapper: lets you run
rem     android\tools\adb.cmd devices
rem without adding the SDK to PATH and without PowerShell execution policy
rem getting in the way (cmd files are not affected by it).
rem ---------------------------------------------------------------------------
"%~dp0..\..\.toolchain\android-sdk\platform-tools\adb.exe" %*
