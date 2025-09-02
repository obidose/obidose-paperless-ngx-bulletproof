import subprocess
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


def install_prereqs() -> None:
    say("Installing prerequisites…")
    env = dict(DEBIAN_FRONTEND="noninteractive")
    run(["apt-get", "update", "-y"])
    run(["apt-get", "upgrade", "-y"])
    run([
        "apt-get",
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
    repo = (
        f"deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] "
        f"https://download.docker.com/linux/ubuntu {codename} stable"
    )
    Path = __import__('pathlib').Path
    Path("/etc/apt/sources.list.d").mkdir(parents=True, exist_ok=True)
    with open("/etc/apt/sources.list.d/docker.list", "w") as f:
        f.write(repo + "\n")
    run(["apt-get", "update", "-y"])
    run(["apt-get", "install", "-y", "docker-ce", "docker-ce-cli", "containerd.io", "docker-buildx-plugin", "docker-compose-plugin"])
    run(["systemctl", "enable", "--now", "docker"])
    run(["usermod", "-aG", "docker", "docker"])


def install_rclone() -> None:
    from shutil import which

    if which("rclone"):
        ok("rclone already installed.")
        return
    say("Installing rclone…")
    run(["bash", "-c", "curl -fsSL https://rclone.org/install.sh | bash"])
