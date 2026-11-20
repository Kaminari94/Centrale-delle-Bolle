# Generated manually for QuickBolla offline synchronization.

from django.db import migrations, models
from django.utils import timezone


def populate_emission_year(apps, schema_editor):
    Bolla = apps.get_model("bolle", "Bolla")
    fallback_year = timezone.localdate().year
    for bolla in Bolla.objects.all().iterator():
        bolla.anno_emissione = bolla.data.year if bolla.data else fallback_year
        bolla.save(update_fields=["anno_emissione"])


class Migration(migrations.Migration):

    dependencies = [
        ("bolle", "0043_rigafattura_recupero_cls_unico"),
    ]

    operations = [
        migrations.AddField(
            model_name="bolla",
            name="anno_emissione",
            field=models.PositiveIntegerField(db_index=True, default=0),
        ),
        migrations.AddField(
            model_name="bolla",
            name="emission_id",
            field=models.UUIDField(blank=True, editable=False, null=True, unique=True),
        ),
        migrations.AddField(
            model_name="bolla",
            name="receipt_renderer",
            field=models.CharField(blank=True, default="", editable=False, max_length=40),
        ),
        migrations.AddField(
            model_name="bolla",
            name="receipt_text",
            field=models.TextField(blank=True, default="", editable=False),
        ),
        migrations.AddField(
            model_name="bolla",
            name="receipt_width",
            field=models.PositiveSmallIntegerField(blank=True, editable=False, null=True),
        ),
        migrations.AddField(
            model_name="bolla",
            name="sync_payload_hash",
            field=models.CharField(blank=True, default="", editable=False, max_length=64),
        ),
        migrations.RunPython(populate_emission_year, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="bolla",
            name="anno_emissione",
            field=models.PositiveIntegerField(db_index=True),
        ),
        migrations.AddConstraint(
            model_name="bolla",
            constraint=models.UniqueConstraint(
                fields=("tipo_documento", "anno_emissione", "numero"),
                name="uniq_bolla_tipo_anno_numero",
            ),
        ),
    ]
