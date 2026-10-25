@echo off
REM Starts the OWNER CONSOLE (plans, companies, payments, e-mail) on http://127.0.0.1:8001/
REM It is a separate website with its own login and its own database (owner.sqlite3).
REM First time only:  python manage.py migrate   and   python manage.py setup_owner --email you@example.com --name "Your Name" --seed-plans
cd /d "%~dp0"
python manage.py runserver 8001 --settings=config.owner_settings
