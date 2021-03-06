# Generated by Django 2.2.4 on 2020-07-27 17:23

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("flows", "0236_flowstart_session_history"),
    ]

    operations = [
        migrations.AlterField(
            model_name="flow",
            name="base_language",
            field=models.CharField(
                blank=True,
                default="base",
                help_text="The authoring language, additional languages can be added later",
                max_length=4,
                null=True,
            ),
        ),
    ]
