"""Deploy-config invariants.

`deploy-k8s.sh` renders `manifests/*.yaml` through `envsubst`, which substitutes the
**empty string** for anything the CI job never exported. Nothing fails at deploy time —
the cluster simply receives a blank value. `MONGO_INITDB_ROOT_PASSWORD` was blanked on
every deploy for three months and surfaced only when a node restart recreated the mongo
pod against the by-then-empty secret.

The configmap half is quieter still: `settings.py` sets `env_ignore_empty=True`, so a
blanked value falls through to a code default and the bot runs on defaults with nothing
in the logs.

These tests close the class. A placeholder the deploy job does not export is a red test
here, not an incident three months from now.
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


def manifest_placeholders() -> dict[str, set[str]]:
    """Every `${VAR}` envsubst will substitute, keyed by manifest file name."""
    return {
        path.name: names
        for path in sorted(_MANIFESTS.glob('*.yaml'))
        if (names := set(_PLACEHOLDER.findall(path.read_text())))
    }


def deploy_job_env() -> set[str]:
    """The names the `deploy` job exports to `deploy-k8s.sh`."""
    workflow = yaml.safe_load(_WORKFLOW.read_text())

    return set(workflow['jobs']['deploy']['env'])


def guarded_vars() -> set[str]:
    """The names `deploy-k8s.sh` refuses to deploy without."""
    return set(_GUARD.search(_SCRIPT.read_text()).group(1).split())


def test_every_manifest_placeholder_is_exported_by_the_deploy_job():
    """The whole bug: an unexported placeholder reaches the cluster as an empty string."""
    exported = deploy_job_env() | _STEP_LEVEL
    missing = {
        name: sorted(placeholders - exported)
        for name, placeholders in manifest_placeholders().items()
        if placeholders - exported
    }

    assert not missing, f'envsubst will blank these — add them to jobs.deploy.env: {missing}'


def test_the_shell_guard_only_requires_what_the_job_exports():
    """A name the guard requires but the job never exports fails every deploy."""
    unexported = sorted(guarded_vars() - (deploy_job_env() | _STEP_LEVEL))

    assert not unexported, f'guarded by deploy-k8s.sh but never exported: {unexported}'


def test_the_invariant_is_not_vacuous():
    """A moved manifest or a renamed job would turn the assertions above green forever."""
    placeholders = manifest_placeholders()

    assert set(placeholders) >= {'configmap.yaml', 'secrets.yaml', 'deployment.yaml'}
    assert 'MONGO_INITDB_ROOT_PASSWORD' in placeholders['secrets.yaml']
    assert len(deploy_job_env()) > 1
    assert guarded_vars()
