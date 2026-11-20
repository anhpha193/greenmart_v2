from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="OrderStockApplied",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("order_id", models.BigIntegerField(unique=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
        ),
    ]
