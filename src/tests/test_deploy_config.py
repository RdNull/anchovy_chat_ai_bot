"""Deploy-config invariants.

`deploy-k8s.sh` renders `manifests/*.yaml` through `envsubst`, which substitutes the
**empty string** for anything the CI job never exported. Nothing errors — the cluster
simply receives a blank.

`MONGO_INITDB_ROOT_PASSWORD` was blanked on every deploy for three months and surfaced only
when a node restart recreated the mongo pod against the by-then-empty secret. The configmap
half is quieter still: `settings.py` sets `env_ignore_empty=True`, so a blanked value falls
through to a code default and the bot runs on defaults with nothing in the logs.

These tests close the class. A value the deploy job does not export is a red test here, not
an incident three months from now.
"""
import re
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[2]
_WORKFLOW = _ROOT / '.github' / 'workflows' / 'deploy.yml'
_MANIFESTS = _ROOT / 'manifests'
_SCRIPT = _ROOT / 'deploy-k8s.sh'

_PLACEHOLDER = re.compile(r'\$\{(\w+)\}')
_GUARD = re.compile(r'^for v in ([\w\s]+); do', re.MULTILINE)

# Set by the `Deploy k8s` step's own `env:` block, not by the job-level one.
_STEP_LEVEL = frozenset({'IMAGE_TAG'})


def required_vars() -> dict[str, set[str]]:
    """Every `${VAR}` envsubst will substitute, keyed by path relative to `manifests/`.

    Recursive: `manifests/blackbox/` is rendered by the same script, and a blank there is
    the same bug in a different namespace.
    """
    return {
        str(path.relative_to(_MANIFESTS)): names
        for path in sorted(_MANIFESTS.rglob('*.yaml'))
        if (names := set(_PLACEHOLDER.findall(path.read_text())))
    }


def secret_keys() -> set[str]:
    """Every placeholder the deploy renders into a Secret, in any manifest.

    The Secret manifests are the source of truth: every kube object stays in `manifests/`,
    and the script does not grow a line per secret. Read per document, so a file holding a
    Secret beside a CronJob contributes only the Secret's values, not the image tag.
    """
    names = set()
    for path in _MANIFESTS.rglob('*.yaml'):
        for document in yaml.safe_load_all(path.read_text()):
            if document and document.get('kind') == 'Secret':
                names |= set(_PLACEHOLDER.findall(str(document.get('stringData', {}))))
    return names


def deploy_job_env() -> set[str]:
    """The names the `deploy` job exports to `deploy-k8s.sh`."""
    workflow = yaml.safe_load(_WORKFLOW.read_text())

    return set(workflow['jobs']['deploy']['env'])


def guarded_vars() -> set[str]:
    """The names `deploy-k8s.sh` refuses to deploy without."""
    return set(_GUARD.search(_SCRIPT.read_text()).group(1).split())


def test_every_required_var_is_exported_by_the_deploy_job():
    """The whole bug: an unexported name reaches the cluster as an empty value."""
    exported = deploy_job_env() | _STEP_LEVEL
    missing = {
        source: sorted(names - exported)
        for source, names in required_vars().items()
        if names - exported
    }

    assert not missing, f'the deploy will blank these — add them to jobs.deploy.env: {missing}'


def test_the_shell_guard_covers_every_secret_the_deploy_writes():
    """A secret outside the guard is this same bug one file over, and just as quiet."""
    unguarded = sorted(secret_keys() - guarded_vars())

    assert not unguarded, f'written to a Secret but not guarded: {unguarded}'


def test_the_shell_guard_only_requires_what_the_job_exports():
    """A name the guard requires but the job never exports fails every deploy."""
    unexported = sorted(guarded_vars() - (deploy_job_env() | _STEP_LEVEL))

    assert not unexported, f'guarded by deploy-k8s.sh but never exported: {unexported}'


def test_the_invariant_is_not_vacuous():
    """A moved manifest or a renamed job would turn the assertions above green forever."""
    sources = required_vars()

    assert set(sources) >= {
        'configmap.yaml', 'deployment.yaml', 'secrets.yaml',
        'blackbox/secrets.yaml', 'blackbox/deployment.yaml', 'dns-sync.yaml',
    }
    assert 'MONGO_INITDB_ROOT_PASSWORD' in sources['secrets.yaml']
    assert secret_keys() >= {
        'MONGO_INITDB_ROOT_PASSWORD', 'BLACKBOX_MCP_ACCESS_TOKEN', 'LINODE_DNS_ACCESS_TOKEN',
    }
    assert len(deploy_job_env()) > 1
    assert guarded_vars()


def _documents():
    for path in sorted(_MANIFESTS.rglob('*.yaml')):
        for document in yaml.safe_load_all(path.read_text()):
            if document:
                yield document


def _namespace(document: dict) -> str:
    return document['metadata'].get('namespace', 'default')


def _pod_spec(document: dict) -> dict | None:
    kind = document.get('kind')
    if kind in ('Deployment', 'StatefulSet'):
        return document['spec']['template']['spec']
    if kind == 'CronJob':
        return document['spec']['jobTemplate']['spec']['template']['spec']
    return None


def service_link_vars(service: dict) -> set[str]:
    """The env names the kubelet injects into every pod in the Service's namespace.

    Docker-links compatibility, on by default: `<NAME>_SERVICE_HOST`, `<NAME>_PORT`
    and a family of per-port names, with the Service name upper-cased and `-` → `_`.
    """
    name = service['metadata']['name'].upper().replace('-', '_')
    names = {f'{name}_SERVICE_HOST', f'{name}_SERVICE_PORT', f'{name}_PORT'}
    for port in service['spec'].get('ports', []):
        if 'name' in port:
            names.add(f'{name}_SERVICE_PORT_{port["name"].upper().replace("-", "_")}')
        base = f'{name}_PORT_{port["port"]}_{port.get("protocol", "TCP")}'
        names |= {base, f'{base}_PROTO', f'{base}_PORT', f'{base}_ADDR'}
    return names


def test_no_injected_service_variable_shadows_a_setting():
    """A Service named like a settings prefix overwrites that setting in every pod beside it.

    The first blackbox rollout crashed on exactly this: the `blackbox` Service injected
    `BLACKBOX_PORT=tcp://<ip>:80`, and settings validation refused it as a port. A pod
    running our image must either see no colliding Service or set `enableServiceLinks: false`.
    """
    from src.settings import _Settings

    fields = set(_Settings.model_fields)
    documents = list(_documents())
    injected: dict[str, set[str]] = {}
    for document in documents:
        if document.get('kind') == 'Service':
            injected.setdefault(_namespace(document), set()).update(service_link_vars(document))

    collisions = {}
    for document in documents:
        spec = _pod_spec(document)
        if not spec or spec.get('enableServiceLinks') is False:
            continue
        if not any(c.get('image') == '${IMAGE_TAG}' for c in spec['containers']):
            continue
        clash = injected.get(_namespace(document), set()) & fields
        if clash:
            collisions[document['metadata']['name']] = sorted(clash)

    assert 'BLACKBOX_PORT' in injected['blackbox']  # the check sees the real collision
    assert not collisions, f'injected Service env shadows settings — set enableServiceLinks: false: {collisions}'
