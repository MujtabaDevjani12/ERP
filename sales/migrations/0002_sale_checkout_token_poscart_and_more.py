import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("register", "0001_initial"),
        ("sales", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="sale",
            name="checkout_token",
            field=models.CharField(blank=True, help_text="Random id the POS sends with every checkout attempt, so a double click / retry can never create two sales.", max_length=64, null=True, unique=True),
        ),
        migrations.AddIndex(
            model_name="sale",
            index=models.Index(fields=["status", "created_at"], name="sale_status_created_idx"),
        ),
        migrations.AddIndex(
            model_name="sale",
            index=models.Index(fields=["store", "created_at"], name="sale_store_created_idx"),
        ),
        migrations.CreateModel(
            name="PosCart",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("data", models.JSONField(blank=True, default=dict)),
                ("version", models.PositiveIntegerField(default=0)),
                ("register_session", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="cart", to="register.registersession")),
            ],
        ),
    ]
