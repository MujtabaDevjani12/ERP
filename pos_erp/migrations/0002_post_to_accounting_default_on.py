"""POS -> accounting posting is now ON by default, so every POS sale shows up in Accounting > Journal entries.

Existing companies were created with the old default (OFF), so this also switches them ON once.
Sales made before this migration are posted with:  python manage.py post_pos_to_accounting --company "<name>" --apply
"""
from django.db import migrations, models


def switch_on(apps, schema_editor):
    Company = apps.get_model("company", "Company")
    PosSettings = apps.get_model("pos_erp", "PosSettings")
    PosSettings.objects.filter(post_to_accounting=False).update(post_to_accounting=True)
    for company in Company.objects.filter(pos_settings__isnull=True):
        PosSettings.objects.create(company=company, post_to_accounting=True)


class Migration(migrations.Migration):

    dependencies = [
        ("company", "0001_initial"),
        ("pos_erp", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="possettings",
            name="post_to_accounting",
            field=models.BooleanField(
                default=True,
                help_text="When ON, every POS sale, payment, return and shift close also posts accounting entries.",
            ),
        ),
        migrations.RunPython(switch_on, migrations.RunPython.noop),
    ]
