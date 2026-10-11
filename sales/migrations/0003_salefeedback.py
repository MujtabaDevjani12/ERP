import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("sales", "0002_sale_checkout_token_poscart_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="SaleFeedback",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("rating", models.PositiveSmallIntegerField(help_text="1 (poor) to 5 (excellent).")),
                ("comment", models.TextField(blank=True, max_length=1000)),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
                ("sale", models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name="feedback", to="sales.sale")),
            ],
            options={
                "verbose_name_plural": "Sale feedback",
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddConstraint(
            model_name="salefeedback",
            constraint=models.CheckConstraint(check=models.Q(("rating__gte", 1), ("rating__lte", 5)), name="salefeedback_rating_1_5"),
        ),
    ]
