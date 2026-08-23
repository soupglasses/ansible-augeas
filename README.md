# soupglasses.augeas

Manage configuration files as Ansible resources while Augeas handles their
native syntax. The `soupglasses.augeas.augeas` module works with SSH, nginx,
systemd, sudoers, XML, JSON, TOML, and any other file supported by an installed
Augeas lens.

You describe the settings that should exist. You do not maintain a full-file
template or a sequence of `augtool` commands.

The public structure is inspired by
[Ansible Resource Modules](https://docs.ansible.com/projects/ansible/latest/collections/ansible/netcommon/network_resource_module.html):
structured configuration, named resource states, check mode, and diffs. Augeas
provides the parser and writer for local configuration files.

## See it work

Before, `/etc/ssh/sshd_config` contains:

```text
Port 22
PermitRootLogin yes
PasswordAuthentication yes
```

Apply only the settings you want to manage:

```yaml
- name: Disable root and password login
  soupglasses.augeas.augeas:
    file: /etc/ssh/sshd_config
    lens: Sshd
    owner: root
    group: root
    mode: "0600"
    config:
      PermitRootLogin: "no"
      PasswordAuthentication: "no"
    state: merged
```

After:

```text
Port 22
PermitRootLogin no
PasswordAuthentication no
```

`Port` remains because `merged` changes only the supplied nodes. Augeas parses
and writes the file through its `Sshd` lens, preserving unrelated settings and
formatting where the lens can.

File attributes are optional. When declared, `owner`, `group`, `mode`, SELinux
context, and filesystem attributes are enforced by the same task and count as
changes. When omitted, an existing file keeps its metadata; a new file inherits
the target's umask, ownership, and SELinux policy defaults.

## Install

The collection requires ansible-core 2.18 or newer:

```shell
ansible-galaxy collection install soupglasses.augeas
```

Each managed node also needs Augeas, its lenses, and `python-augeas` 1.2.0 or
newer. Common Debian package names are `augeas-lenses`, `libaugeas0`, and
`python3-augeas`; common Fedora package names are `augeas-libs` and
`python3-augeas`.

On macOS, install Augeas with Homebrew, then install the binding for the Python
interpreter used by Ansible:

```shell
brew install augeas
AUGEAS_PREFIX="$(brew --prefix augeas)"
CFLAGS="-I${AUGEAS_PREFIX}/include" LDFLAGS="-L${AUGEAS_PREFIX}/lib" \
  python3 -m pip install "python-augeas>=1.2.0"
```

Package names and included lenses vary by operating system. CI runs the unit,
lens, and integration suites on Linux and macOS.

Execution environment dependency metadata is included under
[`meta/`](https://github.com/soupglasses/ansible-augeas/tree/main/meta).

## Choose a state

| State | Result |
| --- | --- |
| `merged` | Set the supplied nodes and keep everything else. |
| `replaced` | Make the selected subtree match `config`, removing unspecified nodes there. |
| `deleted` | Remove nodes described by `config`, or remove `path` when `config` is omitted. |
| `gathered` | Read the selected tree without changing the file. |

The opening example shows `merged`. The other state boundaries are easiest to
see with the file before and after the task.

### Replace one subtree

Before:

```text
Match User deploy
  X11Forwarding yes
  PasswordAuthentication yes
  AllowTcpForwarding yes
```

Task:

```yaml
- name: Replace the deploy Match block
  soupglasses.augeas.augeas:
    file: /etc/ssh/sshd_config
    lens: Sshd
    path: 'Match[Condition/User="deploy"]'
    config:
      Condition:
        User: deploy
      Settings:
        X11Forwarding: "no"
    state: replaced
```

After:

```text
Match User deploy
  X11Forwarding no
```

The two omitted settings are removed because `config` is the complete desired
contents of the selected `Match` node.

### Delete matching nodes

Before:

```text
Port 22
AcceptEnv LANG LC_CTYPE
AcceptEnv LC_ALL
```

Task:

```yaml
- name: Delete every AcceptEnv entry
  soupglasses.augeas.augeas:
    file: /etc/ssh/sshd_config
    lens: Sshd
    config:
      AcceptEnv: null
    state: deleted
```

After:

```text
Port 22
```

The unindexed `AcceptEnv` expression selects both repeated nodes.

### Gather configuration

```yaml
- name: Read sshd configuration
  soupglasses.augeas.augeas:
    file: /etc/ssh/sshd_config
    lens: Sshd
    state: gathered
  register: sshd
```

The gathered result can be passed back to `config`:

```yaml
changed: false
gathered:
  $children:
    - Port: "22"
    - AcceptEnv: [LANG, LC_CTYPE]
    - AcceptEnv: [LC_ALL]
```

See the [state and tree reference](https://github.com/soupglasses/ansible-augeas/blob/main/docs/reference.md) for exact behavior at
`path`, root-level deletion, node values, and repeated-node identity.

## Describe the Augeas tree in YAML

Most files need only ordinary mappings:

```yaml
config:
  PermitRootLogin: "no"
  Subsystem:
    sftp: /usr/lib/openssh/sftp-server
```

Augeas trees can also contain ordered children, duplicate labels, and nodes
that have both a value and children. The YAML representation keeps those cases
explicit:

| YAML shape | Meaning |
| --- | --- |
| Mapping | Uniquely labelled children |
| Sequence | Numeric children selected by position |
| `$children` | Named children in order; labels may repeat |
| `$value` | The surrounding node's own value |

Use `$children` when order or repeated labels matter:

```yaml
config:
  $children:
    - Port: "22"
    - AcceptEnv: [LANG, LC_CTYPE]
    - AcceptEnv: [LC_IDENTIFICATION, LC_ALL]
```

Mapping keys and `path` accept native Augeas expressions. A predicate gives a
repeated resource a stable identity when the lens exposes one:

```yaml
config:
  'key[.="AAAAC3NzaBob"]':
    options:
      from: 198.51.100.0/24
state: merged
```

This updates the matching authorized key without managing its comment or its
position. Adding a missing valued node needs both its Augeas selector and its
`$value`; the [authorized-key example](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/authorized_keys.yml)
shows that case.

Quote string-like values such as `"no"`, `"off"`, and `"yes"`. Ansible's YAML
loader otherwise converts them to booleans, which this module rejects. `null`
means a node without a value and is distinct from the empty string `""`.

For selectors beyond the examples here, use the upstream
[Augeas path-expression guide](https://github.com/hercules-team/augeas/wiki/Path-expressions).

## Validate before replacing the file

Use `validate` when the application has its own configuration checker. It uses
the same `%s` placeholder convention as `ansible.builtin.copy`:

```yaml
- name: Disable gzip only when nginx accepts the result
  soupglasses.augeas.augeas:
    file: /etc/nginx/nginx.conf
    lens: Nginx
    config:
      http:
        gzip: "off"
    state: merged
    validate: nginx -t -c %s
```

The command runs without a shell. A failed validator leaves the managed file
unchanged. Validation also runs in check mode when the task would change the
file. See [`ansible.builtin.copy` validation](https://docs.ansible.com/projects/ansible/latest/collections/ansible/builtin/copy_module.html#parameter-validate)
for the command convention.

Validators that need several files, such as tools that check a whole
configuration directory, need a wrapper that accepts the candidate path and
stages the surrounding files.

## Check mode, diffs, and formatting

All mutating states support check mode and structured diffs:

```shell
ansible-playbook site.yml --check --diff
```

If the semantic tree is already correct, the source file stays byte-for-byte
unchanged. When a task adds or reconstructs syntax, the lens chooses the text
for that syntax. Its stable output can differ from customary hand-written
spacing or expand a shorthand form. This happens on the semantic change; the
next identical run is idempotent.

The [state and tree reference](https://github.com/soupglasses/ansible-augeas/blob/main/docs/reference.md#formatting-and-lens-behavior)
describes this boundary. Internal save and reconciliation behavior is in the
[design notes](https://github.com/soupglasses/ansible-augeas/blob/main/docs/design.md).

## Lens availability

This collection uses the lenses installed on the managed node and does not
bundle copies. Check the upstream [stock lens list](https://augeas.net/stock_lenses.html)
for standard formats.

For another lens, install its `.aug` file on the managed node and set
`load_path`:

```yaml
- name: Manage an application through a custom lens
  soupglasses.augeas.augeas:
    file: /etc/myapp.conf
    lens: MyApp
    load_path: /usr/local/share/augeas/lenses
    config:
      PORT: "9090"
    state: merged
```

A missing requested lens is an error. The module does not skip it or fall back
to editing text without Augeas.

## Limitations

Augeas works through format-specific parsers called lenses. This collection
cannot manage syntax that the selected lens does not understand. Some lenses
support only part of a format, and the versions shipped by Linux distributions
can differ. A parse failure stops the task without changing the file.

The stock `Yaml` lens is not a general YAML parser and cannot load deeply
nested files such as Netplan configuration. The YAML under this module's
`config` option describes an Augeas tree; it does not make arbitrary YAML
target files supported. For those files, prefer an application-specific
Ansible module when one exists, or manage the complete file with
[`ansible.builtin.template`](https://docs.ansible.com/projects/ansible/latest/collections/ansible/builtin/template_module.html).

There are no stock Augeas lenses for HAProxy, nftables, Pgpool-II, or Tcl
configuration. To manage one of these formats with this collection, install or
write a suitable custom lens. Otherwise, use a dedicated Ansible module or a
template. Check the table below before choosing this collection. The [YAML and
Netplan diagnosis](docs/reference.md#yaml-and-netplan) contains the technical
details.

## Tested lenses

Every link below is an executable YAML example with a complete `before`, module
`task`, and exact `after` file. The suite runs each example through Augeas in
check mode, applies it, reloads it, and verifies idempotence. The catalog is
currently tested with Augeas 1.14.1.

| Lens | Example file | Executable example |
| --- | --- | --- |
| `Aliases` | `/etc/aliases` | [Change the root mail alias](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/aliases.yml) |
| `AptConf` | `/etc/apt/apt.conf` | [Make apt assume yes](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/aptconf.yml) |
| `AptPreferences` | `/etc/apt/preferences` | [Raise an APT pin priority](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/apt_preferences.yml) |
| `Aptsources` | `/etc/apt/sources.list` | [Change a Debian repository distribution](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/apt_sources.yml) |
| `Authorized_Keys` | `/root/.ssh/authorized_keys` | [Add a key safely](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/authorized_keys.yml), [update restrictions without managing comments](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/authorized_keys_options.yml) |
| `Automaster` | `/etc/auto.master` | [Increase an autofs map timeout](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/automaster.yml) |
| `Avahi` | `/etc/avahi/avahi-daemon.conf` | [Change the advertised host name](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/avahi.yml) |
| `Ceph` | `/etc/ceph/ceph.conf` | [Change the monitor address](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/ceph.yml) |
| `Chrony` | `/etc/chrony.conf` | [Change a time source](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/chrony.yml) |
| `Collectd` | `/etc/collectd.conf` | [Change the collectd host name](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/collectd.yml) |
| `Cron` | `/etc/crontab` | [Move a system cron job](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/cron.yml) |
| `Crypttab` | `/etc/crypttab` | [Configure a key file](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/crypttab.yml) |
| `Cups` | `/etc/cups/cupsd.conf` | [Enable printer browsing](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/cups.yml) |
| `Dhclient` | `/etc/dhcp/dhclient.conf` | [Shorten the client timeout](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/dhclient.yml) |
| `Dhcpd` | `/etc/dhcp/dhcpd.conf` | [Extend the default lease time](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/dhcpd.yml) |
| `Dns_Zone` | `/etc/bind/db.example.test` | [Change an A record](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/dns_zone.yml) |
| `Dnsmasq` | `/etc/dnsmasq.conf` | [Increase the cache size](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/dnsmasq.yml) |
| `Dovecot` | `/etc/dovecot/dovecot.conf` | [Limit enabled protocols](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/dovecot.yml) |
| `Exports` | `/etc/exports` | [Narrow an NFS client network](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/exports.yml) |
| `Fail2ban` | `/etc/fail2ban/jail.local` | [Increase the ban time](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/fail2ban.yml) |
| `Fstab` | `/etc/fstab` | [Add a mount option](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/fstab.yml) |
| `Fuse` | `/etc/fuse.conf` | [Raise the mount limit](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/fuse.yml) |
| `Gdm` | `/etc/gdm/custom.conf` | [Enable automatic login](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/gdm.yml) |
| `Group` | `/etc/group` | [Change a local group ID](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/group.yml) |
| `Grub` | `/boot/grub/menu.lst` | [Change a legacy menu entry](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/grub.yml) |
| `GrubEnv` | `/boot/grub/grubenv` | [Change the saved entry](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/grubenv.yml) |
| `Hostname` | `/etc/hostname` | [Set the static host name](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/hostname.yml) |
| `Hosts` | `/etc/hosts` | [Change a host address](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/hosts.yml) |
| `Hosts_Access` | `/etc/hosts.allow` | [Narrow a client network](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/hosts_access.yml) |
| `Httpd` | `/etc/apache2/apache2.conf` | [Change the server-token policy](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/httpd.yml) |
| `Inetd` | `/etc/inetd.conf` | [Change a service wait policy](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/inetd.yml) |
| `Interfaces` | `/etc/network/interfaces` | [Change a static address](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/interfaces.yml) |
| `Iproute2` | `/etc/iproute2/rt_tables` | [Rename a routing table](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/iproute2.yml) |
| `Iptables` | `/etc/iptables-save` | [Change a persisted SSH rule](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/iptables.yml) |
| `Iscsid` | `/etc/iscsi/iscsid.conf` | [Start nodes automatically](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/iscsid.yml) |
| `Json` | JSON configuration | [Change a numeric value](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/json.yml), [configure Docker](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/docker_json.yml) |
| `Kdump` | `/etc/kdump.conf` | [Change the output directory](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/kdump.yml) |
| `Keepalived` | `/etc/keepalived/keepalived.conf` | [Raise an instance priority](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/keepalived.yml) |
| `Known_Hosts` | `/etc/ssh/ssh_known_hosts` | [Rotate a host key](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/known_hosts.yml) |
| `Krb5` | `/etc/krb5.conf` | [Disable KDC DNS discovery](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/krb5.yml) |
| `Ldif` | `/etc/ldap/example.ldif` | [Change an LDIF attribute](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/ldif.yml) |
| `LdSo` | `/etc/ld.so.conf` | [Change a linker search path](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/ldso.yml) |
| `Lightdm` | `/etc/lightdm/lightdm.conf` | [Change the automatic-login account](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/lightdm.yml) |
| `Limits` | `/etc/security/limits.conf` | [Raise a file limit](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/limits.yml) |
| `Login_defs` | `/etc/login.defs` | [Tighten the default umask](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/login_defs.yml) |
| `Logrotate` | `/etc/logrotate.d/app` | [Retain more rotations](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/logrotate.yml) |
| `LVM` | `/etc/lvm/lvm.conf` | [Enable discard requests](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/lvm.yml) |
| `Mdadm_conf` | `/etc/mdadm.conf` | [Change the notification address](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/mdadm.yml) |
| `Memcached` | `/etc/memcached.conf` | [Increase the memory limit](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/memcached.yml) |
| `Mke2fs` | `/etc/mke2fs.conf` | [Change the default block size](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/mke2fs.yml) |
| `Modprobe` | `/etc/modprobe.d/kvm.conf` | [Enable nested virtualization](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/modprobe.yml) |
| `Modules` | `/etc/modules` | [Add a boot-time kernel module](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/modules.yml) |
| `MongoDBServer` | `/etc/mongodb.conf` | [Change the server port](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/mongodbserver.yml) |
| `Monit` | `/etc/monitrc` | [Shorten the polling interval](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/monit.yml) |
| `Multipath` | `/etc/multipath.conf` | [Enable `find_multipaths`](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/multipath.yml) |
| `MySQL` | `/etc/mysql/my.cnf` | [Raise the connection limit](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/mysql.yml) |
| `NetworkManager` | `/etc/NetworkManager/system-connections/app.nmconnection` | [Set manual IPv4](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/networkmanager.yml) |
| `Networks` | `/etc/networks` | [Change a network number](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/networks.yml) |
| `Nginx` | `/etc/nginx/nginx.conf` | [Disable HTTP gzip](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/nginx.yml) |
| `Nslcd` | `/etc/nslcd.conf` | [Change the LDAP server](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/nslcd.yml) |
| `Nsswitch` | `/etc/nsswitch.conf` | [Prefer systemd-resolved](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/nsswitch.yml) |
| `Ntp` | `/etc/ntp.conf` | [Change a server without losing options](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/ntp.yml) |
| `Odbc` | `/etc/odbc.ini` | [Change a database server](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/odbc.yml) |
| `OpenDkim` | `/etc/opendkim.conf` | [Change the local socket](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/opendkim.yml) |
| `OpenVPN` | `/etc/openvpn/server.conf` | [Move a server to TCP 443](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/openvpn.yml) |
| `Pam` | `/etc/pam.d/login` | [Change an authentication rule](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/pam.yml) |
| `Passwd` | `/etc/passwd` | [Disable interactive login](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/passwd.yml) |
| `Pg_Hba` | `/etc/postgresql/16/main/pg_hba.conf` | [Change an authentication method](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/pg_hba.yml) |
| `Pgbouncer` | `/etc/pgbouncer/pgbouncer.ini` | [Change the listen port](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/pgbouncer.yml) |
| `PHP` | `/etc/php.ini` | [Raise the memory limit](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/php.yml) |
| `Postfix_Main` | `/etc/postfix/main.cf` | [Expose Postfix on all interfaces](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/postfix_main.yml) |
| `Postfix_Master` | `/etc/postfix/master.cf` | [Disable smtpd chrooting](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/postfix_master.yml) |
| `Postfix_Transport` | `/etc/postfix/transport` | [Change a transport relay](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/postfix_transport.yml) |
| `Postfix_Virtual` | `/etc/postfix/virtual` | [Change a virtual destination](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/postfix_virtual.yml) |
| `Postgresql` | `/etc/postgresql/16/main/postgresql.conf` | [Raise the connection limit](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/postgresql.yml) |
| `Properties` | `/etc/app.properties` | [Enable a feature flag](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/properties.yml) |
| `Protocols` | `/etc/protocols` | [Rename a private protocol alias](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/protocols.yml) |
| `Redis` | `/etc/redis/redis.conf` | [Change the listen port](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/redis.yml) |
| `Resolv` | `/etc/resolv.conf` | [Change the first resolver](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/resolv.yml) |
| `Rsyncd` | `/etc/rsyncd.conf` | [Enable module writes](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/rsyncd.yml) |
| `Rsyslog` | `/etc/rsyslog.conf` | [Redirect a selector](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/rsyslog.yml) |
| `Samba` | `/etc/samba/smb.conf` | [Change the workgroup](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/samba.yml) |
| `Services` | `/etc/services` | [Change a named service port](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/services.yml) |
| `Shadow` | `/etc/shadow` | [Set a password maximum age](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/shadow.yml) |
| `Shells` | `/etc/shells` | [Replace an allowed shell](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/shells.yml) |
| `Shellvars` | `/etc/default/app` | [Change an environment-file port](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/shellvars.yml) |
| `Squid` | `/etc/squid/squid.conf` | [Change the visible host name](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/squid.yml) |
| `Ssh` | `/etc/ssh/ssh_config` | [Increase the client keepalive](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/ssh.yml) |
| `Sshd` | `/etc/ssh/sshd_config` | [Disable root and password login](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/sshd.yml) |
| `Sssd` | `/etc/sssd/sssd.conf` | [Select the SSSD domain](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/sssd.yml) |
| `Strongswan` | `/etc/strongswan.conf` | [Reduce the worker count](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/strongswan.yml) |
| `Stunnel` | `/etc/stunnel/stunnel.conf` | [Change a TLS backend](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/stunnel.yml) |
| `Sudoers` | `/etc/sudoers` | [Add a Defaults flag](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/sudoers.yml) |
| `Sysctl` | `/etc/sysctl.conf` | [Reduce virtual-memory swapping](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/sysctl.yml) |
| `Syslog` | `/etc/syslog.conf` | [Redirect classic syslog messages](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/syslog.yml) |
| `Systemd` | `/etc/systemd/system/app.service` | [Change the restart policy](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/systemd.yml) |
| `Tinc` | `/etc/tinc/net/tinc.conf` | [Change a VPN interface](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/tinc.yml) |
| `Tmpfiles` | `/etc/tmpfiles.d/myapp.conf` | [Restrict a directory mode](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/tmpfiles.yml) |
| `Toml` | TOML configuration | [Change a table integer](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/toml.yml), [configure containerd](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/containerd_toml.yml) |
| `Tuned` | `/etc/tuned/tuned-main.conf` | [Enable dynamic optimization](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/tuned.yml) |
| `Vsftpd` | `/etc/vsftpd.conf` | [Enable local uploads](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/vsftpd.yml) |
| `Xinetd` | `/etc/xinetd.conf` | [Raise the instance limit](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/xinetd.yml) |
| `Xml` | `/etc/xml/example.xml` | [Change an attribute without losing text](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/xml.yml) |
| `Xorg` | `/etc/X11/xorg.conf` | [Select a GPU driver](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/xorg.yml) |
| `Yaml` | Supported YAML subset | [Change a scalar](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/yaml.yml) |
| `Yum` | `/etc/yum.conf` | [Retain downloaded packages](https://github.com/soupglasses/ansible-augeas/blob/main/tests/lens_examples/yum.yml) |

## Module documentation

Use `ansible-doc` for the complete parameter and return-value reference:

```shell
ansible-doc soupglasses.augeas.augeas
```

The source documentation is also available in
[`plugins/modules/augeas.py`](https://github.com/soupglasses/ansible-augeas/blob/main/plugins/modules/augeas.py).

## Development

Develop the repository at an Ansible collection path such as
`ansible_collections/soupglasses/augeas`, then run:

```shell
ansible-test sanity
ansible-test units
ansible-test integration augeas
ansible-galaxy collection build
```

See [CONTRIBUTING.md](https://github.com/soupglasses/ansible-augeas/blob/main/CONTRIBUTING.md) for dependencies and test expectations.

## Support and contributing

Report bugs and request features through the
[GitHub issue tracker](https://github.com/soupglasses/ansible-augeas/issues).
See [CONTRIBUTING.md](https://github.com/soupglasses/ansible-augeas/blob/main/CONTRIBUTING.md) for the test commands and contribution
requirements.

Releases follow [Semantic Versioning](https://semver.org/). Breaking changes to
the public module interface are reserved for a new major version.

## License

GNU General Public License v3.0 or later. See
[LICENSE](https://github.com/soupglasses/ansible-augeas/blob/main/LICENSE).
