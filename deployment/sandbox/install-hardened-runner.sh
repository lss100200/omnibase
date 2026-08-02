#!/bin/sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
  echo "install-hardened-runner.sh must run as root" >&2
  exit 2
fi

asset_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
runner_user=omnibase-runner

if ! id "$runner_user" >/dev/null 2>&1; then
  useradd --system --create-home --home-dir /var/lib/omnibase-runner \
    --shell /usr/sbin/nologin "$runner_user"
fi

install -d -o root -g root -m 0755 /usr/libexec/omnibase
install -d -o root -g root -m 0755 /etc/omnibase-runner
install -d -o root -g root -m 0555 /run/omnibase-host-ns
install -d -o "$runner_user" -g "$runner_user" -m 0700 \
  /var/lib/omnibase-runner \
  /var/lib/omnibase-runner/evidence \
  /var/lib/omnibase-runner/runtimes
install -o "$runner_user" -g "$runner_user" -m 0600 /dev/null \
  /var/lib/omnibase-runner/replay.sqlite3

install -o root -g root -m 0755 \
  "$asset_dir/omnibase-isolation-launcher.py" \
  /usr/libexec/omnibase/omnibase-isolation-launcher
install -o root -g root -m 0644 \
  "$asset_dir/omnibase-runner-seccomp.json" \
  /etc/omnibase-runner/seccomp.json
install -o root -g root -m 0644 \
  "$asset_dir/omnibase-runner.apparmor" \
  /etc/apparmor.d/omnibase-runner
install -o root -g root -m 0644 \
  "$asset_dir/omnibase-runner.service" \
  /etc/systemd/system/omnibase-runner.service
install -o root -g root -m 0644 \
  "$asset_dir/omnibase-runner-host-ns.service" \
  /etc/systemd/system/omnibase-runner-host-ns.service

apparmor_parser -r /etc/apparmor.d/omnibase-runner
systemctl daemon-reload
systemctl enable omnibase-runner-host-ns.service omnibase-runner.service
systemctl restart omnibase-runner-host-ns.service
systemctl restart omnibase-runner.service

attempt=0
while [ "$attempt" -lt 30 ]; do
  if [ -S /run/omnibase-runner/control.sock ]; then
    break
  fi
  attempt=$((attempt + 1))
  sleep 1
done

if [ ! -S /run/omnibase-runner/control.sock ]; then
  systemctl status omnibase-runner.service --no-pager >&2 || true
  exit 3
fi

launcher_digest=$(sha256sum /usr/libexec/omnibase/omnibase-isolation-launcher | awk '{print $1}')
seccomp_digest=$(sha256sum /etc/omnibase-runner/seccomp.json | awk '{print $1}')
lsm_digest=$(sha256sum /etc/apparmor.d/omnibase-runner | awk '{print $1}')

if [ -f /etc/omnibase-runner/runner-id ]; then
  runner_id=$(cat /etc/omnibase-runner/runner-id)
else
  runner_id=$(cat /proc/sys/kernel/random/uuid)
  printf '%s\n' "$runner_id" > /etc/omnibase-runner/runner-id
  chmod 0600 /etc/omnibase-runner/runner-id
fi

cat > /etc/omnibase-runner/probe.json <<EOF
{
  "cgroup_root": "/sys/fs/cgroup/system.slice/omnibase-runner.service",
  "expected_launcher_digest": "$launcher_digest",
  "host_namespace_root": "/run/omnibase-host-ns",
  "launcher_path": "/usr/libexec/omnibase/omnibase-isolation-launcher",
  "lsm_profile_digest": "$lsm_digest",
  "lsm_profile_name": "omnibase-runner",
  "lsm_profile_path": "/etc/apparmor.d/omnibase-runner",
  "runner_id": "$runner_id",
  "runner_root": "/var/lib/omnibase-runner",
  "seccomp_profile_digest": "$seccomp_digest",
  "seccomp_profile_path": "/etc/omnibase-runner/seccomp.json"
}
EOF
chmod 0644 /etc/omnibase-runner/probe.json

/usr/libexec/omnibase/omnibase-isolation-launcher probe \
  < /etc/omnibase-runner/probe.json
