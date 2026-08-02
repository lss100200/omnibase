#!/bin/sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
  echo "install-network-broker.sh must run as root" >&2
  exit 2
fi

asset_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
broker_user=omnibase-network-broker

if ! id "$broker_user" >/dev/null 2>&1; then
  useradd --system --create-home --home-dir /var/lib/omnibase-network-broker \
    --shell /usr/sbin/nologin "$broker_user"
fi

broker_uid=$(id -u "$broker_user")
install -d -o root -g root -m 0755 /usr/libexec/omnibase
install -d -o root -g "$broker_user" -m 0750 /etc/omnibase-network-broker
install -d -o "$broker_user" -g "$broker_user" -m 0700 \
  /var/lib/omnibase-network-broker \
  /var/lib/omnibase-network-broker/consumed

install -o root -g root -m 0755 \
  "$asset_dir/omnibase-network-broker.py" \
  /usr/libexec/omnibase/omnibase-network-broker
install -o root -g root -m 0644 \
  "$asset_dir/omnibase-network-broker.service" \
  /etc/systemd/system/omnibase-network-broker.service
install -o root -g root -m 0644 \
  "$asset_dir/omnibase-network-broker-tmpfiles.conf" \
  /usr/lib/tmpfiles.d/omnibase-network-broker.conf

authentication_key=/etc/omnibase-network-broker/daemon-auth.key
if [ ! -f "$authentication_key" ]; then
  temporary_key=/etc/omnibase-network-broker/.daemon-auth.key.$$
  dd if=/dev/urandom bs=32 count=1 2>/dev/null | od -An -tx1 | tr -d ' \n' \
    > "$temporary_key"
  printf '\n' >> "$temporary_key"
  chown root:"$broker_user" "$temporary_key"
  chmod 0440 "$temporary_key"
  mv -f "$temporary_key" "$authentication_key"
fi
if [ "$(stat -c '%U:%G:%a:%s' "$authentication_key")" != \
  "root:$broker_user:440:65" ]; then
  echo "daemon authentication key metadata is invalid" >&2
  exit 4
fi

temporary_config=/etc/omnibase-network-broker/.config.json.$$
cat > "$temporary_config" <<EOF
{
  "connect_timeout_seconds": 2.0,
  "consumed_directory": "/var/lib/omnibase-network-broker/consumed",
  "daemon_authentication_key_path": "/etc/omnibase-network-broker/daemon-auth.key",
  "daemon_uid": $broker_uid,
  "host_namespace_owner_uid": 0,
  "host_network_namespace_path": "/run/omnibase-host-ns/net",
  "max_request_bytes": 65536,
  "permit_directory": "/run/omnibase-network-broker-permits",
  "permit_owner_uid": 0,
  "read_timeout_seconds": 0.25,
  "socket_path": "/run/omnibase-network-broker-daemon/broker.sock",
  "trusted_client_gid": 0,
  "trusted_client_uid": 0
}
EOF
chown root:"$broker_user" "$temporary_config"
chmod 0440 "$temporary_config"
mv -f "$temporary_config" /etc/omnibase-network-broker/config.json

systemd-tmpfiles --create /usr/lib/tmpfiles.d/omnibase-network-broker.conf
systemctl disable --now omnibase-network-broker-host-ns.service >/dev/null 2>&1 || true
rm -f \
  /etc/systemd/system/omnibase-network-broker-host-ns.service \
  /usr/libexec/omnibase/omnibase-network-broker-snapshot-host-net
systemctl daemon-reload
systemctl enable omnibase-network-broker.service
systemctl restart omnibase-runner-host-ns.service
systemctl restart omnibase-network-broker.service

attempt=0
while [ "$attempt" -lt 30 ]; do
  if [ -S /run/omnibase-network-broker-daemon/broker.sock ]; then
    exit 0
  fi
  attempt=$((attempt + 1))
  sleep 1
done

systemctl status omnibase-network-broker.service --no-pager >&2 || true
exit 3
