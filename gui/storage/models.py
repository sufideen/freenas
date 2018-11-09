# Copyright 2010 iXsystems, Inc.
# All rights reserved
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted providing that the following conditions
# are met:
# 1. Redistributions of source code must retain the above copyright
#    notice, this list of conditions and the following disclaimer.
# 2. Redistributions in binary form must reproduce the above copyright
#    notice, this list of conditions and the following disclaimer in the
#    documentation and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE AUTHOR ``AS IS'' AND ANY EXPRESS OR
# IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED.  IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR ANY
# DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS
# OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION)
# HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT,
# STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING
# IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.
#
#####################################################################

from datetime import time
import pickle
import logging
import os
import re
import uuid
import subprocess

from django.db import models
from django.db.models import Q
from django.utils.translation import ugettext as __, ugettext_lazy as _

from freenasUI import choices
from freenasUI.freeadmin.models import ListField
from freenasUI.middleware import zfs
from freenasUI.middleware.notifier import notifier
from freenasUI.middleware.client import client
from freenasUI.freeadmin.models import Model, UserField
from freenasUI.system.models import SSHCredentialsKeychainCredential

log = logging.getLogger('storage.models')
REPL_RESULTFILE = '/tmp/.repl-result'


class Volume(Model):
    vol_name = models.CharField(
        unique=True,
        max_length=120,
        verbose_name=_("Name")
    )
    vol_guid = models.CharField(
        max_length=50,
        blank=True,
        editable=False,
    )
    vol_encrypt = models.IntegerField(
        choices=choices.VolumeEncrypt_Choices,
        default=0,
        verbose_name=_("Encryption Type"),
    )
    vol_encryptkey = models.CharField(
        max_length=50,
        blank=True,
        editable=False,
    )

    @property
    def is_upgraded(self):
        with client as c:
            return c.call('pool.is_upgraded', self.id)

    @property
    def vol_path(self):
        return '/mnt/%s' % self.vol_name

    class Meta:
        verbose_name = _("Volume")

    def get_disks(self):
        try:
            if not hasattr(self, '_disks'):
                n = notifier()
                if self.is_decrypted():
                    pool = n.zpool_parse(self.vol_name)
                    self._disks = pool.get_disks()
                else:
                    self._disks = []
                    for ed in self.encrypteddisk_set.all():
                        if not ed.encrypted_disk:
                            continue
                        if os.path.exists('/dev/{}'.format(ed.encrypted_disk.devname)):
                            self._disks.append(ed.encrypted_disk.devname)
            return self._disks
        except Exception as e:
            log.debug(
                "Exception on retrieving disks for %s: %s",
                self.vol_name,
                e)
            return []

    def get_children(self, hierarchical=True, include_root=True):
        return zfs.zfs_list(
            path=self.vol_name,
            recursive=True,
            types=["filesystem", "volume"],
            hierarchical=hierarchical,
            include_root=include_root)

    def get_datasets(self, hierarchical=False, include_root=False):
        return zfs.list_datasets(
            path=self.vol_name,
            recursive=True,
            hierarchical=hierarchical,
            include_root=include_root)

    def get_zvols(self):
        return notifier().list_zfs_vols(self.vol_name)

    def _get_status(self):
        try:
            # Make sure do not compute it twice
            if not hasattr(self, '_status'):
                status = notifier().get_volume_status(self.vol_name)
                if status == 'UNKNOWN' and self.vol_encrypt > 0:
                    return _("LOCKED")
                else:
                    self._status = status
            return self._status
        except Exception as e:
            if self.is_decrypted():
                log.debug(
                    "Exception on retrieving status for %s: %s",
                    self.vol_name,
                    e)
                return _("Error")
    status = property(_get_status)

    def get_geli_keyfile(self):
        from freenasUI.middleware.notifier import GELI_KEYPATH
        if not os.path.exists(GELI_KEYPATH):
            os.mkdir(GELI_KEYPATH)
        return "%s/%s.key" % (GELI_KEYPATH, self.vol_encryptkey, )

    def is_decrypted(self):
        __is_decrypted = getattr(self, '__is_decrypted', None)
        if __is_decrypted is not None:
            return __is_decrypted

        self.__is_decrypted = True
        # If the status is not UNKNOWN means the pool is already imported
        status = notifier().get_volume_status(self.vol_name)
        if status != 'UNKNOWN':
            return self.__is_decrypted
        if self.vol_encrypt > 0:
            _notifier = notifier()
            for ed in self.encrypteddisk_set.all():
                if not _notifier.geli_is_decrypted(ed.encrypted_provider):
                    self.__is_decrypted = False
                    break
        return self.__is_decrypted

    def delete(self, destroy=True, cascade=True):
        return super().delete()

    def save(self, *args, **kwargs):
        if not self.vol_encryptkey and self.vol_encrypt > 0:
            self.vol_encryptkey = str(uuid.uuid4())
        super(Volume, self).save(*args, **kwargs)

    def __str__(self):
        return self.vol_name

    def _get__zplist(self):
        if not hasattr(self, '__zplist'):
            try:
                self.__zplist = zfs.zpool_list().get(self.vol_name)
            except SystemError:
                self.__zplist = None
        return self.__zplist

    def _set__zplist(self, value):
        self.__zplist = value

    def _get_avail(self):
        try:
            return self._zplist['free']
        except:
            if self.is_decrypted():
                return __("Error getting available space")
            else:
                return __("Locked")

    def _get_used_bytes(self):
        try:
            return self._zplist['alloc']
        except:
            return 0

    def _get_used(self):
        try:
            return self._get_used_bytes()
        except:
            if self.is_decrypted():
                return __("Error getting used space")
            else:
                return __("Locked")

    def _get_used_pct(self):
        try:
            return "%d%%" % self._zplist['capacity']
        except:
            return __("Error")

    _zplist = property(_get__zplist, _set__zplist)
    avail = property(_get_avail)
    used_pct = property(_get_used_pct)
    used = property(_get_used)


