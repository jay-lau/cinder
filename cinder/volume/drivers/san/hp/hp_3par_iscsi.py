# vim: tabstop=4 shiftwidth=4 softtabstop=4
#
#    (c) Copyright 2012-2013 Hewlett-Packard Development Company, L.P.
#    All Rights Reserved.
#
#    Copyright 2012 OpenStack LLC
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.
#
"""
Volume driver for HP 3PAR Storage array. This driver requires 3.1.2 firmware
on the 3PAR array.

You will need to install the python hp3parclient.
sudo pip install hp3parclient

Set the following in the cinder.conf file to enable the
3PAR iSCSI Driver along with the required flags:

volume_driver=cinder.volume.drivers.san.hp.hp_3par_iscsi.HP3PARISCSIDriver
"""

from hp3parclient import exceptions as hpexceptions

from cinder import exception
from cinder.openstack.common import log as logging
from cinder import utils
import cinder.volume.driver
from cinder.volume.drivers.san.hp import hp_3par_common as hpcommon
from cinder.volume.drivers.san import san

VERSION = 1.0
LOG = logging.getLogger(__name__)


class HP3PARISCSIDriver(cinder.volume.driver.ISCSIDriver):
    """OpenStack iSCSI driver to enable 3PAR storage array.

    Version history:
        1.0 - Initial driver

    """
    def __init__(self, *args, **kwargs):
        super(HP3PARISCSIDriver, self).__init__(*args, **kwargs)
        self.common = None
        self.configuration.append_config_values(hpcommon.hp3par_opts)
        self.configuration.append_config_values(san.san_opts)

    def _init_common(self):
        return hpcommon.HP3PARCommon(self.configuration)

    def _check_flags(self):
        """Sanity check to ensure we have required options set."""
        required_flags = ['hp3par_api_url', 'hp3par_username',
                          'hp3par_password', 'iscsi_ip_address',
                          'iscsi_port', 'san_ip', 'san_login',
                          'san_password']
        self.common.check_flags(self.configuration, required_flags)

    @utils.synchronized('3par', external=True)
    def get_volume_stats(self, refresh):
        self.common.client_login()
        stats = self.common.get_volume_stats(refresh)
        stats['storage_protocol'] = 'iSCSI'
        backend_name = self.configuration.safe_get('volume_backend_name')
        stats['volume_backend_name'] = backend_name or self.__class__.__name__
        self.common.client_logout()
        return stats

    def do_setup(self, context):
        self.common = self._init_common()
        self._check_flags()
        self.common.do_setup(context)

        # make sure ssh works.
        self._iscsi_discover_target_iqn(self.configuration.iscsi_ip_address)

    def check_for_setup_error(self):
        """Returns an error if prerequisites aren't met."""
        self._check_flags()

    @utils.synchronized('3par', external=True)
    def create_volume(self, volume):
        self.common.client_login()
        metadata = self.common.create_volume(volume)
        self.common.client_logout()

        return {'provider_location': "%s:%s" %
                (self.configuration.iscsi_ip_address,
                 self.configuration.iscsi_port),
                'metadata': metadata}

    @utils.synchronized('3par', external=True)
    def create_cloned_volume(self, volume, src_vref):
        """Clone an existing volume."""
        self.common.client_login()
        new_vol = self.common.create_cloned_volume(volume, src_vref)
        self.common.client_logout()

        return {'provider_location': "%s:%s" %
                (self.configuration.iscsi_ip_address,
                 self.configuration.iscsi_port),
                'metadata': new_vol}

    @utils.synchronized('3par', external=True)
    def delete_volume(self, volume):
        self.common.client_login()
        self.common.delete_volume(volume)
        self.common.client_logout()

    @utils.synchronized('3par', external=True)
    def create_volume_from_snapshot(self, volume, snapshot):
        """
        Creates a volume from a snapshot.

        TODO: support using the size from the user.
        """
        self.common.client_login()
        self.common.create_volume_from_snapshot(volume, snapshot)
        self.common.client_logout()

    @utils.synchronized('3par', external=True)
    def create_snapshot(self, snapshot):
        self.common.client_login()
        self.common.create_snapshot(snapshot)
        self.common.client_logout()

    @utils.synchronized('3par', external=True)
    def delete_snapshot(self, snapshot):
        self.common.client_login()
        self.common.delete_snapshot(snapshot)
        self.common.client_logout()

    @utils.synchronized('3par', external=True)
    def initialize_connection(self, volume, connector):
        """Assigns the volume to a server.

        Assign any created volume to a compute node/host so that it can be
        used from that host.

        This driver returns a driver_volume_type of 'iscsi'.
        The format of the driver data is defined in _get_iscsi_properties.
        Example return value:

            {
                'driver_volume_type': 'iscsi'
                'data': {
                    'target_discovered': True,
                    'target_iqn': 'iqn.2010-10.org.openstack:volume-00000001',
                    'target_protal': '127.0.0.1:3260',
                    'volume_id': 1,
                }
            }

        Steps to export a volume on 3PAR
          * Get the 3PAR iSCSI iqn
          * Create a host on the 3par
          * create vlun on the 3par
        """
        self.common.client_login()
        # get the target_iqn on the 3par interface.
        target_iqn = self._iscsi_discover_target_iqn(
            self.configuration.iscsi_ip_address)

        # we have to make sure we have a host
        host = self._create_host(volume, connector)

        # now that we have a host, create the VLUN
        vlun = self.common.create_vlun(volume, host)

        self.common.client_logout()
        info = {'driver_volume_type': 'iscsi',
                'data': {'target_portal': "%s:%s" %
                         (self.configuration.iscsi_ip_address,
                          self.configuration.iscsi_port),
                         'target_iqn': target_iqn,
                         'target_lun': vlun['lun'],
                         'target_discovered': True
                         }
                }
        return info

    @utils.synchronized('3par', external=True)
    def terminate_connection(self, volume, connector, **kwargs):
        """Driver entry point to unattach a volume from an instance."""
        self.common.client_login()
        self.common.terminate_connection(volume,
                                         connector['host'],
                                         connector['initiator'])
        self.common.client_logout()

    def _iscsi_discover_target_iqn(self, remote_ip):
        result = self.common._cli_run('showport -ids', None)

        iqn = None
        if result:
            # first line is header
            result = result[1:]
            for line in result:
                info = line.split(",")
                if info and len(info) > 2:
                    if info[1] == remote_ip:
                        iqn = info[2]

        return iqn

    def _create_3par_iscsi_host(self, hostname, iscsi_iqn, domain, persona_id):
        """Create a 3PAR host.

        Create a 3PAR host, if there is already a host on the 3par using
        the same iqn but with a different hostname, return the hostname
        used by 3PAR.
        """
        cmd = 'createhost -iscsi -persona %s -domain %s %s %s' % \
              (persona_id, domain, hostname, iscsi_iqn)
        out = self.common._cli_run(cmd, None)
        if out and len(out) > 1:
            return self.common.parse_create_host_error(hostname, out)
        return hostname

    def _modify_3par_iscsi_host(self, hostname, iscsi_iqn):
        # when using -add, you can not send the persona or domain options
        self.common._cli_run('createhost -iscsi -add %s %s'
                             % (hostname, iscsi_iqn), None)

    def _create_host(self, volume, connector):
        """Creates or modifies existing 3PAR host."""
        # make sure we don't have the host already
        host = None
        hostname = self.common._safe_hostname(connector['host'])
        try:
            host = self.common._get_3par_host(hostname)
            if not host['iSCSIPaths']:
                self._modify_3par_iscsi_host(hostname, connector['initiator'])
                host = self.common._get_3par_host(hostname)
        except hpexceptions.HTTPNotFound:
            # get persona from the volume type extra specs
            persona_id = self.common.get_persona_type(volume)
            # host doesn't exist, we have to create it
            hostname = self._create_3par_iscsi_host(hostname,
                                                    connector['initiator'],
                                                    self.configuration.
                                                    hp3par_domain,
                                                    persona_id)
            host = self.common._get_3par_host(hostname)

        return host

    @utils.synchronized('3par', external=True)
    def create_export(self, context, volume):
        pass

    @utils.synchronized('3par', external=True)
    def ensure_export(self, context, volume):
        pass

    @utils.synchronized('3par', external=True)
    def remove_export(self, context, volume):
        pass
