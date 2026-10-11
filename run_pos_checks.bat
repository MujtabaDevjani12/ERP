@echo off
REM One-click verification for the POS upgrade. Double-click this file.
REM It backs up db.sqlite3, validates migrations, applies them, runs the tests, and writes everything to pos_check_log.txt
cd /d "%~dp0"
set LOG=%~dp0pos_check_log.txt
set PY=python
if exist "venv\Scripts\python.exe" set PY=venv\Scripts\python.exe
if exist ".venv\Scripts\python.exe" set PY=.venv\Scripts\python.exe
echo === POS CHECK %DATE% %TIME% === > "%LOG%"
echo Using: %PY% >> "%LOG%"
if exist db.sqlite3 (
  copy /Y db.sqlite3 "db.sqlite3.pre_pos_upgrade.bak" >> "%LOG%" 2>&1
  echo Backup written: db.sqlite3.pre_pos_upgrade.bak >> "%LOG%"
)
echo. >> "%LOG%" & echo --- manage.py check --- >> "%LOG%"
%PY% manage.py check >> "%LOG%" 2>&1
echo. >> "%LOG%" & echo --- makemigrations --check --dry-run --- >> "%LOG%"
%PY% manage.py makemigrations --check --dry-run >> "%LOG%" 2>&1
echo makemigrations exit code: %ERRORLEVEL% >> "%LOG%"
echo. >> "%LOG%" & echo --- showmigrations (plan) --- >> "%LOG%"
%PY% manage.py migrate --plan >> "%LOG%" 2>&1
echo. >> "%LOG%" & echo --- migrate --- >> "%LOG%"
%PY% manage.py migrate >> "%LOG%" 2>&1
echo migrate exit code: %ERRORLEVEL% >> "%LOG%"
echo. >> "%LOG%" & echo --- tests --- >> "%LOG%"
%PY% manage.py test register sales payments core catalog configuration --noinput -v 2 >> "%LOG%" 2>&1
echo tests exit code: %ERRORLEVEL% >> "%LOG%"
echo === DONE === >> "%LOG%"
echo Finished. Results saved to pos_check_log.txt
pause
