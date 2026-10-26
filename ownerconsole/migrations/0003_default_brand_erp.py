from django.db import migrations, models


def rename_default_brand(apps, schema_editor):
    """The built-in name "Multiple POS" becomes the neutral default "ERP". A name the owner typed himself is not touched."""
    Settings = apps.get_model("ownerconsole", "PlatformSettings")
    Settings.objects.filter(business_name="Multiple POS").update(business_name="ERP")


class Migration(migrations.Migration):
    dependencies = [("ownerconsole", "0002_platform_extras")]
    operations = [
        migrations.AlterField(model_name="platformsettings", name="business_name",
                              field=models.CharField(default="ERP", help_text="Shown on invoices, e-mails and plan pages.", max_length=150)),
        migrations.RunPython(rename_default_brand, migrations.RunPython.noop),
    ]
