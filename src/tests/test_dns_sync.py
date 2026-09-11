"""The DNS sync CronJob: address selection, the private-address refusal, and write-on-mismatch.

The Kubernetes and Linode APIs are both served by one `httpx.MockTransport` that records
every request, so "no write" is asserted as the absence of a PUT rather than inferred.
"""

import json
import logging

import httpx
import pytest

from src.dns_sync.sync import DnsSyncError, external_ipv4, is_private, sync

NODE_IP = '172.104.140.180'  # the real node: in 172.104/16, outside 172.16/12
DOMAIN = 'rdnull.im'
RECORD = 'mcp.anchovy-bot'
DOMAIN_ID = 42
RECORD_ID = 7


def _node(*addresses: tuple[str, str], ready: bool = True) -> dict:
    return {
        'status': {
            'addresses': [{'type': kind, 'address': address} for kind, address in addresses],
            'conditions': [{'type': 'Ready', 'status': 'True' if ready else 'False'}],
        },
    }


def _real_node(external: str = NODE_IP) -> dict:
    # Order as the real node reports it, plus the internal address first to prove order
    # does not matter.
    return _node(
        ('InternalIP', '192.168.152.134'),
        ('Hostname', 'lke617801-905705-5b2741360000'),
        ('ExternalIP', external),
        ('ExternalIP', '2a01:7e01::2000:6ff:fe41:29a6'),
    )


class FakeApis:
    """Serves `/api/v1/nodes` and the Linode domain endpoints; records every request."""

    def __init__(self, nodes: list[dict], records: list[dict]):
        self.nodes = nodes
        self.records = records
        self.requests: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path == '/api/v1/nodes':
            return httpx.Response(200, json={'items': self.nodes})
        if path == '/v4/domains':
            return httpx.Response(200, json={'data': [{'id': DOMAIN_ID, 'domain': DOMAIN}]})
        if path == f'/v4/domains/{DOMAIN_ID}/records':
            return httpx.Response(200, json={'data': self.records, 'page': 1, 'pages': 1})
        if request.method == 'PUT' and path.startswith(f'/v4/domains/{DOMAIN_ID}/records/'):
            return httpx.Response(200, json=json.loads(request.content))
        return httpx.Response(404)

    def writes(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.method != 'GET']

    def clients(self) -> tuple[httpx.Client, httpx.Client]:
        transport = httpx.MockTransport(self.handle)
        return (
            httpx.Client(base_url='https://kube', transport=transport),
            httpx.Client(base_url='https://linode/v4', transport=transport),
        )


def _a_record(target: str, name: str = RECORD, record_id: int = RECORD_ID) -> dict:
    return {'id': record_id, 'type': 'A', 'name': name, 'target': target}


def test_external_ipv4_takes_the_external_address_not_the_first():
    assert external_ipv4([_real_node()]) == NODE_IP


def test_external_ipv4_ignores_an_ipv6_external_address():
    node = _node(('ExternalIP', '2a01:7e01::1'), ('ExternalIP', NODE_IP))

    assert external_ipv4([node]) == NODE_IP


def test_external_ipv4_refuses_a_node_with_only_an_internal_address():
    with pytest.raises(DnsSyncError):
        external_ipv4([_node(('InternalIP', '192.168.152.134'))])


def test_external_ipv4_skips_a_node_that_is_not_ready():
    draining = _real_node('198.51.100.9')
    draining['status']['conditions'][0]['status'] = 'False'

    assert external_ipv4([draining, _real_node()]) == NODE_IP


def test_external_ipv4_refuses_to_guess_between_two_ready_nodes():
    with pytest.raises(DnsSyncError):
        external_ipv4([_real_node(), _real_node('198.51.100.9')])


@pytest.mark.parametrize('ip', ['10.2.0.1', '172.16.0.1', '172.31.255.254', '192.168.152.134'])
def test_is_private_refuses_each_private_range(ip):
    assert is_private(ip)


@pytest.mark.parametrize('ip', [NODE_IP, '172.15.255.255', '172.32.0.1', '8.8.8.8'])
def test_is_private_accepts_public_addresses_including_other_172s(ip):
    assert not is_private(ip)


def test_sync_leaves_a_matching_record_alone(caplog):
    apis = FakeApis([_real_node()], [_a_record(NODE_IP)])

    with caplog.at_level(logging.INFO):
        action = sync(*apis.clients(), DOMAIN, RECORD)

    assert action == 'unchanged'
    assert apis.writes() == []
    assert (
        f'DNS_SYNC record={RECORD}.{DOMAIN} node_ip={NODE_IP} record_ip={NODE_IP} action=unchanged'
        in caplog.messages
    )


def test_sync_updates_a_mismatched_record_with_one_put(caplog):
    apis = FakeApis([_real_node()], [_a_record('203.0.113.1')])

    with caplog.at_level(logging.INFO):
        action = sync(*apis.clients(), DOMAIN, RECORD)

    writes = apis.writes()
    assert action == 'updated'
    assert len(writes) == 1
    assert writes[0].method == 'PUT'
    assert writes[0].url.path == f'/v4/domains/{DOMAIN_ID}/records/{RECORD_ID}'
    assert json.loads(writes[0].content) == {'target': NODE_IP}
    assert (
        f'DNS_SYNC record={RECORD}.{DOMAIN} node_ip={NODE_IP} record_ip=203.0.113.1 action=updated'
        in caplog.messages
    )


def test_sync_touches_only_the_named_record():
    other = _a_record('203.0.113.50', name='webhook.anchovy-bot', record_id=99)
    apis = FakeApis([_real_node()], [other, _a_record('203.0.113.1')])

    sync(*apis.clients(), DOMAIN, RECORD)

    assert [r.url.path for r in apis.writes()] == [f'/v4/domains/{DOMAIN_ID}/records/{RECORD_ID}']


def test_sync_refuses_a_private_address_and_logs_both_values(caplog):
    apis = FakeApis([_real_node('10.0.0.5')], [_a_record(NODE_IP)])

    with caplog.at_level(logging.INFO), pytest.raises(DnsSyncError):
        sync(*apis.clients(), DOMAIN, RECORD)

    assert apis.writes() == []
    assert (
        f'DNS_SYNC record={RECORD}.{DOMAIN} node_ip=10.0.0.5 record_ip={NODE_IP} action=refused'
        in caplog.messages
    )


def test_sync_refuses_when_the_record_does_not_exist():
    apis = FakeApis([_real_node()], [_a_record(NODE_IP, name='something-else')])

    with pytest.raises(DnsSyncError):
        sync(*apis.clients(), DOMAIN, RECORD)

    assert apis.writes() == []
