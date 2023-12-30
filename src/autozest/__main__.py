"""Allow ``python -m autozest`` as well as the installed ``az`` script."""

from autozest.cli.main import main

if __name__ == "__main__":
    raise SystemExit(main())
