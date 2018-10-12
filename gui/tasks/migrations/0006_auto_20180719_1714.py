# -*- coding: utf-8 -*-
# Generated by Django 1.10.8 on 2018-07-19 17:14
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tasks', '0005_merge_20180617_1001'),
    ]

    operations = [
        migrations.AddField(
            model_name='cloudsync',
            name='post_script',
            field=models.TextField(blank=True, help_text='Script to execute after running sync.', verbose_name='Post-script'),
        ),
        migrations.AddField(
            model_name='cloudsync',
            name='pre_script',
            field=models.TextField(blank=True, help_text='Script to execute before running sync.', verbose_name='Pre-script'),
        ),
        migrations.AddField(
            model_name='cloudsync',
            name='snapshot',
            field=models.BooleanField(default=False, help_text='Take dataset snapshot before pushing data.', verbose_name='Take snapshot'),
            preserve_default=False,
        ),
    ]
