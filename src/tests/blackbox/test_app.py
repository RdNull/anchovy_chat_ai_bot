"""The blackbox HTTP surface: bearer auth, probes, and the refusal to start without a token.

`TestClient` is used as a context manager so the MCP app's lifespan runs; without it the
session manager's task group never starts and every MCP request fails for that reason
instead of the one under test.
"""

import logging

import pytest
from starlette.testclient import TestClient

from src import mongo
from src.blackbox import app as blackbox_app
from src.blackbox.app import BearerAuth, build_app
from src.embeddings.messages import messages_embeddings_client

TOKEN = 'test-token-0123456789abcdef'

TOOLS = {
    'find_windows', 'get_window', 'list_messages', 'list_snapshots',
    'get_memory', 'diff_memory', 'get_user_facts',
}

_MCP_HEADERS = {
    'Accept': 'application/json, text/event-stream',
    'Content-Type': 'application/json',
}

_INITIALIZE = {
    'jsonrpc': '2.0',
    'id': 1,
    'method': 'initialize',
    'params': {
        'protocolVersion': '2025-06-18',
        'capabilities': {},
        'clientInfo': {'name': 'test', 'version': '0'},
    },
}


def _authorized(token: str = TOKEN) -> dict[str, str]:
    return {**_MCP_HEADERS, 'Authorization': f'Bearer {token}'}


@pytest.fixture
def client():
    with TestClient(build_app(TOKEN)) as test_client:
        yield test_client


@pytest.mark.parametrize('token', [None, '', '   '])
def test_build_app_refuses_a_missing_or_blank_token(token):
    with pytest.raises(ValueError) as error:
        build_app(token)

    assert 'BLACKBOX_MCP_ACCESS_TOKEN' in str(error.value)


def test_request_without_a_token_is_rejected(client):
    response = client.post('/', json=_INITIALIZE, headers=_MCP_HEADERS)

    assert response.status_code == 401
    assert response.headers['www-authenticate'] == 'Bearer'
    assert response.content == b''


def test_request_with_a_wrong_token_is_rejected(client):
    response = client.post('/', json=_INITIALIZE, headers=_authorized('wrong-token'))

    assert response.status_code == 401
    assert response.headers['www-authenticate'] == 'Bearer'
    assert response.content == b''


def test_the_token_under_another_scheme_is_rejected(client):
    headers = {**_MCP_HEADERS, 'Authorization': f'Basic {TOKEN}'}

    response = client.post('/', json=_INITIALIZE, headers=headers)

    assert response.status_code == 401


def test_the_bearer_scheme_is_matched_case_insensitively(client):
    headers = {**_MCP_HEADERS, 'Authorization': f'bearer {TOKEN}'}

    response = client.post('/', json=_INITIALIZE, headers=headers)

    assert response.status_code == 200


def test_a_correct_token_initializes_and_lists_every_tool(client):
    initialized = client.post('/', json=_INITIALIZE, headers=_authorized())
    listed = client.post(
        '/',
        json={'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list', 'params': {}},
        headers=_authorized(),
    )

    assert initialized.status_code == 200
    assert initialized.json()['result']['serverInfo']['name'] == 'blackbox'
    assert listed.status_code == 200
    assert {tool['name'] for tool in listed.json()['result']['tools']} == TOOLS


def test_healthz_needs_no_token_and_returns_no_data(client):
    response = client.get('/healthz')

    assert response.status_code == 200
    assert response.content == b''


def test_readyz_is_ready_when_both_stores_answer(client, mocker):
    mocker.patch.object(mongo.db, 'command', return_value={'ok': 1})
    mocker.patch.object(messages_embeddings_client.qdrant_client, 'get_collections')

    response = client.get('/readyz')

    assert response.status_code == 200
    assert response.content == b''


def test_readyz_is_not_ready_when_a_store_fails(client, mocker):
    mocker.patch.object(mongo.db, 'command', return_value={'ok': 1})
    mocker.patch.object(
        messages_embeddings_client.qdrant_client,
        'get_collections',
        side_effect=ConnectionError('qdrant unreachable'),
    )

    response = client.get('/readyz')

    assert response.status_code == 503
    assert response.content == b''


def test_readyz_is_not_ready_when_a_store_hangs(client, mocker):
    async def hang(*_args, **_kwargs):
        await __import__('asyncio').sleep(60)

    mocker.patch.object(blackbox_app, '_READY_TIMEOUT', 0.01)
    mocker.patch.object(mongo.db, 'command', side_effect=hang)

    response = client.get('/readyz')

    assert response.status_code == 503


async def test_an_unauthenticated_request_never_reaches_the_app_or_its_body():
    """The body is the JSON-RPC request: rejecting must not read it, let alone parse it."""
    app_calls = []
    receive_calls = []
    sent = []

    async def inner(scope, receive, send):
        app_calls.append(scope)

    async def receive():
        receive_calls.append(True)
        return {'type': 'http.request', 'body': b'{}', 'more_body': False}

    async def send(message):
        sent.append(message)

    scope = {
        'type': 'http',
        'method': 'POST',
        'path': '/',
        'headers': [(b'authorization', b'Bearer wrong-token')],
    }

    await BearerAuth(inner, TOKEN)(scope, receive, send)

    assert app_calls == []
    assert receive_calls == []
    assert sent[0]['status'] == 401


def test_logs_carry_neither_the_token_nor_the_body(client, caplog):
    body_marker = 'body-marker-7c1e'
    request = {**_INITIALIZE, 'params': {**_INITIALIZE['params'], 'clientInfo': {
        'name': body_marker, 'version': '0',
    }}}

    with caplog.at_level(logging.DEBUG):
        client.post('/', json=request, headers=_authorized())
        client.post('/', json=request, headers=_authorized('wrong-token'))

    lines = [record.getMessage() for record in caplog.records]
    http_lines = [line for line in lines if line.startswith('BLACKBOX_HTTP ')]
    assert http_lines == [
        line for line in http_lines if 'method=POST path=/ status=' in line
    ]
    assert len(http_lines) == 2
    assert not [line for line in lines if TOKEN in line or 'wrong-token' in line]
    assert not [line for line in lines if body_marker in line]
