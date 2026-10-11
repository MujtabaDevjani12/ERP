from django.db import migrations, models


class Migration(migrations.Migration):
    """Fields behind the Settings hub: business identity, outgoing-email sender, WhatsApp wording."""

    dependencies = [
        ("configuration", "0004_currency"),
    ]

    operations = [
        migrations.AddField(
            model_name="systemconfiguration",
            name="tagline",
            field=models.CharField(blank=True, help_text="Short line shown under the business name (login page, receipts).", max_length=150),
        ),
        migrations.AddField(
            model_name="systemconfiguration",
            name="website",
            field=models.CharField(blank=True, max_length=150),
        ),
        migrations.AddField(
            model_name="systemconfiguration",
            name="tax_number",
            field=models.CharField(blank=True, help_text="Printed on invoices and receipts.", max_length=40, verbose_name="Tax / NTN number"),
        ),
        migrations.AddField(
            model_name="systemconfiguration",
            name="email_from_name",
            field=models.CharField(blank=True, help_text="Shown as the sender name. Leave empty to use the business name.", max_length=100),
        ),
        migrations.AddField(
            model_name="systemconfiguration",
            name="email_from_address",
            field=models.EmailField(blank=True, help_text="Leave empty to use the server's DEFAULT_FROM_EMAIL. Must be an address your mail provider lets you send from.", max_length=254),
        ),
        migrations.AddField(
            model_name="systemconfiguration",
            name="email_reply_to",
            field=models.EmailField(blank=True, help_text="Where customer replies go.", max_length=254),
        ),
        migrations.AddField(
            model_name="systemconfiguration",
            name="whatsapp_country_code",
            field=models.CharField(default="92", help_text="Added in front of local phone numbers that start with 0 (92 for Pakistan).", max_length=5),
        ),
        migrations.AddField(
            model_name="systemconfiguration",
            name="whatsapp_message",
            field=models.TextField(
                blank=True,
                default="Hi {customer_name}, thank you for shopping with {store_name}. Your invoice {reference} for {total} is ready.",
                help_text="Placeholders: {customer_name}, {store_name}, {reference}, {total}.",
            ),
        ),
    ]
