from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0010_delete_environmentvariable'),
    ]

    operations = [
        # Drop django-allauth tables if they still exist
        migrations.RunSQL(
            sql="DROP TABLE IF EXISTS socialaccount_socialtoken;",
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.RunSQL(
            sql="DROP TABLE IF EXISTS socialaccount_socialapp_sites;",
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.RunSQL(
            sql="DROP TABLE IF EXISTS socialaccount_socialaccount;",
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.RunSQL(
            sql="DROP TABLE IF EXISTS socialaccount_socialapp;",
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]