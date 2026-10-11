import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("configuration", "0003_systemconfiguration_font_family_and_more"),
        ("tenants", "0002_store_code_store_company_warehouse_code_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="Currency",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("code", models.CharField(help_text="International code, e.g. USD, AED, GBP.", max_length=6)),
                ("name", models.CharField(blank=True, help_text="Optional, e.g. US Dollar.", max_length=60)),
                ("symbol", models.CharField(help_text="Printed before the amount, e.g. $, AED, £.", max_length=8)),
                ("rate", models.DecimalField(decimal_places=6, help_text="How many units of your own (base) currency equal ONE unit of this currency. Example: 280 if 1 USD = 280 PKR.", max_digits=18)),
                ("decimals", models.PositiveSmallIntegerField(default=2, help_text="Decimal places to print (0 for whole numbers).")),
                ("is_active", models.BooleanField(default=True)),
                ("store", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="currencies", to="tenants.store")),
            ],
            options={
                "verbose_name_plural": "Currencies",
                "ordering": ["code"],
            },
        ),
        migrations.AddConstraint(
            model_name="currency",
            constraint=models.UniqueConstraint(fields=("store", "code"), name="uniq_currency_code_per_store"),
        ),
    ]
