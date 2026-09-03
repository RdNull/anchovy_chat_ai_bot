"""Deploy-config invariants.

Two mechanisms put values into the cluster and neither fails on a missing one.
`deploy-k8s.sh` renders `manifests/*.yaml` through `envsubst`, which substitutes the
**empty string** for anything the CI job never exported, and it writes `bot-secrets` with
`--from-literal=VAR="$VAR"`, which stores `""` just as willingly. Nothing errors — the
cluster simply receives a blank.

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
_FROM_LITERAL = re.compile(r'--from-literal=(\w+)=')
_GUARD = re.compile(r'^for v in ([\w\s]+); do', re.MULTILINE)

# Set by the `Deploy k8s` step's own `env:` block, not by the job-level one.
_STEP_LEVEL = frozenset({'IMAGE_TAG'})


def secret_keys() -> set[str]:
    """The keys `deploy-k8s.sh` writes into `bot-secrets`.

    The script is the source of truth for these — `manifests/secrets.yaml` is gone, since
    a password holding a quote, a backslash or a `$` cannot survive a YAML round trip.
    """
    return set(_FROM_LITERAL.findall(_SCRIPT.read_text()))


def required_vars() -> dict[str, set[str]]:
    """Every name the deploy substitutes into the cluster, keyed by where it is read."""
    sources = {
        path.name: names
        for path in sorted(_MANIFESTS.glob('*.yaml'))
        if (names := set(_PLACEHOLDER.findall(path.read_text())))
    }
    sources[_SCRIPT.name] = secret_keys()

    return sources


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

    assert not unguarded, f'written to bot-secrets but not guarded: {unguarded}'


def test_the_shell_guard_only_requires_what_the_job_exports():
    """A name the guard requires but the job never exports fails every deploy."""
    unexported = sorted(guarded_vars() - (deploy_job_env() | _STEP_LEVEL))

    assert not unexported, f'guarded by deploy-k8s.sh but never exported: {unexported}'


def test_the_invariant_is_not_vacuous():
    """A moved manifest or a renamed job would turn the assertions above green forever."""
    sources = required_vars()

    assert set(sources) >= {'configmap.yaml', 'deployment.yaml', 'deploy-k8s.sh'}
    assert 'MONGO_INITDB_ROOT_PASSWORD' in sources['deploy-k8s.sh']
    assert len(deploy_job_env()) > 1
    assert guarded_vars()
