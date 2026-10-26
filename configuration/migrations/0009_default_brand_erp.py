from django.db import migrations, models


def rename_default_brand(apps, schema_editor):
    """The built-in names ("My POS" / "Multiple POS") become the new neutral default "ERP". A name the owner typed himself is not touched."""
    Config = apps.get_model("configuration", "SystemConfiguration")
    Config.objects.filter(site_name__in=["My POS", "Multiple POS"]).update(site_name="ERP")


class Migration(migrations.Migration):
    dependencies = [("configuration", "0008_receipt_designer")]
    operations = [
        migrations.AlterField(model_name="systemconfiguration", name="site_name", field=models.CharField(default="ERP", max_length=150)),
        migrations.RunPython(rename_default_brand, migrations.RunPython.noop),
    ]
