#!/usr/bin/env python3
"""Python-based installer for Paperless-ngx Bulletproof.

When executed via ``curl ... | python3 -`` this script bootstraps the rest of the
repository so the full installer can run without a prior ``git clone``.
"""

from pathlib import Path
import os
import argparse
import sys


def _parse_branch() -> str:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--branch")
    args, unknown = parser.parse_known_args()
    sys.argv[1:] = unknown
    return args.branch or os.environ.get("BP_BRANCH", "main")


BRANCH = _parse_branch()


def _bootstrap() -> None:
    """Download repository sources into a temporary directory and load them."""
    import io
    import sys
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


try:  # first attempt to import locally present modules
    from installer import common, deps, files, pcloud
    from utils.selftest import run_stack_tests
except ModuleNotFoundError:
    _bootstrap()
    from installer import common, deps, files, pcloud
    from utils.selftest import run_stack_tests

cfg = common.cfg
say = common.say
need_root = common.need_root
ensure_dir_tree = common.ensure_dir_tree
preflight_ubuntu = common.preflight_ubuntu
prompt_core_values = common.prompt_core_values
pick_and_merge_preset = common.pick_and_merge_preset
ok = common.ok
warn = common.warn
# ``prompt_backup_plan`` was added in newer releases; fall back to a no-op if
# running against an older checkout that lacks it.
prompt_backup_plan = getattr(common, "prompt_backup_plan", lambda: None)


def main() -> None:
    need_root()
    say(f"Fetching assets from branch '{BRANCH}'")

    say("Starting Paperless-ngx setup wizard...")
    preflight_ubuntu()
    try:
        deps.install_prereqs()
        deps.ensure_user()
        deps.install_docker()
        deps.install_rclone()

        # pCloud
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

        # Presets and prompts
        pick_and_merge_preset(
            f"https://raw.githubusercontent.com/obidose/obidose-paperless-ngx-bulletproof/{BRANCH}"
        )
        prompt_core_values()
        prompt_backup_plan()

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
    except KeyboardInterrupt:
        warn("Installation cancelled; cleaning up")
        files.cleanup_stack_dir()
        raise
    except Exception as e:
        warn(f"Installation failed: {e}")
        files.cleanup_stack_dir()
        raise


if __name__ == "__main__":
    main()
