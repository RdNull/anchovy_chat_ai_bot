"""Keeps the ingress host's A record pointed at the node's public IPv4.

A node recycle replaces the node, and the new one gets a new IP; the record would then
resolve fine and point nowhere. This runs from a CronJob every ten minutes: read the node's
address, compare it with the record through the Linode API, and write only on a mismatch.
It is insurance for the unattended case, not a load-bearing component.

It has its own settings class rather than `src/settings.py`, which requires the Telegram
token and the Mongo URL — credentials this job must not hold.

Two guards, both from a real mistake. The address is selected by `type: ExternalIP` and
IPv4 explicitly, because the first entry of `.status.addresses` is the internal one, and a
record pointed at it fails silently. And a private address is refused outright rather than
written, whatever its source.
"""

import ipaddress
import ssl
from pathlib import Path
from typing import Any

import httpx
from pydantic_settings import BaseSettings, SettingsConfigDict

from src.logs import logger

PRIVATE_NETWORKS = tuple(
    ipaddress.ip_network(cidr) for cidr in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')
)

_SERVICE_ACCOUNT = Path('/var/run/secrets/kubernetes.io/serviceaccount')
_TIMEOUT = 15


class DnsSyncSettings(BaseSettings):
    model_config = SettingsConfigDict(env_ignore_empty=True)

    # Scoped to Domains read/write and nothing else.
    LINODE_DNS_ACCESS_TOKEN: str
    DNS_SYNC_DOMAIN: str = 'rdnull.im'
    DNS_SYNC_RECORD: str = 'mcp.anchovy-bot'
    LINODE_API_URL: str = 'https://api.linode.com/v4'
    KUBE_API_URL: str = 'https://kubernetes.default.svc'


class DnsSyncError(Exception):
    """A state this job refuses to act on. Exits non-zero, so the Job shows as failed."""


def is_private(ip: str) -> bool:
    address = ipaddress.ip_address(ip)
    return any(address in network for network in PRIVATE_NETWORKS)


def external_ipv4(nodes: list[dict[str, Any]]) -> str:
    """The public IPv4 of the single Ready node.

    More than one Ready node happens briefly during a recycle, while the replacement joins
    and the old node drains. Guessing then could point the record at the node about to be
    deleted, so the run fails instead and the next one, ten minutes later, gets it right.
    """
    ready = [node for node in nodes if _is_ready(node)]
    if len(ready) != 1:
        raise DnsSyncError(f'expected exactly one Ready node, found {len(ready)}')

    addresses = [
        entry['address']
        for entry in ready[0].get('status', {}).get('addresses', [])
        if entry.get('type') == 'ExternalIP' and _is_ipv4(entry.get('address', ''))
    ]
    if len(addresses) != 1:
        raise DnsSyncError(f'expected exactly one IPv4 ExternalIP, found {addresses}')
    return addresses[0]


def sync(kube: httpx.Client, linode: httpx.Client, domain: str, record_name: str) -> str:
    """One comparison, and at most one write. Returns the action taken."""
    nodes = kube.get('/api/v1/nodes').raise_for_status().json()['items']
    node_ip = external_ipv4(nodes)

    domain_id = _domain_id(linode, domain)
    record = _a_record(linode, domain_id, record_name)
    record_ip = record['target']
    fqdn = f'{record_name}.{domain}'

    if is_private(node_ip):
        logger.error(
            'DNS_SYNC record=%s node_ip=%s record_ip=%s action=refused', fqdn, node_ip, record_ip,
        )
        raise DnsSyncError(f'refusing to point {fqdn} at private address {node_ip}')

    action = 'unchanged'
    if node_ip != record_ip:
        linode.put(
            f'/domains/{domain_id}/records/{record["id"]}', json={'target': node_ip},
        ).raise_for_status()
        action = 'updated'

    logger.info(
        'DNS_SYNC record=%s node_ip=%s record_ip=%s action=%s', fqdn, node_ip, record_ip, action,
    )
    return action


def main() -> int:
    settings = DnsSyncSettings()
    kube = httpx.Client(
        base_url=settings.KUBE_API_URL,
        headers={'Authorization': f'Bearer {(_SERVICE_ACCOUNT / "token").read_text().strip()}'},
        verify=ssl.create_default_context(cafile=str(_SERVICE_ACCOUNT / 'ca.crt')),
        timeout=_TIMEOUT,
    )
    linode = httpx.Client(
        base_url=settings.LINODE_API_URL,
        headers={'Authorization': f'Bearer {settings.LINODE_DNS_ACCESS_TOKEN}'},
        timeout=_TIMEOUT,
    )
    with kube, linode:
        try:
            sync(kube, linode, settings.DNS_SYNC_DOMAIN, settings.DNS_SYNC_RECORD)
        except DnsSyncError as exc:
            logger.error('DNS_SYNC_FAILED %s', exc)
            return 1
    return 0


def _is_ready(node: dict[str, Any]) -> bool:
    conditions = node.get('status', {}).get('conditions', [])
    return any(c.get('type') == 'Ready' and c.get('status') == 'True' for c in conditions)


def _is_ipv4(address: str) -> bool:
    try:
        return ipaddress.ip_address(address).version == 4
    except ValueError:
        return False


def _domain_id(linode: httpx.Client, domain: str) -> int:
    response = linode.get('/domains', headers={'X-Filter': f'{{"domain": "{domain}"}}'})
    matches = [d for d in response.raise_for_status().json()['data'] if d['domain'] == domain]
    if len(matches) != 1:
        raise DnsSyncError(f'expected exactly one Linode domain {domain}, found {len(matches)}')
    return matches[0]['id']


def _a_record(linode: httpx.Client, domain_id: int, name: str) -> dict[str, Any]:
    """The one A record named `name`. Every other record is left alone, and none is deleted."""
    page = linode.get(
        f'/domains/{domain_id}/records', params={'page_size': 500},
    ).raise_for_status().json()
    if page.get('pages', 1) > 1:
        raise DnsSyncError(f'domain {domain_id} has more than one page of records')

    matches = [r for r in page['data'] if r['type'] == 'A' and r['name'] == name]
    if len(matches) != 1:
        raise DnsSyncError(f'expected exactly one A record named {name}, found {len(matches)}')
    return matches[0]
