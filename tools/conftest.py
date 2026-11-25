"""Adds `--update-golden` (same as GOLDEN_UPDATE=1) for the golden regression tests in tools/golden. Nothing else lives here."""


def pytest_addoption(parser):
    parser.addoption(
        "--update-golden",
        action="store_true",
        default=False,
        help="rewrite the JSON fixtures in tools/golden/fixtures from the current behaviour (a deliberate change)",
    )
