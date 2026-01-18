#!/usr/bin/env python3
"""
Legacy installer entry point - redirects to unified paperless.py

For backward compatibility, this still works but the recommended approach is:
  curl -fsSL https://raw.githubusercontent.com/obidose/...main/paperless.py | sudo python3 -
"""

import os
import sys


def _parse_branch() -> str:
    import argparse
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--branch")
    args, unknown = parser.parse_known_args()
    sys.argv[1:] = unknown
    return args.branch or os.environ.get("BP_BRANCH", "main")


BRANCH = _parse_branch()


def _bootstrap() -> None:
    import io
    import tarfile
    import tempfile
    import urllib.request

    url = (
        "https://codeload.github.com/obidose/obidose-paperless-ngx-bulletproof/"
        f"tar.gz/refs/heads/{BRANCH}"
    )
    tmpdir = tempfile.mkdtemp(prefix="paperless-inst-")
    with urllib.request.urlopen(url) as resp:
        with tarfile.open(fileobj=io.BytesIO(resp.read()), mode="r:gz") as tf:
            root = tf.getmembers()[0].name.split("/", 1)[0]
            tf.extractall(tmpdir)
    repo = os.path.join(tmpdir, root)
    os.chdir(repo)
    sys.path.insert(0, repo)


# Import or bootstrap
try:
    from installer import common, deps, files, pcloud
    from utils.selftest import run_stack_tests
except ModuleNotFoundError:
    _bootstrap()
    from installer import common, deps, files, pcloud
    from utils.selftest import run_stack_tests

from pathlib import Path

cfg = common.cfg
say = common.say
need_root = common.need_root
ensure_dir_tree = common.ensure_dir_tree
preflight_ubuntu = common.preflight_ubuntu
prompt_core_values = common.prompt_core_values
pick_and_merge_preset = common.pick_and_merge_preset
ok = common.ok
warn = common.warn
prompt_backup_plan = getattr(common, "prompt_backup_plan", lambda: None)


def main() -> None:
    need_root()
    say(f"Fetching assets from branch '{BRANCH}'")
    say("Starting Paperless-ngx setup wizard...")
    
    preflight_ubuntu()
    deps.install_prereqs()
    deps.ensure_user()
    deps.install_docker()
    deps.install_rclone()
    pcloud.ensure_pcloud_remote_or_menu()
    ensure_dir_tree(cfg)
    
    restore_existing_backup_if_present = getattr(
        files, "restore_existing_backup_if_present", lambda: False
    )
    if restore_existing_backup_if_present():
        files.copy_helper_scripts()
        if Path(cfg.env_file).exists():
            for line in Path(cfg.env_file).read_text().splitlines():
                if line.startswith("CRON_FULL_TIME="):
                    cfg.cron_full_time = line.split("=", 1)[1].strip()
                elif line.startswith("CRON_INCR_TIME="):
                    cfg.cron_incr_time = line.split("=", 1)[1].strip()
                elif line.startswith("CRON_ARCHIVE_TIME="):
                    cfg.cron_archive_time = line.split("=", 1)[1].strip()
        files.install_cron_backup()
        files.show_status()
        return

    pick_and_merge_preset(
        f"https://raw.githubusercontent.com/obidose/obidose-paperless-ngx-bulletproof/{BRANCH}"
    )
    prompt_core_values()
    prompt_backup_plan()
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
