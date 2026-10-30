"""One public management entry point; lifecycle ownership stays with each component."""
from agent_monitor import hub_config
import argparse
import os
from pathlib import Path
import subprocess
import sys

from agent_monitor.runtime import deployment_guard
from agent_monitor.runtime import local_web
from agent_monitor.runtime import process_identity
from agent_monitor.runtime import service_runtime
from agent_monitor.runtime.python_entrypoint import module_command

from agent_monitor.paths import PROJECT_ROOT

ROOT = PROJECT_ROOT


def run(*args, timeout=60):
    return deployment_guard._run([sys.executable, *map(str, args)], ROOT, timeout=timeout)


def local_status():
    owned = process_identity.read_owned(ROOT / 'state', ROOT)
    if owned is None:
        print('Local Web: not running (optional); no repair required.')
        return 0
    try:
        current = service_runtime.matches(local_web.health(ROOT), ROOT, service_runtime.signature(ROOT))
    except (OSError, ValueError):
        current = False
    print(f'Local Web: owned pid={owned[0]}; ' + (
        'local HTTP confirms target source/configuration.' if current else
        'target HTTP source/configuration is unverified; run service deploy or inspect state/server.log.'))
    print('  Browser interaction and collection freshness were not checked.')
    return 0 if current else 1


def status():
    # Separate checks continue after a component failure; status never installs.
    result = 0
    try:
        role = 'hub' if hub_config.is_hub() else ('client' if hub_config.enabled() else 'standalone')
        print(f'Role: {role}', flush=True)
        if role == 'client':
            print(f'Central dashboard: {hub_config.configuration()["url"]} (availability not checked).', flush=True)
        if role == 'hub':
            result |= run("-m", "agent_monitor.runtime.hub", 'status-web')
        else:
            result |= local_status()
    except (OSError, ValueError, subprocess.SubprocessError, process_identity.IdentityUnknown) as error:
        print(f'Web status unverified: {error}', file=sys.stderr)
        result = 1
    components = ['agent_monitor.runtime.rollup_service'] + (['agent_monitor.runtime.connector_service'] if sys.platform == 'darwin' else [])
    for component in components:
        try:
            result |= run('-m', component, 'status')
        except (OSError, ValueError, subprocess.SubprocessError):
            print(f'{component}: status check failed or timed out; this component remains unverified.', file=sys.stderr)
            result = 1
    if sys.platform != 'darwin':
        print('Connector: automatic startup is not managed on this platform.')
    print('Management: service restart COMPONENT; service deploy; service rebuild-runtime --python /absolute/python.')
    return 1 if result else 0


def component_status(component):
    """Retain the narrow observations previously exposed by status.sh."""
    if component == 'all':
        return status()
    if component == 'rollup-daemon':
        return run('-m', 'agent_monitor.runtime.rollup_service', 'status')
    if component == 'connector' and sys.platform != 'darwin':
        print('connector: no startup definition installed on this platform')
        return 0
    arguments = ['status'] if component == 'web' else ['connect', 'status']
    return deployment_guard._run(
        ['bash', str(ROOT / 'agent_monitor/runtime/cli.sh'), *arguments], ROOT, timeout=60)


def restart(component):
    from agent_monitor.runtime import hub
    with deployment_guard.mutation_guard(ROOT):
        if component == 'local-web':
            if hub_config.is_hub():
                raise ValueError('This checkout owns the Hub; use service restart hub.')
            with local_web.account_quiescence(ROOT):
                recipe = local_web.capture(ROOT)
                if recipe is None:
                    recipe = local_web.saved_recipe(ROOT)
                local_web.run_cli(ROOT, 'stop')
                local_web.restore(ROOT, recipe)
            print('Local Web restarted; target local HTTP identity confirmed.')
            return 0
        if component == 'hub':
            if not hub_config.is_hub():
                raise ValueError('This checkout is not the Hub; manage it on the Hub host.')
            with local_web.account_quiescence(ROOT):
                hub.stop_web()
                hub.install_web()
            return 0
        if component == 'connector':
            # Covers bounded launchctl inspection/unload/bootstrap and HTTP
            # readiness, including recovery when the job was not loaded.
            return run("-m", "agent_monitor.runtime.connector_service", 'restart', timeout=240)
        if sys.platform != 'darwin':
            raise ValueError('The collector schedule is managed only on macOS; exporters run on demand.')
        from agent_monitor.runtime import rollup_service
        managed = rollup_service.owned_definition(rollup_service.plist_path())
        info = rollup_service.loaded_info(managed)
        if not managed:
            raise ValueError('Collector schedule is not installed; use ./install.sh rollup-daemon to enable it.')
        if info and rollup_service.number(info.get('pid')):
            raise ValueError('Collection is running; wait for completion. No running collection was stopped.')
        if info is None:
            outcome = rollup_service.launch('bootstrap', f'gui/{os.getuid()}', str(rollup_service.plist_path()))
        else:
            outcome = rollup_service.launch('kickstart', rollup_service.target())
        rollup_service.require_success(outcome, 'start', 'Startup definition retained.')
        print('Collector requested; completion is not yet verified. Run service status after it exits.')
        return 0


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    # Preserve the original deploy parser and all its source verification.
    if args[:1] == ['deploy']:
        os.execv(sys.executable, module_command('agent_monitor.runtime.deploy', *args[1:]))
    if args[:1] == ['rebuild-runtime']:
        parser = argparse.ArgumentParser()
        parser.add_argument('--python', required=True)
        parser.add_argument('--restore')
        parser.add_argument('--expect')
        parsed = parser.parse_args(args[1:])
        python = Path(parsed.python)
        if not python.is_absolute() or not python.is_file() or not os.access(python, os.X_OK):
            parser.error('--python must name an executable absolute path outside .venv')
        # The controller must survive replacement of this project's runtime.
        if python.is_relative_to(ROOT / '.venv') or python.resolve().is_relative_to((ROOT / '.venv').resolve()):
            parser.error('select an interpreter outside this project runtime')
        os.execv(str(python), module_command('agent_monitor.runtime.runtime_rebuild', *args[1:], python=python))
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    status_parser = sub.add_parser('status')
    status_parser.add_argument('component', nargs='?', default='all',
                              choices=('all', 'web', 'rollup-daemon', 'connector'))
    restart_parser = sub.add_parser('restart')
    restart_parser.add_argument('component', choices=('local-web', 'hub', 'collector', 'connector'))
    parsed = parser.parse_args(args)
    try:
        return component_status(parsed.component) if parsed.action == 'status' else restart(parsed.component)
    except (OSError, ValueError, subprocess.SubprocessError, process_identity.IdentityUnknown) as error:
        print(f'Service management failed: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
