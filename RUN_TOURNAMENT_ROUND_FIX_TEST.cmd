@echo off
setlocal
cd /d "%~dp0"

rem Experimental acknowledgement for resumed/progressed tournament rounds.
rem Full tournament progress is still saved; only the immediate reply from
rem round 2 onward is reduced to the parser-safe tournament identity object.
set "FIFA14_TOURNAMENT_UPDATE_ACK=auto"

echo ============================================================
echo  FIFA 14 LOCAL FUT - TOURNAMENT ROUND FIX TEST
echo ============================================================
echo Round 1 uses the established response.
echo Round 2 and later use the minimal safe acknowledgement.
echo.

call ".\RUN_FIFA14_LOCAL_BETA.cmd"
exit /b %errorlevel%
