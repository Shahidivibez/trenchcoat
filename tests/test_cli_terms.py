"""accept-terms consent gate: refuses without a key or without explicit --yes.
These paths exit before any network call, so they're safe offline."""

import argparse

from trenchcoat.cli import cmd_accept_terms


def test_accept_terms_requires_key(cfg):
    cfg.delphi_api_key = ""
    args = argparse.Namespace(yes=True)
    assert cmd_accept_terms(args) == 1


def test_accept_terms_requires_yes(cfg):
    cfg.delphi_api_key = "fake-key"
    args = argparse.Namespace(yes=False)
    assert cmd_accept_terms(args) == 1


def test_accept_terms_registered():
    from trenchcoat.cli import main

    # parser should recognize the subcommand (help exits 0)
    try:
        main(["accept-terms", "--help"])
    except SystemExit as e:
        assert e.code == 0
