# Deployment checklist

1. **Back up first**: copy the database file (or `pg_dump`) and the `media/` folder. Keep the copy somewhere off the server.
2. Environment variables (never commit them): `SECRET_KEY` (long random), `DEBUG=False`, `ALLOWED_HOSTS=your.domain`,
   e-mail settings (`EMAIL_HOST` ...) if you want invoice mail / notifications. Set `NOTIFY_BY_EMAIL=True` to e-mail alerts.
3. `pip install -r requirements.txt` (add `djangorestframework` only if you want the API).
4. `python manage.py check --deploy`
5. `python manage.py migrate`  (additive; review `showmigrations` first on a copy of production data)
6. First time only: `bootstrap_company`, `seed_chart_of_accounts`, `seed_roles`, then `backfill_stock_ledger --apply` (see RUN guide).
7. **Do not run `seed_demo` or `seed_demo_erp` in production.** `seed_demo_erp` refuses to run when `DEBUG=False`.
   The older `seed_demo` creates `admin / admin12345`, `manager / manager12345`, `cashier / cashier12345`: if it was ever run on a database that
   is now live, change those passwords immediately or deactivate the users.
8. Serve with gunicorn / waitress behind nginx / IIS with HTTPS; `python manage.py collectstatic`.
9. Schedule daily: `python manage.py generate_notifications` (Windows Task Scheduler or cron).
10. Schedule backups. Keep the audit log (it is append-only).

## Switching features on

* **POS -> accounting**: admin -> POS settings -> tick *Post to accounting*, then optionally `post_pos_to_accounting --company "..." --apply`.
* **Approvals**: admin -> Approval workflows. Until you create one for a document type, that type is approved the moment it is submitted.
* **Negative stock**: admin -> Stock policies (default: not allowed for ERP documents; the POS keeps its own *allow over-selling* setting).

## Rollback

Restore the database backup and the previous code. Migrations are additive, so older code still runs against a newer database
(new columns/tables are ignored), but do not run an older code version's `migrate` backwards over live data.
