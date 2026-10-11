from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("expenses", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="expense",
            name="method",
            field=models.CharField(choices=[("cash", "Cash"), ("card", "Card"), ("bank_transfer", "Bank transfer"), ("easypaisa", "Easypaisa"), ("jazzcash", "JazzCash"), ("other", "Other"), ("cheque", "Cheque"), ("gift_card", "Gift card")], default="cash", max_length=15),
        ),
    ]
