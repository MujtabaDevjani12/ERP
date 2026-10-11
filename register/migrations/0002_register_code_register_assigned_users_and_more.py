from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("register", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="register",
            name="code",
            field=models.CharField(blank=True, help_text="Short code, e.g. C01. Unique within the branch.", max_length=20),
        ),
        migrations.AddField(
            model_name="register",
            name="default_payment_method",
            field=models.CharField(blank=True, help_text="Payment method preselected at this counter (code, e.g. cash).", max_length=15),
        ),
        migrations.AddField(
            model_name="register",
            name="assigned_users",
            field=models.ManyToManyField(blank=True, help_text="Only these users can open this counter. Leave empty to allow every cashier of the branch.", related_name="assigned_registers", to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddConstraint(
            model_name="register",
            constraint=models.UniqueConstraint(condition=models.Q(("code", ""), _negated=True), fields=("store", "code"), name="uniq_register_code_per_store"),
        ),
        migrations.AddField(
            model_name="registersession",
            name="opening_note",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AlterField(
            model_name="registersession",
            name="note",
            field=models.TextField(blank=True, help_text="Closing note."),
        ),
        migrations.AddField(
            model_name="registersession",
            name="expected_cash_at_close",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True),
        ),
        migrations.AddField(
            model_name="registersession",
            name="total_sales_at_close",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True),
        ),
        migrations.AddField(
            model_name="registersession",
            name="transactions_at_close",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="registersession",
            name="closing_summary",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddIndex(
            model_name="registersession",
            index=models.Index(fields=["status", "opened_at"], name="regsession_status_opened_idx"),
        ),
    ]