class Scrub(Model):
    scrub_volume = models.OneToOneField(
        Volume,
        verbose_name=_("Volume"),
    )
    scrub_threshold = models.PositiveSmallIntegerField(
        verbose_name=_("Threshold days"),
        default=35,
        help_text=_("Determine how many days shall be between scrubs"),
    )
    scrub_description = models.CharField(
        max_length=200,
        verbose_name=_("Description"),
        blank=True,
    )
    scrub_minute = models.CharField(
        max_length=100,
        default="00",
        verbose_name=_("Minute"),
        help_text=_("Values 0-59 allowed."),
    )
    scrub_hour = models.CharField(
        max_length=100,
        default="00",
        verbose_name=_("Hour"),
        help_text=_("Values 0-23 allowed."),
    )
    scrub_daymonth = models.CharField(
        max_length=100,
        default="*",
        verbose_name=_("Day of month"),
        help_text=_("Values 1-31 allowed."),
    )
    scrub_month = models.CharField(
        max_length=100,
        default='*',
        verbose_name=_("Month"),
    )
    scrub_dayweek = models.CharField(
        max_length=100,
        default="7",
        verbose_name=_("Day of week"),
    )
    scrub_enabled = models.BooleanField(
        default=True,
        verbose_name=_("Enabled"),
    )

    class Meta:
        verbose_name = _("Scrub")
        verbose_name_plural = _("Scrubs")
        ordering = ["scrub_volume__vol_name"]

    def __str__(self):
        return self.scrub_volume.vol_name


class Resilver(Model):
    enabled = models.BooleanField(
        verbose_name=_('Enabled'),
        default=False,
    )
    begin = models.TimeField(
        default=time(hour=18),
        verbose_name=_('Begin higher priority resilvering at this time'),
    )
    end = models.TimeField(
        default=time(hour=9),
        verbose_name=_('End higher priority resilvering at this time'),
    )
    weekday = models.CharField(
        max_length=120,
        default='1,2,3,4,5,6,7',
        verbose_name=_('Weekday'),
        blank=True,
    )

    class Meta:
        verbose_name = _('Resilver Priority')

    class FreeAdmin:
        deletable = False

    def __str__(self):
        return '<Resilver Priority>'


