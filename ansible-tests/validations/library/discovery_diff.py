#!/usr/bin/env python

import json
import os
import six
import sys
from subprocess import Popen, PIPE

from ansible.module_utils.basic import *


DOCUMENTATION = '''
---
module: discovery_diff
short_description: Provide difference in hardware configuration
author: "Swapnil Kulkarni, @coolsvap"
'''


def get_node_hardware_data(hw_id, upenv):
    '''Read the inspector data about the given node from Swift'''
    p = Popen(('swift', 'download', '--output', '-', 'ironic-inspector', hw_id),
              env=upenv, stdout=PIPE, stderr=PIPE)
    if p.wait() == 0:
        return json.loads(p.stdout.read())


def all_equal(coll):
    if len(coll) <= 1:
        return True
    first = coll[0]
    for item in coll[1:]:
        if item != first:
            return False
    return True


def process_nested_dict(d, prefix=None):
    '''
    Turn a nested dictionary into a flat one.

    Example:
    inspector_data = {
        'memory_mb': 6000,
        'system': {
             'os': {
                 'version': 'CentOS Linux release 7.2.1511 (Core)',
             }
        },
        'network': {
             'eth0': {
                  'businfo': 'pci@0000:00:03.0'
             }
        }
    }

    >>> process_nested_dict(inspector_data)
    {
        'memory_mb': 6000,
        'system/os/version': 'CentOS Linux release 7.2.1511 (Core)',
        'network/eth0/businfo': 'pci@0000:00:03.0',
    }
    '''
    result = {}
    for k, v in six.iteritems(d):
        if prefix:
            new_key = prefix + '/' + k
        else:
            new_key = k
        if isinstance(v, dict):
            for k, v in six.iteritems(process_nested_dict(v, new_key)):
                result[k] = v
        else:
            result[new_key] = v
    return result


def process_nested_list(l):
    '''
    Turn a list of lists into a single key/value dict.

    Example:
    inspector_data = [
        ['memory_mb', 6000],
        ['system', 'os', 'version', 'CentOS Linux release 7.2.1511 (Core)'],
        ['network', 'eth0', 'businfo', 'pci@0000:00:03.0'],
    ]

    >>> process_nested_list(inspector_data)
    {
        'memory_mb': 6000,
        'system/os/version': 'CentOS Linux release 7.2.1511 (Core)',
        'network/eth0/businfo': 'pci@0000:00:03.0',
    }
    '''
    result = {}
    for item in l:
        key = '/'.join(item[:-1])
        value = item[-1]
        result[key] = value
    return result


def process_inspector_data(hw_item):
    '''
    Convert the raw ironic inspector data into something easier to work with.

    The inspector posts either a list of lists or a nested dictionary. We turn
    it to a flat dictionary with nested keys separated by a slash.
    '''
    if isinstance(hw_item, dict):
        return process_nested_dict(hw_item)
    elif isinstance(hw_item, list):
        return process_nested_list(hw_item)
    else:
        msg = "The hardware item '{}' must be either a dictionary or a list"
        raise Exception(msg.format(repr(hw_item)))


def main():
    module = AnsibleModule(
        argument_spec={
            'os_tenant_name': dict(required=True, type='str'),
            'os_username': dict(required=True, type='str'),
            'os_password': dict(required=True, type='str'),
        }
    )

    env = os.environ.copy()
    # NOTE(shadower): Undercloud OS_AUTH_URL should already be in Ansible's env
    env['OS_TENANT_NAME'] = module.params.get('os_tenant_name')
    env['OS_USERNAME'] = module.params.get('os_username')
    env['OS_PASSWORD'] = module.params.get('os_password')

    # TODO(shadower): use python-swiftclient here
    p = Popen(('swift', 'list', 'ironic-inspector'), env=env, stdout=PIPE, stderr=PIPE)
    if p.wait() != 0:
        msg = "Error running `swift list ironic-inspector`: {}".format(
            p.stderr.read())
        module.fail_json(msg=msg)

    hardware_ids = [i.strip() for i in p.stdout.read().splitlines() if i.strip()]
    inspector_data = [get_node_hardware_data(i, env) for i in hardware_ids]
    processed_data = [process_inspector_data(hw) for hw in inspector_data]

    all_keys = set()
    for hw in processed_data:
        all_keys.update(hw.keys())

    # TODO(shadower): checks for values that must be different (e.g. mac addresses)
    diffs = []

    for key in all_keys:
        values = [hw.get(key) for hw in processed_data]
        if not all_equal(values):
            msg = "The key '{}' has differing values: {}"
            diffs.append(msg.format(key, repr(values)))

    if diffs:
        msg = 'Found some differences between the introspected hardware.'
    else:
        msg = 'No differences found.'

    result = {
        'changed': True,
        'msg': msg,
        'warnings': diffs,
    }
    module.exit_json(**result)


if __name__ == '__main__':
    main()
