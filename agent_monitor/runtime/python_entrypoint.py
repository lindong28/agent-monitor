"""Launch checkout modules without changing the caller's cwd or environment."""
import sys

from agent_monitor.paths import PROJECT_ROOT


_BOOTSTRAP = (
    "import sys; sys.path[0] = sys.argv.pop(1); import runpy; "
    "runpy.run_module(sys.argv.pop(1), run_name='__main__', alter_sys=True)"
)


def module_command(module, *arguments, python=None):
    """Pin implementation imports while retaining relative caller paths."""
    return [str(python or sys.executable), "-c", _BOOTSTRAP, str(PROJECT_ROOT), module,
            *map(str, arguments)]
