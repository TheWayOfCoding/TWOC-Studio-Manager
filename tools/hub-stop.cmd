@echo off
REM SPDX-License-Identifier: AGPL-3.0-or-later
title Stop Studio Hub
echo Stopping Studio Hub...
schtasks /End /TN StudioHub >nul 2>&1
schtasks /Change /TN StudioHubWatchdog /DISABLE >nul 2>&1
echo Hub stopped. The watchdog is paused, so it will stay stopped.
echo Use Start Studio Hub to bring it back.
pause
