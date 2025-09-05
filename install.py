#!/usr/bin/env python3
"""Python-based installer for Paperless-ngx Bulletproof.

When executed via ``curl ... | python3 -`` this script bootstraps the rest of the
repository so the full installer can run without a prior ``git clone``.
"""

from pathlib import Path
import os
import argparse
import sys
import shutil
import subprocess


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


def offer_initial_actions() -> bool:
    """Return True if the script should exit early."""
    from tools import bulletproof as bp

    rem = bp.list_remote_instances()
    opts: list[tuple[str, str]] = []
    if rem:
        opts.append(("Restore all backups", "restore"))
    opts.append(("Install new instance", "install"))
    opts.append(("Launch Bulletproof CLI", "cli"))
    opts.append(("Quit", "quit"))

    while True:
        say("Select action:")
        for idx, (label, _) in enumerate(opts, 1):
            say(f" {idx}) {label}")
        choice = common.prompt("Select", "1")
        try:
            action = opts[int(choice) - 1][1]
            break
        except Exception:
            say("Invalid choice")

    if action == "restore":
        for name in rem:
            cfg.instance_name = name
            cfg.stack_dir = str(bp.BASE_DIR / f"{name}{bp.INSTANCE_SUFFIX}")
            cfg.data_root = str(bp.BASE_DIR / name)
            cfg.refresh_paths()
            ensure_dir_tree(cfg)
            inst = bp.Instance(name, Path(cfg.stack_dir), Path(cfg.data_root), {})
            snaps = bp.fetch_snapshots_for(name)
            if snaps:
                bp.restore_instance(inst, snaps[-1][0], name)
            if Path(cfg.env_file).exists():
                for line in Path(cfg.env_file).read_text().splitlines():
                    if line.startswith("CRON_FULL_TIME="):
                        cfg.cron_full_time = line.split("=", 1)[1].strip()
                    elif line.startswith("CRON_INCR_TIME="):
                        cfg.cron_incr_time = line.split("=", 1)[1].strip()
                    elif line.startswith("CRON_ARCHIVE_TIME="):
                        cfg.cron_archive_time = line.split("=", 1)[1].strip()
            files.copy_helper_scripts()
            files.install_cron_backup()
        bp.multi_main()
        return True

    if action == "cli":
        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path(__file__).resolve().parent)
        try:
            with open("/dev/tty", "r+") as tty:
                subprocess.run(
                    [sys.executable, str(Path(__file__).resolve().parent / "tools" / "bulletproof.py")],
                    stdin=tty,
                    stdout=tty,
                    stderr=tty,
                    check=False,
                    env=env,
                )
        except OSError:
            subprocess.run(
                [sys.executable, str(Path(__file__).resolve().parent / "tools" / "bulletproof.py")],
                check=False,
                env=env,
            )
        return True

    if action == "quit":
        return True

    return False


def main() -> None:
    need_root()
    say(f"Fetching assets from branch '{BRANCH}'")

    say("Starting Paperless-ngx setup wizard...")
    preflight_ubuntu()

    # If the Bulletproof CLI is already installed this one-liner acts as a
    # convenience wrapper.  Skip the heavy installation routine and hand off to
    # the multi-instance manager instead of re-running the wizard.
    if shutil.which("bulletproof") and Path("/usr/local/bin/bulletproof").exists():
        say("Bulletproof CLI detected; launching manager...")
        files.install_global_cli()
        from tools import bulletproof as bp
        # Let the Bulletproof manager handle leftover cleanup when it starts.
        insts = bp.find_instances()
        if not insts:
            rem = bp.list_remote_instances()
            if rem and common.confirm("Remote backups found. Restore all now?", True):
                for name in rem:
                    cfg.instance_name = name
                    cfg.stack_dir = str(bp.BASE_DIR / f"{name}{bp.INSTANCE_SUFFIX}")
                    cfg.data_root = str(bp.BASE_DIR / name)
                    cfg.refresh_paths()
                    ensure_dir_tree(cfg)
                    inst = bp.Instance(name, Path(cfg.stack_dir), Path(cfg.data_root), {})
                    snaps = bp.fetch_snapshots_for(name)
                    if snaps:
                        bp.restore_instance(inst, snaps[-1][0], name)
                    if Path(cfg.env_file).exists():
                        for line in Path(cfg.env_file).read_text().splitlines():
                            if line.startswith("CRON_FULL_TIME="):
                                cfg.cron_full_time = line.split("=", 1)[1].strip()
                            elif line.startswith("CRON_INCR_TIME="):
                                cfg.cron_incr_time = line.split("=", 1)[1].strip()
                            elif line.startswith("CRON_ARCHIVE_TIME="):
                                cfg.cron_archive_time = line.split("=", 1)[1].strip()
                    files.copy_helper_scripts()
                    files.install_cron_backup()
        # Hand control to the bundled Bulletproof manager in a fresh process
        # with its stdio attached to the real TTY so prompts remain interactive
        # even when this script itself is piped through curl.
        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path(__file__).resolve().parent)
        cli_path = Path(__file__).resolve().parent / "tools" / "bulletproof.py"
        try:
            with open("/dev/tty", "r+") as tty:
                subprocess.run(
                    [sys.executable, str(cli_path)],
                    stdin=tty,
                    stdout=tty,
                    stderr=tty,
                    check=False,
                    env=env,
                )
        except OSError:
            subprocess.run([sys.executable, str(cli_path)], check=False, env=env)
        return

    try:
        deps.install_prereqs()
        deps.ensure_user()
        deps.install_docker()
        deps.install_rclone()

        # pCloud
        pcloud.ensure_pcloud_remote_or_menu()

        # Offer to restore existing backups or jump straight into the CLI
        if offer_initial_actions():
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
