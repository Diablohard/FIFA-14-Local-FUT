@echo off
setlocal
cd /d "%~dp0"

rem Experimental acknowledgement for the post-second-match round transition.
rem Full tournament progress is still saved; only the immediate round-3 reply
rem is reduced to the parser-safe tournament identity object.
set "FIFA14_TOURNAMENT_UPDATE_ACK=auto"

echo ============================================================
echo  FIFA 14 LOCAL FUT - TOURNAMENT ROUND FIX TEST
echo ============================================================
echo Round 1 and 2 use the established response.
echo Round 3 and later use the minimal safe acknowledgement.
echo.

call ".\RUN_FIFA14_LOCAL_BETA.cmd"
exit /b %errorlevel%
