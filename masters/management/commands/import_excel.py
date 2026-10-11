from django.core.management.base import BaseCommand, CommandError

from company.models import Company
from masters import importing


class Command(BaseCommand):
    help = ("Validate (and optionally import) an Excel file. Dry run by default.\n"
            "  python manage.py import_excel items items.xlsx\n"
            "  python manage.py import_excel items items.xlsx --confirm")

    def add_arguments(self, parser):
        parser.add_argument("import_type", choices=sorted(importing.IMPORT_TYPES) or None)
        parser.add_argument("path")
        parser.add_argument("--company", default="")
        parser.add_argument("--confirm", action="store_true", help="Import after a clean validation.")

    def handle(self, *args, **opts):
        company = Company.objects.filter(name=opts["company"]).first() if opts["company"] else Company.objects.first()
        if company is None:
            raise CommandError("No company found. Run bootstrap_company first.")
        with open(opts["path"], "rb") as fh:
            job = importing.create_import_job(company, opts["import_type"], fh)
        self.stdout.write(job.summary)
        for item in job.errors[:200]:
            self.stdout.write(self.style.ERROR(f"  row {item['row']}: " + "; ".join(item["errors"])))
        if job.status != job.VALIDATED:
            raise CommandError("Fix the errors above and run again. Nothing was imported.")
        if opts["confirm"]:
            importing.confirm_import(job)
            self.stdout.write(self.style.SUCCESS("Imported."))
        else:
            self.stdout.write("Dry run only. Add --confirm to import.")
