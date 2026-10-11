import django.db.models.deletion
from django.db import migrations, models
from django.db.models import OuterRef, Subquery

METHODS = [
    ("cash", "Cash", False, True, 10),
    ("card", "Card", True, True, 20),
    ("bank_transfer", "Bank transfer", True, True, 30),
    ("easypaisa", "Easypaisa", True, True, 40),
    ("jazzcash", "JazzCash", True, True, 50),
    ("other", "Other", True, True, 60),
    ("cheque", "Cheque", True, True, 70),
    ("gift_card", "Gift card", False, True, 80),
]


def seed_methods_and_backfill(apps, schema_editor):
    """Give every existing branch its payment-method list, and link old payments to the till session of their sale.
    Only adds / fills blanks - no existing sale, payment or refund value is changed."""
    Store = apps.get_model("tenants", "Store")
    PaymentMethod = apps.get_model("payments", "PaymentMethod")
    Payment = apps.get_model("payments", "Payment")
    Sale = apps.get_model("sales", "Sale")

    for store in Store.objects.all():
        have = set(PaymentMethod.objects.filter(store=store).values_list("code", flat=True))
        PaymentMethod.objects.bulk_create([
            PaymentMethod(store=store, code=code, name=name, requires_reference=ref, is_active=active, sort_order=order)
            for code, name, ref, active, order in METHODS if code not in have
        ])

    session_of_sale = Sale.objects.filter(pk=OuterRef("sale_id")).values("register_session_id")[:1]
    Payment.objects.filter(register_session__isnull=True, sale__register_session__isnull=False).update(
        register_session_id=Subquery(session_of_sale))


class Migration(migrations.Migration):

    dependencies = [
        ("payments", "0001_initial"),
        ("register", "0001_initial"),
        ("sales", "0001_initial"),
        ("tenants", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="PaymentMethod",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("code", models.CharField(max_length=15)),
                ("name", models.CharField(max_length=60)),
                ("requires_reference", models.BooleanField(default=False, help_text="Cashier must type a reference / transaction ID before the sale can be completed.")),
                ("is_active", models.BooleanField(default=True)),
                ("sort_order", models.PositiveIntegerField(default=0)),
                ("store", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="payment_methods", to="tenants.store")),
            ],
            options={
                "ordering": ["sort_order", "name"],
            },
        ),
        migrations.AddConstraint(
            model_name="paymentmethod",
            constraint=models.UniqueConstraint(fields=("store", "code"), name="uniq_payment_method_code_per_store"),
        ),
        migrations.AddField(
            model_name="payment",
            name="reference_no",
            field=models.CharField(blank=True, help_text="Card / bank / wallet transaction ID.", max_length=100),
        ),
        migrations.AddField(
            model_name="payment",
            name="register_session",
            field=models.ForeignKey(blank=True, help_text="The till session that took this money (counter and cashier follow from it).", null=True, on_delete=django.db.models.deletion.PROTECT, related_name="payments", to="register.registersession"),
        ),
        migrations.AddField(
            model_name="payment",
            name="status",
            field=models.CharField(choices=[("completed", "Completed"), ("refunded", "Refunded"), ("voided", "Voided")], default="completed", max_length=10),
        ),
        migrations.AlterField(
            model_name="payment",
            name="method",
            field=models.CharField(choices=[("cash", "Cash"), ("card", "Card"), ("bank_transfer", "Bank transfer"), ("easypaisa", "Easypaisa"), ("jazzcash", "JazzCash"), ("other", "Other"), ("cheque", "Cheque"), ("gift_card", "Gift card")], default="cash", max_length=15),
        ),
        migrations.AlterField(
            model_name="refund",
            name="method",
            field=models.CharField(choices=[("cash", "Cash"), ("card", "Card"), ("bank_transfer", "Bank transfer"), ("easypaisa", "Easypaisa"), ("jazzcash", "JazzCash"), ("other", "Other"), ("cheque", "Cheque"), ("gift_card", "Gift card")], default="cash", max_length=15),
        ),
        migrations.AddField(
            model_name="refund",
            name="register_session",
            field=models.ForeignKey(blank=True, help_text="The till session the refund was paid out of (cash refunds reduce that till's expected cash).", null=True, on_delete=django.db.models.deletion.PROTECT, related_name="refunds", to="register.registersession"),
        ),
        migrations.AddIndex(
            model_name="payment",
            index=models.Index(fields=["register_session", "method"], name="pay_session_method_idx"),
        ),
        migrations.AddIndex(
            model_name="payment",
            index=models.Index(fields=["paid_at"], name="pay_paid_at_idx"),
        ),
        migrations.RunPython(seed_methods_and_backfill, migrations.RunPython.noop),
    ]
