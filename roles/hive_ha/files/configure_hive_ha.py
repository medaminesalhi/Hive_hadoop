#!/usr/bin/env python3
"""Derive secure, separate Hive Metastore / HS2 configs from existing hive-site.xml.
No passwords printed; does not modify original hive-site.xml.
"""
import argparse
import os
import re
import stat
import tempfile
from pathlib import Path
from xml.etree import ElementTree as ET


def set_property(root, name, value):
    matches = [p for p in root.findall('property') if p.findtext('name') == name]
    if matches:
        prop = matches[0]
        for duplicate in matches[1:]:
            root.remove(duplicate)
    else:
        prop = ET.SubElement(root, 'property')
        ET.SubElement(prop, 'name').text = name
    node = prop.find('value')
    if node is None:
        node = ET.SubElement(prop, 'value')
    node.text = value


def get_property(root, name):
    for prop in root.findall('property'):
        if prop.findtext('name') == name:
            return prop.findtext('value', '')
    return ''


def main():
    ap = argparse.ArgumentParser()
    for arg in ['source', 'target', 'service', 'node-ip', 'db-endpoint',
                'zk-quorum', 'metastore-uris', 'namespace']:
        ap.add_argument('--' + arg, required=True)
    args = ap.parse_args()
    if args.service not in {'metastore', 'hs2'}:
        raise SystemExit('Invalid service')
    source = Path(args.source)
    target = Path(args.target)
    tree = ET.parse(source)
    root = tree.getroot()
    if root.tag != 'configuration':
        raise SystemExit('Invalid hive-site.xml: no configuration root')

    old_url = get_property(root, 'javax.jdo.option.ConnectionURL').strip()
    match = re.match(r'^(jdbc:mysql://)[^/]+(/[^\s]*)$', old_url)
    if not match:
        raise SystemExit('Expected an existing jdbc:mysql://... ConnectionURL')
    tail = match.group(2)
    path, sep, qs = tail.partition('?')
    if not path.startswith('/metastore_db'):
        raise SystemExit('Safety check: expected existing metastore_db JDBC schema')
    params = [p for p in qs.split('&') if p and not p.lower().startswith('createdatabaseifnotexist=')]
    jdbc = match.group(1) + args.db_endpoint + path
    if params:
        jdbc += '?' + '&'.join(params)

    if not get_property(root, 'javax.jdo.option.ConnectionUserName').strip():
        raise SystemExit('JDBC username missing')
    if not any(
    prop.findtext('name') == 'javax.jdo.option.ConnectionPassword'
    	for prop in root.findall('property')
    ):
    	raise SystemExit('JDBC password property missing')
    set_property(root, 'javax.jdo.option.ConnectionURL', jdbc)
    set_property(root, 'hive.metastore.schema.verification', 'true')

    if args.service == 'metastore':
        set_property(root, 'hive.metastore.uris', '')
        set_property(root, 'hive.metastore.service.discovery.mode', '')
        set_property(root, 'hive.metastore.thrift.bind.host', args.node_ip)
        set_property(root, 'hive.metastore.port', '9083')
    else:
        set_property(root, 'hive.metastore.uris', args.metastore_uris)
        set_property(root, 'hive.metastore.service.discovery.mode', '')
        set_property(root, 'hive.server2.support.dynamic.service.discovery', 'true')
        set_property(root, 'hive.zookeeper.quorum', args.zk_quorum)
        set_property(root, 'hive.server2.zookeeper.namespace', args.namespace)
        set_property(root, 'hive.server2.thrift.bind.host', args.node_ip)
        set_property(root, 'hive.server2.thrift.port', '10000')
        set_property(root, 'hive.server2.transport.mode', 'binary')

    if hasattr(ET, 'indent'):
        ET.indent(tree, space='  ')
    content = ET.tostring(root, encoding='utf-8', xml_declaration=True) + b'\n'
    if target.exists() and target.read_bytes() == content:
        print('UNCHANGED')
        return

    target.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.hive-site-', suffix='.xml', dir=target.parent)
    try:
        with os.fdopen(fd, 'wb') as outf:
            outf.write(content)
        os.chmod(name, stat.S_IRUSR | stat.S_IWUSR)
        os.replace(name, target)
    finally:
        if os.path.exists(name):
            os.unlink(name)
    print('UPDATED')


if __name__ == '__main__':
    main()
