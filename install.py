#!/usr/bin/env python3
"""Python-based installer for Paperless-ngx Bulletproof.

When executed via ``curl ... | python3 -`` this script bootstraps the rest of the
repository so the full installer can run without a prior ``git clone``.
"""

from pathlib import Path


def _bootstrap() -> None:
    """Download repository sources into a temporary directory and load them."""
    import io
    import os
    import sys
    import tarfile
    import tempfile
    import urllib.request

    url = (
        "https://codeload.github.com/obidose/obidose-paperless-ngx-bulletproof/"
        "tar.gz/refs/heads/main"
    )
    tmpdir = tempfile.mkdtemp(prefix="paperless-inst-")
    with urllib.request.urlopen(url) as resp:
        with tarfile.open(fileobj=io.BytesIO(resp.read()), mode="r:gz") as tf:
            root = tf.getmembers()[0].name.split("/", 1)[0]
            tf.extractall(tmpdir)
    repo = os.path.join(tmpdir, root)
    os.chdir(repo)
    sys.path.insert(0, repo)


try:  # first attempt to import locally present modules
    from installer.common import (
        cfg,
        say,
        need_root,
        ensure_dir_tree,
        preflight_ubuntu,
        prompt_core_values,
        pick_and_merge_preset,
        ok,
        warn,
    )
    from installer import deps, files, pcloud
    from utils.selftest import run_stack_tests
except ModuleNotFoundError:
    _bootstrap()
    from installer.common import (
        cfg,
        say,
        need_root,
        ensure_dir_tree,
        preflight_ubuntu,
        prompt_core_values,
        pick_and_merge_preset,
        ok,
        warn,
    )
    from installer import deps, files, pcloud
    from utils.selftest import run_stack_tests


def main() -> None:
    need_root()

    say("Starting Paperless-ngx setup wizard...")
    preflight_ubuntu()
    deps.install_prereqs()
    deps.ensure_user()
    deps.install_docker()
    deps.install_rclone()

    # pCloud
    pcloud.ensure_pcloud_remote_or_menu()

    # Presets and prompts
    pick_and_merge_preset("https://raw.githubusercontent.com/obidose/obidose-paperless-ngx-bulletproof/main")
    prompt_core_values()

    # Directories and files
    ensure_dir_tree(cfg)
    files.write_env_file()
    files.write_compose_file()
    files.copy_helper_scripts()
    files.bring_up_stack()

    if run_stack_tests(Path(cfg.compose_file), Path(cfg.env_file)):
        ok("Self-test passed")
    else:
        warn("Self-test failed; check container logs")

    files.install_cron_backup()
    files.show_status()


if __name__ == "__main__":
    main()
