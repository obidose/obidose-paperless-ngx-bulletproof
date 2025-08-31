
root@ubuntu:~# bash -c "$(curl -fsSL https://raw.githubusercontent.com/obidose/obidose-paperless-ngx-bulletproof/main/install.sh)"
[•] Fetching modules…
[•] Starting Paperless-ngx setup wizard…
[•] Installing prerequisites…
Hit:1 http://security.ubuntu.com/ubuntu noble-security InRelease
Hit:2 https://download.docker.com/linux/ubuntu noble InRelease
Hit:3 http://archive.ubuntu.com/ubuntu noble InRelease
Hit:4 http://archive.ubuntu.com/ubuntu noble-updates InRelease
Hit:5 http://archive.ubuntu.com/ubuntu noble-backports InRelease
Reading package lists... Done
Reading package lists... Done
Building dependency tree... Done
Reading state information... Done
Calculating upgrade... Done
0 upgraded, 0 newly installed, 0 to remove and 0 not upgraded.
Reading package lists... Done
Building dependency tree... Done
Reading state information... Done
ca-certificates is already the newest version (20240203).
curl is already the newest version (8.5.0-2ubuntu10.6).
gnupg is already the newest version (2.4.4-2ubuntu17.3).
lsb-release is already the newest version (12.0-2).
unzip is already the newest version (6.0-28ubuntu4.1).
tar is already the newest version (1.35+dfsg-3build1).
cron is already the newest version (3.0pl1-184ubuntu2).
software-properties-common is already the newest version (0.99.49.3).
dos2unix is already the newest version (7.5.1-1).
jq is already the newest version (1.7.1-3ubuntu0.24.04.1).
0 upgraded, 0 newly installed, 0 to remove and 0 not upgraded.
[•] Docker already installed.
[•] rclone already installed.
[•] Connect to pCloud via WebDAV (if 2FA is ON, use an App Password).
pCloud login email: mikedromey@gmail.com
pCloud password (or App Password): [•] Trying EU WebDAV endpoint…
Error: update remote: invalid key or value contains \n or \r
Usage:
  rclone config create name type [key value]* [flags]

Flags:
      --all               Ask the full set of config questions
      --continue          Continue the configuration process with an answer
  -h, --help              help for create
      --no-obscure        Force any passwords not to be obscured
      --no-output         Don't provide any output
      --non-interactive   Don't interact with user and return questions
      --obscure           Force any passwords to be obscured
      --result string     Result - use with --continue
      --state string      State - use with --continue

Use "rclone [command] --help" for more information about a command.
Use "rclone help flags" for to see the global flags.
Use "rclone help backends" for a list of supported services.

2025/08/31 05:59:10 NOTICE: Fatal error: update remote: invalid key or value contains \n or \r

^C
root@ubuntu:~#