class Disk(Model):
    disk_identifier = models.CharField(
        max_length=42,
        verbose_name=_("Identifier"),
        editable=False,
        primary_key=True,
    )
    disk_name = models.CharField(
        max_length=120,
        verbose_name=_("Name")
    )
    disk_subsystem = models.CharField(
        default='',
        max_length=10,
        editable=False,
    )
    disk_number = models.IntegerField(
        editable=False,
        default=1,
    )
    disk_serial = models.CharField(
        max_length=30,
        verbose_name=_("Serial"),
        blank=True,
    )
    disk_size = models.CharField(
        max_length=20,
        verbose_name=_('Disk Size'),
        editable=False,
        blank=True,
    )
    disk_multipath_name = models.CharField(
        max_length=30,
        verbose_name=_("Multipath name"),
        blank=True,
        editable=False,
    )
    disk_multipath_member = models.CharField(
        max_length=30,
        verbose_name=_("Multipath member"),
        blank=True,
        editable=False,
    )
    disk_description = models.CharField(
        max_length=120,
        verbose_name=_("Description"),
        blank=True
    )
    disk_transfermode = models.CharField(
        max_length=120,
        choices=choices.TRANSFERMODE_CHOICES,
        default="Auto",
        verbose_name=_("Transfer Mode")
    )
    disk_hddstandby = models.CharField(
        max_length=120,
        choices=choices.HDDSTANDBY_CHOICES,
        default="Always On",
        verbose_name=_("HDD Standby")
    )
    disk_advpowermgmt = models.CharField(
        max_length=120,
        choices=choices.ADVPOWERMGMT_CHOICES,
        default="Disabled",
        verbose_name=_("Advanced Power Management")
    )
    disk_acousticlevel = models.CharField(
        max_length=120,
        choices=choices.ACOUSTICLVL_CHOICES,
        default="Disabled",
        verbose_name=_("Acoustic Level")
    )
    disk_togglesmart = models.BooleanField(
        default=True,
        verbose_name=_("Enable S.M.A.R.T."),
    )
    disk_smartoptions = models.CharField(
        max_length=120,
        verbose_name=_("S.M.A.R.T. extra options"),
        blank=True
    )
    disk_expiretime = models.DateTimeField(
        null=True,
        editable=False,
    )
    disk_enclosure_slot = models.IntegerField(
        verbose_name=_("Enclosure Slot"),
        blank=True,
        null=True,
        editable=False,
    )
    disk_passwd = models.CharField(
        max_length=120,
        verbose_name=_("Password for SED"),
        blank=True
    )

    def identifier_to_device(self):
        """
        Get the corresponding device name from disk_identifier field
        """
        return notifier().identifier_to_device(self.disk_identifier)

    @property
    def devname(self):
        if self.disk_multipath_name:
            return "multipath/%s" % self.disk_multipath_name
        else:
            return self.disk_name

    class Meta:
        verbose_name = _("Disk")
        verbose_name_plural = _("Disks")
        ordering = ["disk_subsystem", "disk_number"]

    def __str__(self):
        return str(self.disk_name)


class EncryptedDisk(Model):
    encrypted_volume = models.ForeignKey(Volume)
    encrypted_disk = models.ForeignKey(
        Disk,
        on_delete=models.SET_NULL,
        null=True,
    )
    encrypted_provider = models.CharField(
        unique=True,
        max_length=120,
        verbose_name=_("Underlying provider"),
    )


