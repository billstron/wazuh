# Copyright (C) 2015-2019, Wazuh Inc.
# Created by Wazuh, Inc. <info@wazuh.com>.
# This program is free software; you can redistribute it and/or modify it under the terms of GPLv2

import json
import os
import re
import socket
import sys

from jq import jq
from jsonschema import validate

_data_path = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'data')

WAZUH_PATH = os.path.join('/', 'var', 'ossec')
ALERTS_FILE_PATH = os.path.join(WAZUH_PATH, 'logs', 'alerts', 'alerts.json')
WAZUH_CONF_PATH = os.path.join(WAZUH_PATH, 'etc', 'ossec.conf')
LOG_FILE_PATH = os.path.join(WAZUH_PATH, 'logs', 'ossec.log')

FIFO = 'fifo'
SYSLINK = 'sys_link'
SOCKET = 'socket'
REGULAR = 'regular'

CHECK_ALL = 'check_all'
CHECK_SUM = 'check_sum'
CHECK_SHA1SUM = 'check_sha1sum'
CHECK_MD5SUM = 'check_md5sum'
CHECK_SHA256SUM = 'check_sha256sum'
CHECK_SIZE = 'check_size'
CHECK_OWNER = 'check_owner'
CHECK_GROUP = 'check_group'
CHECK_PERM = 'check_perm'
CHECK_ATTRS = 'check_attrs'
CHECK_MTIME = 'check_mtime'
CHECK_INODE = 'check_inode'

REQUIRED_ATTRIBUTES = {
    CHECK_SHA1SUM: 'hash_sha1',
    CHECK_MD5SUM: 'hash_md5',
    CHECK_SHA256SUM: 'hash_sha256',
    CHECK_SIZE: 'size',
    CHECK_OWNER: ['uid', 'user_name'],
    CHECK_GROUP: ['gid', 'group_name'],
    CHECK_PERM: 'perm',
    CHECK_ATTRS: 'win_attributes',
    CHECK_MTIME: 'mtime',
    CHECK_INODE: 'inode',
    CHECK_ALL: {CHECK_SHA256SUM, CHECK_SHA1SUM, CHECK_MD5SUM, CHECK_SIZE, CHECK_OWNER,
                CHECK_GROUP, CHECK_PERM, CHECK_MTIME, CHECK_INODE},
    CHECK_SUM: {CHECK_SHA1SUM, CHECK_SHA256SUM, CHECK_MD5SUM}
}

_REQUIRED_AUDIT = {
    'user_id',
    'user_name',
    'group_id',
    'group_name',
    'process_name',
    'path',
    'audit_uid',
    'audit_name',
    'effective_uid',
    'effective_name',
    'ppid',
    'process_id'  # Only in windows, TODO parametrization
}

_last_log_line = 0


def load_fim_alerts(n_last=0):
    with open(ALERTS_FILE_PATH, 'r') as f:
        alerts = f.read()
    return list(filter(lambda x: x is not None, jq('.syscheck').transform(text=alerts, multiple_output=True)))[-n_last:]


def validate_event(event, checks=None):
    """ Checks if event is properly formatted according to some checkers.
        If a checker value is "yes", it must appear in the log.
        Else, it must not appear in the log.

        Checkers keys must be the exact name of the attribute.
        Example: For check_sum="yes" you put "checksum":"yes" in checkers.

    :param checkers: Dict of checkers
    :type checkers: Dict
    :param event: Parsed JSON log.
    :type event: JSON
    :return: None
    """
    def get_required_attributes(check_attributes, result=None):
        result = set() if result is None else result
        for check in check_attributes:
            mapped = REQUIRED_ATTRIBUTES[check]
            if isinstance(mapped, str):
                result |= {mapped}
            elif isinstance(mapped, list):
                result |= set(mapped)
            elif isinstance(mapped, set):
                result |= get_required_attributes(mapped, result=result)
        return result

    checks = {CHECK_ALL} if checks is None else checks
    with open(os.path.join(_data_path, 'syscheck_event.json'), 'r') as f:
        schema = json.load(f)
    validate(schema=schema, instance=event)

    # Check attributes
    attributes = event['data']['attributes'].keys() - {'type', 'checksum'}
    required_attributes = get_required_attributes(checks)
    print(f'attributes: {attributes}')
    print(f'required_attributes: {required_attributes}')
    assert(attributes ^ required_attributes == set())

    # Check audit
    print(f"audit: {event['data']['audit'].keys()}")
    if event['data']['mode'] == 'whodata':
        assert('audit' in event['data'])
        assert(event['data']['audit'].keys() ^ _REQUIRED_AUDIT == set())


def is_fim_scan_ended():
    message = 'File integrity monitoring scan ended.'
    line_number = 0
    with open(LOG_FILE_PATH, 'r') as f:
        for line in f:
            line_number += 1
            if line_number > _last_log_line:  # Ignore if has not reached from_line
                if message in line:
                    globals()['_last_log_line'] = line_number
                    return line_number
    return -1


