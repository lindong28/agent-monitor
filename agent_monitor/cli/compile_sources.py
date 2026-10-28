"""CLI adapter extracted from the public agent-monitor command."""


def main():
    import sys
    from agent_monitor.source_inventory import compile_sources
    compile_sources(sys.argv[1])


if __name__ == "__main__":
    main()