class Replication(Model):
    repl_direction = models.CharField(
        max_length=4,
        choices=[("PUSH", "Push"), ("PULL", "Pull")],
        default="push",
        verbose_name=_("Direction"),
    )
    repl_transport = models.CharField(
        max_length=10,
        choices=[("SSH", "SSH"), ("SSH+NETCAT", "SSH+netcat"), ("LOCAL", "Local"), ("LEGACY", "Legacy")],
        default="ssh",
        verbose_name=_("Transport"),
    )
    repl_ssh_credentials = models.ForeignKey(
        SSHCredentialsKeychainCredential,
        null=True,
        verbose_name=_("SSH Connection"),
    )
    repl_netcat_active_side = models.CharField(
        max_length=5,
        choices=[("LOCAL", "Local"), ("REMOTE", "Remote")],
        default="local",
        null=True,
        verbose_name=_("Netcat Active Side"),
    )
    repl_netcat_active_side_port_min = models.PositiveIntegerField(
        null=True,
        verbose_name=_("Netcat Active Side Min Port"),
    )
    repl_netcat_active_side_port_max = models.PositiveIntegerField(
        null=True,
        verbose_name=_("Netcat Active Side Max Port"),
    )
    repl_source_datasets = ListField(
        verbose_name=_("Source Datasets"),
    )
    repl_target_dataset = models.CharField(
        max_length=120,
        verbose_name=_("Target Dataset"),
        help_text=_(
            "This should be the name of the ZFS filesystem on "
            "remote side. eg: Volumename/Datasetname not the mountpoint or "
            "filesystem path"),
    )
    repl_recursive = models.BooleanField(
        default=False,
        verbose_name=_("Recursively replicate child dataset's snapshots"),
    )
    repl_exclude = ListField(
        verbose_name=_("Exclude child datasets"),
    )
    repl_tasks = models.ManyToManyField(
        "Task",
        related_name="replication_tasks",
        verbose_name=_("Periodic snapshot tasks"),
    )
    repl_naming_schema = ListField(
        verbose_name=_("Also replicate snapshots matching naming schema"),
    )
    repl_auto = models.BooleanField(
        verbose_name=_("Run automatically")
    )
    repl_minute = models.CharField(
        max_length=100,
        default="00",
        verbose_name=_("Minute"),
        help_text=_(
            "Values allowed:"
            "<br>Slider: 0-30 (as it is every Nth minute)."
            "<br>Specific Minute: 0-59."),
    )
    repl_hour = models.CharField(
        max_length=100,
        default="*",
        verbose_name=_("Hour"),
        help_text=_("Values allowed:"
                    "<br>Slider: 0-12 (as it is every Nth hour)."
                    "<br>Specific Hour: 0-23."),
    )
    repl_daymonth = models.CharField(
        max_length=100,
        default="*",
        verbose_name=_("Day of month"),
        help_text=_("Values allowed:"
                    "<br>Slider: 0-15 (as its is every Nth day)."
                    "<br>Specific Day: 1-31."),
    )
    repl_month = models.CharField(
        max_length=100,
        default='*',
        verbose_name=_("Month"),
    )
    repl_dayweek = models.CharField(
        max_length=100,
        default="*",
        verbose_name=_("Day of week"),
    )
    repl_begin = models.TimeField(
        default=time(hour=0),
        verbose_name=_("Begin"),
        help_text=_("Do not start replication before"),
    )
    repl_end = models.TimeField(
        default=time(hour=23, minute=59),
        verbose_name=_("End"),
        help_text=_("Do not start replication after"),
    )
    repl_only_matching_schedule = models.BooleanField(
        verbose_name=_("Only replicate snapshots matching schedule"),
    )
    repl_allow_from_scratch = models.BooleanField(
        verbose_name=_("Replicate from scratch if incremental is not possible"),
    )
    repl_hold_pending_snapshots = models.BooleanField(
        verbose_name=_("Hold pending snapshots"),
    )
    repl_retention_policy = models.CharField(
        max_length=5,
        choices=[("SOURCE", "Same as source"), ("CUSTOM", "Custom"), ("NONE", "None")],
        default="none",
        verbose_name=_("Snapshot retention policy"),
    )
    repl_lifetime_value = models.PositiveIntegerField(
        null=True,
        default=2,
        verbose_name=_("Snapshot lifetime value"),
    )
    repl_lifetime_unit = models.CharField(
        null=True,
        default='WEEK',
        max_length=120,
        choices=choices.RetentionUnit_Choices,
        verbose_name=_("Snapshot lifetime unit"),
    )
    repl_compression = models.CharField(
        null=True,
        max_length=5,
        choices=choices.Repl_CompressionChoices,
        default="LZ4",
        verbose_name=_("Stream Compression"),
    )
    repl_speed_limit = models.IntegerField(
        null=True,
        default=None,
        verbose_name=_("Limit (kbps)"),
        help_text=_(
            "Limit the replication speed. Unit in "
            "kilobits/second. 0 = unlimited."),
    )
    repl_dedup = models.BooleanField(
        default=False,
        verbose_name=_('Send deduplicated stream'),
        help_text=_(
            'Blocks	which would have been '
            'sent multiple times in	the send stream	will only be sent '
            'once. The receiving system must also support this feature to '
            'receive a deduplicated	stream. This flag can be used regard-'
            'less of the dataset\'s dedup property, but performance will be '
            'much better if	the filesystem uses a dedup-capable checksum '
            '(eg. sha256).'
        ),
    )
    repl_large_block = models.BooleanField(
        default=True,
        verbose_name=_('Allow blocks larger than 128KB'),
        help_text=_(
            'Generate a stream which may contain blocks larger than	128KB. '
            'This flag has no effect if the	large_blocks pool feature is '
            'disabled, or if the recordsize	property of this filesystem '
            'has never been	set above 128KB. The receiving	system must '
            'have the large_blocks pool feature enabled as well. See '
            'zpool-features(7) for details on ZFS feature flags and	the '
            'large_blocks feature.'
        ),
    )
    repl_embed = models.BooleanField(
        default=True,
        verbose_name=_('Allow WRITE_EMBEDDED records'),
        help_text=_(
            'Generate a more compact stream by using WRITE_EMBEDDED '
            'records for blocks which are stored more compactly on disk by '
            'the embedded_data pool feature. This flag has no effect if '
            'the embedded_data feature is disabled. The receiving system '
            'must have the embedded_data feature enabled. If the '
            'lz4_compress feature is active on the sending system, then '
            'the receiving system must have that feature enabled as well. '
            'See zpool-features(7) for details on ZFS feature flags and '
            'the embedded_data feature.'
        ),
    )
    repl_compressed = models.BooleanField(
        default=True,
        verbose_name=_('Allow compressed WRITE records'),
        help_text=_(
            'Generate a more compact stream by using compressed WRITE '
            'records for blocks which are compressed on disk and in memory '
            '(see the compression property for details). If the '
            'lz4_compress feature is active on the sending system, then '
            'the receiving system must have that feature enabled as well. '
            'If the large_blocks feature is enabled on the sending system '
            'but the -L option is not supplied in conjunction with -c then '
            'the data will be decompressed before sending so it can be '
            'split into smaller block sizes. '
        ),
    )
    repl_retries = models.PositiveIntegerField(
        default=5,
        verbose_name=_("Number of retries for failed replications"),
    )
    repl_enabled = models.BooleanField(
        default=True,
        verbose_name=_("Enabled"),
        help_text=_(
            'Disabling will not stop any replications which are in progress.'
        ),
    )

    class Meta:
        verbose_name = _("Replication Task")
        verbose_name_plural = _("Replication Tasks")

    def __str__(self):
        return '%s -> %s:%s' % (
            self.repl_filesystem,
            self.repl_remote.ssh_remote_hostname,
            self.repl_zfs)

    @property
    def repl_lastresult(self):
        if not os.path.exists(REPL_RESULTFILE):
            return {'msg': 'Waiting'}
        with open(REPL_RESULTFILE, 'rb') as f:
            data = f.read()
        try:
            results = pickle.loads(data)
            return results[self.id]
        except:
            return {'msg': None}

    @property
    def status(self):
        progressfile = '/tmp/.repl_progress_%d' % self.id
        if os.path.exists(progressfile):
            with open(progressfile, 'r') as f:
                pid = int(f.read())
            title = notifier().get_proc_title(pid)
            if title:
                reg = re.search(r'sending (\S+) \((\d+)%', title)
                if reg:
                    return _('Sending %(snapshot)s (%(percent)s%%)') % {
                        'snapshot': reg.groups()[0],
                        'percent': reg.groups()[1],
                    }
                else:
                    return _('Sending')
        if self.repl_lastresult:
            return self.repl_lastresult['msg']


