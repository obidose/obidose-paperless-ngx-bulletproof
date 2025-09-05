import os
import subprocess
import sys
import time
from .common import say, ok, warn


def run(cmd: list[str], **kwargs) -> None:
    """Run a subprocess command with error checking.

    This helper is a thin wrapper around :func:`subprocess.run` that always
    enables ``check=True`` and forwards any additional keyword arguments such
    as ``input`` or ``env``. The previous implementation only accepted the
    command list, which meant callers passing extra options (for example the
    ``input`` used when importing Docker's GPG key) would raise ``TypeError``.
    Allowing arbitrary kwargs keeps the convenience wrapper while supporting
    these advanced usages.
    """

    subprocess.run(cmd, check=True, **kwargs)


def apt(args: list[str], retries: int | None = None) -> None:
    """Run ``apt-get`` with basic retry logic.

    Retries are controlled by the ``APT_RETRIES`` environment variable (default
    3). HTTP 403/404 errors are surfaced with a helpful hint so the user can
    switch to another mirror if needed.
    """

    if retries is None:
        retries = int(os.environ.get("APT_RETRIES", "3"))
    env = dict(os.environ, DEBIAN_FRONTEND="noninteractive")
    for attempt in range(1, retries + 1):
        proc = subprocess.Popen(
            ["apt-get", *args],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
        )
        output: list[str] = []
        assert proc.stdout is not None  # for mypy/linters
        for line in proc.stdout:
            sys.stdout.write(line)
            output.append(line)
        rc = proc.wait()
        combined = "".join(output)
        if rc == 0:
            return
        if "403" in combined or "404" in combined:
            warn(
                "apt-get returned HTTP error; you may need to choose a different mirror"
            )
        if attempt < retries:
            say(
                f"apt-get {' '.join(args)} failed (attempt {attempt}/{retries}); retrying…"
            )
            time.sleep(2 * attempt)
        else:
            raise subprocess.CalledProcessError(rc, ["apt-get", *args], combined)


def install_prereqs() -> None:
    say("Installing prerequisites…")

    # Some hosts may have an existing Docker APT source configured. If the
    # file is malformed (for example from a prior manual installation), the
    # initial ``apt-get update`` would fail before we get a chance to
    # reconfigure Docker properly.  Removing the list upfront ensures the
    # update succeeds and we can install Docker later with a clean source.
    from pathlib import Path

    docker_list = Path("/etc/apt/sources.list.d/docker.list")
    if docker_list.exists():
        warn("Removing existing Docker apt source to avoid malformed entry")
        docker_list.unlink()

    apt(["update", "-y"])
    apt(["upgrade", "-y"])
    apt([
        "install",
        "-y",
        "ca-certificates",
        "curl",
        "gnupg",
        "lsb-release",
        "unzip",
        "tar",
        "cron",
        "software-properties-common",
        "dos2unix",
        "jq",
    ])
    try:
        run(["sed", "-i", "s/^#\\?user_allow_other/user_allow_other/", "/etc/fuse.conf"])
    except Exception:
        warn("Could not update /etc/fuse.conf")


def ensure_user() -> None:
    import pwd

    try:
        pwd.getpwnam("docker")
    except KeyError:
        say("Creating 'docker' user (uid/gid 1001)…")
        run(["groupadd", "-g", "1001", "docker"])
        run(["useradd", "-m", "-u", "1001", "-g", "1001", "-s", "/bin/bash", "docker"])


def install_docker() -> None:
    from shutil import which

    if which("docker"):
        ok("Docker already installed.")
        run(["usermod", "-aG", "docker", "docker"])  # ensure group
        return
    say("Installing Docker Engine + Compose plugin…")
    run(["install", "-m", "0755", "-d", "/etc/apt/keyrings"])
    curl = subprocess.run(
        ["curl", "-fsSL", "https://download.docker.com/linux/ubuntu/gpg"],
        check=True,
        capture_output=True,
    )
    run([
        "gpg",
        "--dearmor",
        "-o",
        "/etc/apt/keyrings/docker.gpg",
    ],
        input=curl.stdout,
    )
    run(["chmod", "a+r", "/etc/apt/keyrings/docker.gpg"])
    with open("/etc/os-release") as f:
        lines = dict(
            line.strip().split("=", 1) for line in f if "=" in line
        )
    codename = lines.get("VERSION_CODENAME", "stable").strip('"')
    arch = subprocess.check_output(
        ["dpkg", "--print-architecture"], text=True
    ).strip()
    repo = (
        f"deb [arch={arch} signed-by=/etc/apt/keyrings/docker.gpg] "
        f"https://download.docker.com/linux/ubuntu {codename} stable"
    )
    Path = __import__('pathlib').Path
    Path("/etc/apt/sources.list.d").mkdir(parents=True, exist_ok=True)
    with open("/etc/apt/sources.list.d/docker.list", "w") as f:
        f.write(repo + "\n")
    apt(["update", "-y"])
    apt([
        "install",
        "-y",
        "docker-ce",
        "docker-ce-cli",
        "containerd.io",
        "docker-buildx-plugin",
        "docker-compose-plugin",
    ])
    run(["systemctl", "enable", "--now", "docker"])
    run(["usermod", "-aG", "docker", "docker"])


def install_rclone() -> None:
    from shutil import which

    if which("rclone"):
        ok("rclone already installed.")
        return
    say("Installing rclone…")
    run(["bash", "-c", "curl -fsSL https://rclone.org/install.sh | bash"])
