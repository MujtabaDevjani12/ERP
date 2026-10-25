from django.core.management.base import BaseCommand

from ownerconsole import services


class Command(BaseCommand):
    help = ("Daily billing job: moves subscriptions between trial/active/payment-due/expired, creates renewal invoices and sends "
            "'N days left' reminders. Safe to run as often as you like. Schedule it once a day (cron or Windows Task Scheduler).")

    def handle(self, *args, **options):
        t = services.run_cycle()
        self.stdout.write(self.style.SUCCESS(
            f"Checked {t['checked']} companies - {t['invoices']} invoice(s) created, {t['reminders']} reminder(s) sent, {t['expired']} newly expired."))