class Task(Model):
    task_dataset = models.CharField(
        max_length=150,
        verbose_name=_("Volume/Dataset"),
    )
    task_recursive = models.BooleanField(
        default=False,
        verbose_name=_("Recursive"),
    )
    task_exclude = ListField(
        verbose_name=_("Exclude child datasets"),
    )
    task_lifetime_value = models.PositiveIntegerField(
        default=2,
        verbose_name=_("Snapshot lifetime value"),
    )
    task_lifetime_unit = models.CharField(
        default='WEEK',
        max_length=120,
        choices=choices.RetentionUnit_Choices,
        verbose_name=_("Snapshot lifetime unit"),
    )
    task_naming_schema = models.CharField(
        default='auto-%Y-%m-%d_%H-%M',
        max_length=150,
        verbose_name=_("Naming schema"),
    )
    task_minute = models.CharField(
        max_length=100,
        default="00",
        verbose_name=_("Minute"),
        help_text=_(
            "Values allowed:"
            "<br>Slider: 0-30 (as it is every Nth minute)."
            "<br>Specific Minute: 0-59."),
    )
    task_hour = models.CharField(
        max_length=100,
        default="*",
        verbose_name=_("Hour"),
        help_text=_("Values allowed:"
                    "<br>Slider: 0-12 (as it is every Nth hour)."
                    "<br>Specific Hour: 0-23."),
    )
    task_daymonth = models.CharField(
        max_length=100,
        default="*",
        verbose_name=_("Day of month"),
        help_text=_("Values allowed:"
                    "<br>Slider: 0-15 (as its is every Nth day)."
                    "<br>Specific Day: 1-31."),
    )
    task_month = models.CharField(
        max_length=100,
        default='*',
        verbose_name=_("Month"),
    )
    task_dayweek = models.CharField(
        max_length=100,
        default="*",
        verbose_name=_("Day of week"),
    )
    task_begin = models.TimeField(
        default=time(hour=9),
        verbose_name=_("Begin"),
        help_text=_("Do not snapshot before"),
    )
    task_end = models.TimeField(
        default=time(hour=18),
        verbose_name=_("End"),
        help_text=_("Do not snapshot after"),
    )
    task_enabled = models.BooleanField(
        default=True,
        verbose_name=_("Enabled"),
    )

    def __str__(self):
        return '%s - %s - %d %s' % (
            self.task_dataset,
            self.task_naming_schema,
            self.task_lifetime_value,
            self.task_lifetime_unit.lower(),
        )

    class Meta:
        verbose_name = _("Periodic Snapshot Task")
        verbose_name_plural = _("Periodic Snapshot Tasks")
        ordering = ["task_dataset"]


class VMWarePlugin(Model):

    hostname = models.CharField(
        verbose_name=_('Hostname'),
        max_length=200,
    )
    username = models.CharField(
        verbose_name=_('Username'),
        max_length=200,
        help_text=_(
            'Username on the above VMware host with enough privileges to '
            'snapshot virtual machines.'
        ),
    )
    password = models.CharField(
        verbose_name=_('Password'),
        max_length=200,
    )
    filesystem = models.CharField(
        verbose_name=_('ZFS Filesystem'),
        max_length=200,
    )
    datastore = models.CharField(
        verbose_name=_('Datastore'),
        max_length=200,
        help_text=_(
            'The datastore on the VMware side that the filesystem corresponds '
            'to.'
        ),
    )

    class Meta:
        verbose_name = _('VMware-Snapshot')
        verbose_name_plural = _('VMware-Snapshots')

    def __str__(self):
        return '{}:{}'.format(self.hostname, self.datastore)

    def set_password(self, passwd):
        self.password = notifier().pwenc_encrypt(passwd)

    def get_password(self):
        return notifier().pwenc_decrypt(self.password)
