@echo off
REM SPDX-License-Identifier: AGPL-3.0-or-later
title Start Studio Hub
echo Starting Studio Hub...
schtasks /Change /TN StudioHubWatchdog /ENABLE >nul 2>&1
schtasks /Run /TN StudioHub >nul 2>&1
echo Waiting for the hub to answer...
for /L %%i in (1,1,15) do (
  curl.exe -s --max-time 3 http://localhost:8001/api/health >nul 2>&1 && goto up
  ping -n 4 127.0.0.1 >nul
)
echo Hub did not answer yet - give it another few seconds, then open the bench page.
goto done
:up
echo Hub is UP: http://localhost:8001/bench
:done
pause
