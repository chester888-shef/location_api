from django.db import migrations


def seed_cache_revision(apps, schema_editor):
    revision = apps.get_model("core", "CacheRevision")
    revision.objects.using(schema_editor.connection.alias).get_or_create(name="locations")


class Migration(migrations.Migration):
    dependencies = [("core", "0001_initial")]
    operations = [migrations.RunPython(seed_cache_revision, migrations.RunPython.noop)]