def create_file(type, name, path, content=''):
    """ Creates a file in a given path.

    :param type: Defined constant that specifies the type. It can be: FIFO, SYSLINK, SOCKET or REGULAR
    :type type: Constant string
    :param name: File name
    :type name: String
    :param path: Path where the file will be created
    :type path: String
    :param content: Content of the file. Used for regular files.
    :type content: String or binary
    :return: None
    """
    getattr(sys.modules[__name__], f'_create_{type}')(path, name, content)


def _create_fifo(path, name, content):
    """ Creates a FIFO file.

    :param path: Path where the file will be created
    :type path: String
    :param name: File name
    :type name: String
    :param content: Content of the created file
    :type content: String or binary
    :return: None
    """
    fifo_path = os.path.join(path, name)
    try:
        os.mkfifo(fifo_path)
    except OSError:
        raise


def _create_sys_link(path, name, content):
    """ Creates a SysLink file.

    :param path: Path where the file will be created
    :type path: String
    :param name: File name
    :type name: String
    :param content: Content of the created file
    :type content: String or binary
    :return: None
    """
    syslink_path = os.path.join(path, name)
    try:
        os.symlink(syslink_path, syslink_path)
    except OSError:
        raise


def _create_socket(path, name, content):
    """ Creates a Socket file.

    :param path: Path where the file will be created
    :type path: String
    :param name: File name
    :type name: String
    :param content: Content of the created file
    :type content: String or binary
    :return: None
    """
    socket_path = os.path.join(path, name)
    try:
        os.unlink(socket_path)
    except OSError:
        if os.path.exists(socket_path):
            raise
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.bind(socket_path)


def _create_regular(path, name, content):
    """ Creates a Regular file.

    :param path: Path where the file will be created
    :type path: String
    :param name: File name
    :type name: String
    :param content: Content of the created file
    :type content: String or binary
    :return: None
    """
    regular_path = os.path.join(path, name)
    # Check if content is binary so it changes the mode
    isBinary = re.compile('^b\'.*\'$')
    if isBinary.match(str(content)):
        mode = 'wb'
    else:
        mode = 'w'
    with open(regular_path, mode) as f:
        f.write(content)


def delete_file(path, name):
    """ Deletes regular file """
    regular_path = os.path.join(path, name)
    if os.path.exists(regular_path):
        os.remove(regular_path)


def modify_file(path, name, content):
    """ Modify a Regular file.

    :param path: Path where the file will be created
    :type path: String
    :param name: File name
    :type name: String
    :param content: Content of the created file
    :type content: String or binary
    :return: None
    """
    regular_path = os.path.join(path, name)
    # Check if content is binary so it changes the mode
    isBinary = re.compile('^b\'.*\'$')
    if isBinary.match(str(content)):
        mode = 'ab'
    else:
        mode = 'a'
    with open(regular_path, mode) as f:
        f.write(content)


def change_internal_options(opt_path, pattern, value):
    """ Changes the value of a given parameter

    :param opt_path: File path
    :type opt_path: String
    :param pattern: Parameter to change
    :type pattern: String
    :param value: New value
    :type value: String
    """
    add_pattern = True
    with open(opt_path, "r") as sources:
        lines = sources.readlines()

    with open(opt_path, "w") as sources:
        for line in lines:
            sources.write(re.sub(f'{pattern}=[0-9]*', f'{pattern}={value}', line))
            if pattern in line:
                add_pattern = False

    if add_pattern:
        with open(opt_path, "a") as sources:
            sources.write(f'\n\n{pattern}={value}')


def callback_detect_end_scan(line):
    if 'File integrity monitoring scan ended.' in line:
        return line
    return None


def callback_detect_event(line):
    match = re.match(r'.*Sending event: (.+)$', line)
    if match:
        return json.loads(match.group(1))
    return None


def callback_audit_health_check(line):
    if 'Whodata health-check: Success.' in line:
        return True
    return None


def callback_audit_added_rule(line):
    match = re.match(r'.*Added audit rule for monitoring directory: \'(.+)\'', line)
    if match:
        return match.group(1)
    return None


def callback_audit_rules_manipulation(line):
    if 'Detected Audit rules manipulation' in line:
        return True
    return None


def callback_audit_connection(line):
    if '(6030): Audit: connected' in line:
        return True
    return None


def callback_audit_loaded_rule(line):
    match = re.match(r'.*Audit rule loaded: -w (.+) -p', line)
    if match:
        return match.group(1)
    return None


def callback_realtime_added_directory(line):
    match = re.match(r'.*Directory added for real time monitoring: \'(.+)\'', line)
    if match:
        return match.group(1)
    return None