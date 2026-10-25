#!/usr/bin/env python3
"""
atv.py - find an Android TV on your Wi-Fi, back it up, switch off its ads,
telemetry and junk (reversibly), and install Glass Home as its home screen.

Standard-library Python 3.8+ and Google's `adb` (Android platform-tools).
Run it on a computer on the same network as the TV.

    ./atv.py discover                    find the TV
    ./atv.py connect 192.168.1.50        connect (accept the prompt on the TV)
    ./atv.py backup                      always first; writes the restore script
    ./atv.py audit                       what's on it, what can go
    ./atv.py debloat                     asks, disables, checks with screenshots
    ./atv.py install-home                Glass Home as the home screen
    ./atv.py restore                     undo everything (or run the restore.sh)

Safety rules it follows:
  - never uninstalls anything (only `pm disable-user`, undone by `pm enable`);
  - refuses to change anything before a backup of this TV exists;
  - only offers packages it knows (catalog.json), or unknown ones whose names
    look like ads or telemetry, which it asks about one at a time;
  - never touches the system, Google Play services, the Play Store, input,
    settings, remote, setup, update or TV-input packages;
  - only replaces the stock home screen after Glass Home has been shown to
    start, and puts it back at once if the TV is left without a home screen;
  - takes a screenshot from the TV after every step (state/<tv>/shots/).

Every change is written to state/<tv>/journal.jsonl, and the backup folder has
a standalone restore.sh that needs only adb.
"""

import argparse
import datetime as _dt
import ipaddress
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.environ.get('ATV_STATE') or os.path.join(HERE, 'state')
CATALOG = os.path.join(HERE, 'catalog.json')
DEFAULT_APK = os.path.join(HERE, 'launcher', 'build', 'glass-home.apk')
GLASS_PKG = 'app.glasshome.tv'
GLASS_HOME = GLASS_PKG + '/' + GLASS_PKG + '.HomeActivity'
OWN_PHOTOS = '/sdcard/Android/data/%s/files/wallpapers' % GLASS_PKG
ANIMATION_KEYS = ('window_animation_scale', 'transition_animation_scale', 'animator_duration_scale')
# Private DNS (Android 9+) with an ad- and tracker-blocking resolver: free, no account.
AD_BLOCK_DNS = 'dns.adguard-dns.com'
UNSET = '<unset>'

# Never disabled, whatever a plan or the catalogue says. Disabling any of
# these can leave a TV unbootable, without input, or unable to update.
PROTECTED = [re.compile(p) for p in (
    r'^android$', r'^com\.android\.systemui$', r'^com\.android\.settings$', r'^com\.android\.tv\.settings$',
    r'^com\.android\.providers\.', r'^com\.android\.shell$', r'^com\.android\.phone$',
    r'^com\.android\.packageinstaller$', r'^com\.google\.android\.packageinstaller$',
    r'^com\.android\.permissioncontroller$', r'^com\.google\.android\.permissioncontroller$',
    r'^com\.android\.networkstack', r'^com\.google\.android\.networkstack', r'^com\.android\.bluetooth',
    r'^com\.android\.inputdevices$', r'inputmethod', r'\.ime$', r'^com\.google\.android\.leanback\.ime$',
    r'^com\.android\.keychain$', r'^com\.android\.externalstorage$', r'^com\.android\.server\.',
    r'^com\.android\.webview$', r'^com\.google\.android\.webview$', r'^com\.google\.android\.trichromelibrary',
    r'^com\.google\.android\.gms$', r'^com\.google\.android\.gsf$', r'^com\.android\.vending$',
    r'^com\.google\.android\.ext\.', r'^com\.android\.location', r'^com\.android\.se$',
    r'^com\.google\.android\.tv\.remote\.service$', r'^com\.google\.android\.tungsten\.setupwraith$',
    r'setupwizard', r'^com\.android\.tv$', r'^com\.google\.android\.tv$', r'ota$', r'\.update', r'upgrade',
    r'^com\.android\.certinstaller$', r'^com\.android\.wifi', r'^com\.google\.android\.wifi',
    r'^com\.android\.media', r'^com\.google\.android\.media', r'^com\.android\.tethering',
    r'^com\.android\.cellbroadcast', r'^com\.android\.hdmi', r'^com\.android\.tv\.(?!.*(recommend|promo))',
    r'^' + re.escape(GLASS_PKG) + '$',
)]

# Unknown packages whose names look like this are offered one by one, default No.
SUSPECT = re.compile(
    r'(^|[._])('
    r'ads?(service|sdk|track\w*|push|engine|manager|server|agent|report\w*|platform)?|'
    r'\w*advert\w*|\w*analytic\w*|\w*telemetr\w*|\w*track(er|ing)\w*|(?!bio)\w*metrics?|stats|acr|samba\w*|'
    r'\w*promo\w*|\w*recommend\w*|feedback|\w*crashreport\w*|\w*bugreport\w*|\w*logupload\w*|'
    r'\w*logcollect\w*|\w*datacollect\w*|usagestats|survey|\w*marketing\w*|campaign'
    r')([._]|$)', re.I)

GROUPS = ('ads', 'telemetry', 'junk')
GROUP_TITLES = {
    'ads': 'Ads and promoted content',
    'telemetry': 'Telemetry (usage, crash and viewing reports)',
    'junk': 'Junk (nothing useful on a TV)',
    'optional': 'Apps and features you might use',
    'suspect': 'Not in the catalogue, but the name suggests ads or tracking',
}


class AtvError(Exception):
    pass


def log(msg=''):
    print(msg, flush=True)


def now_stamp():
    return _dt.datetime.now().strftime('%Y%m%d-%H%M%S')


def is_protected(pkg):
    return any(p.search(pkg) for p in PROTECTED)


# --------------------------------------------------------------------- adb

def adb_path():
    exe = os.environ.get('ADB') or shutil.which('adb')
    if not exe:
        raise AtvError('adb not found. Install Android platform-tools '
                       '(https://developer.android.com/tools/releases/platform-tools) '
                       'or set ADB=/path/to/adb.')
    return exe


def run(args, timeout=60, check=True, binary=False, input_bytes=None):
    try:
        p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout,
                           input=input_bytes)
    except subprocess.TimeoutExpired:
        raise AtvError('timed out: %s' % ' '.join(args))
    out = p.stdout if binary else p.stdout.decode('utf-8', 'replace')
    if check and p.returncode != 0:
        err = p.stderr.decode('utf-8', 'replace').strip() or (out if not binary else '')
        raise AtvError('%s failed: %s' % (' '.join(args[:4]), str(err).strip()))
    return out if binary else out.replace('\r\n', '\n')


class Tv:
    """One TV over adb."""

    def __init__(self, serial):
        self.serial = serial
        self.adb = adb_path()
        self._props = None

    def cmd(self, *args, **kw):
        return run([self.adb, '-s', self.serial] + list(args), **kw)

    def sh(self, command, check=True, timeout=60):
        """Runs a shell command on the TV and returns its output."""
        return self.cmd('shell', command, check=check, timeout=timeout)

    def prop(self, name):
        if self._props is None:
            self._props = {}
            for line in self.sh('getprop').splitlines():
                m = re.match(r'\[(.+?)\]: \[(.*)\]', line)
                if m:
                    self._props[m.group(1)] = m.group(2)
        return self._props.get(name, '')

    @property
    def sdk(self):
        try:
            return int(self.prop('ro.build.version.sdk'))
        except ValueError:
            return 0

    def device_id(self):
        sid = self.prop('ro.serialno') or self.prop('ro.boot.serialno') or self.serial
        model = self.prop('ro.product.model') or 'tv'
        return re.sub(r'[^A-Za-z0-9._-]+', '-', '%s-%s' % (model, sid)).strip('-')

    def packages(self, flag=''):
        out = self.sh('pm list packages %s' % flag).splitlines()
        return sorted({l.split(':', 1)[1].strip() for l in out if l.startswith('package:')})

    def disabled(self):
        return set(self.packages('-d'))

    def features(self):
        return {l.split(':', 1)[1].strip() for l in self.sh('pm list features', check=False).splitlines()
                if l.startswith('feature:')}

    def setting(self, ns, key):
        v = self.sh('settings get %s %s' % (ns, key), check=False).strip()
        return UNSET if v in ('', 'null') else v

    def home(self):
        """The component the Home button opens now, '' if none, None if this Android can't say."""
        if 0 < self.sdk < 24:
            return None   # `cmd package resolve-activity` arrived in Android 7
        out = self.sh('cmd package resolve-activity --brief -a android.intent.action.MAIN '
                      '-c android.intent.category.HOME', check=False).strip().splitlines()
        comp = out[-1].strip() if out else ''
        return comp if '/' in comp else ''

    def set_home(self, component):
        for c in ('cmd package set-home-activity --user 0 %s', 'cmd package set-home-activity %s',
                  'pm set-home-activity %s'):
            out = self.sh(c % shlex.quote(component), check=False)
            if 'rror' not in out and 'nknown' not in out and 'xception' not in out:
                return True
        return False

    def focused_app(self):
        out = self.sh('dumpsys window', check=False, timeout=30)
        for key in ('mCurrentFocus', 'mFocusedApp'):
            for line in out.splitlines():
                if key in line:
                    m = re.search(r'([a-zA-Z0-9_.]+)/([a-zA-Z0-9_.$]+)', line)
                    if m:
                        return m.group(1)
        return ''

    def key(self, name):
        code = name if name.startswith('KEYCODE_') or name.isdigit() else 'KEYCODE_' + name.upper()
        self.sh('input keyevent %s' % code)

    def disable(self, pkg):
        out = self.sh('pm disable-user --user 0 %s' % shlex.quote(pkg), check=False)
        return pkg in self.disabled(), out.strip()

    def enable(self, pkg):
        out = self.sh('pm enable --user 0 %s' % shlex.quote(pkg), check=False)
        if pkg in self.disabled():
            out += self.sh('pm enable %s' % shlex.quote(pkg), check=False)
        return pkg not in self.disabled(), out.strip()

    def meminfo(self):
        info = {}
        for line in self.sh('cat /proc/meminfo', check=False).splitlines():
            m = re.match(r'(\w+):\s+(\d+)', line)
            if m:
                info[m.group(1)] = int(m.group(2))
        return info

    def process_count(self):
        out = self.sh('ps -A 2>/dev/null || ps', check=False)
        return max(0, len(out.strip().splitlines()) - 1)

    def screencap(self):
        data = self.cmd('exec-out', 'screencap', '-p', binary=True, timeout=30)
        if not data.startswith(b'\x89PNG'):
            # very old adb/Android mangle \n in exec-out; fall back to a file
            self.sh('screencap -p /sdcard/atv-shot.png')
            data = self.cmd('exec-out', 'cat', '/sdcard/atv-shot.png', binary=True, timeout=30)
            self.sh('rm -f /sdcard/atv-shot.png', check=False)
        if not data.startswith(b'\x89PNG'):
            raise AtvError('the TV returned no screenshot')
        return data


# --------------------------------------------------------------------- state

def target_file():
    return os.path.join(STATE, 'target.txt')


def pick_serial(args):
    s = getattr(args, 'serial', None) or os.environ.get('ATV_SERIAL')
    if not s and os.path.exists(target_file()):
        s = open(target_file()).read().strip()
    if not s:
        raise AtvError('no TV selected: run `atv.py discover`, then `atv.py connect <address>`')
    return s


class Workspace:
    """state/<tv>/: backups, screenshots, the journal of changes."""

    def __init__(self, tv):
        self.tv = tv
        self.dir = os.path.join(STATE, tv.device_id())
        self.shots = os.path.join(self.dir, 'shots')
        os.makedirs(self.shots, exist_ok=True)
        self.journal_path = os.path.join(self.dir, 'journal.jsonl')

    def latest_backup(self):
        root = os.path.join(self.dir, 'backups')
        if not os.path.isdir(root):
            return None
        names = sorted(n for n in os.listdir(root) if os.path.exists(os.path.join(root, n, 'backup.json')))
        return os.path.join(root, names[-1]) if names else None

    def require_backup(self):
        b = self.latest_backup()
        if not b:
            raise AtvError('no backup of this TV yet. Run `atv.py backup` first.')
        return b

    def journal(self, action, **fields):
        rec = dict(t=_dt.datetime.now().isoformat(timespec='seconds'), action=action, **fields)
        with open(self.journal_path, 'a') as f:
            f.write(json.dumps(rec) + '\n')

    def shot(self, name, quiet=False):
        """Takes a screenshot from the TV; returns its path (or None if the TV refused)."""
        existing = [n for n in os.listdir(self.shots) if n.endswith('.png')]
        path = os.path.join(self.shots, '%03d-%s.png' % (len(existing) + 1, re.sub(r'[^\w.-]+', '-', name)))
        try:
            data = self.tv.screencap()
        except AtvError as e:
            log('  ! no screenshot: %s' % e)
            return None
        with open(path, 'wb') as f:
            f.write(data)
        if not quiet:
            log('  screenshot: %s%s' % (path, '  (black: the TV hides protected video from screenshots)'
                                        if looks_black(data) else ''))
        return path

    def record_setting(self, backup, ns, key, old):
        """Keeps the first-seen value of a setting we change, for restore.sh."""
        path = os.path.join(backup, 'settings-changed.txt')
        seen = set()
        if os.path.exists(path):
            for line in open(path):
                parts = line.rstrip('\n').split(' ', 2)
                if len(parts) >= 2:
                    seen.add((parts[0], parts[1]))
        if (ns, key) not in seen:
            with open(path, 'a') as f:
                f.write('%s %s %s\n' % (ns, key, old))


def looks_black(png):
    """A rough check: a PNG of a black screen compresses to almost nothing."""
    return len(png) < 30000


# --------------------------------------------------------------------- catalogue

def load_catalog():
    with open(CATALOG) as f:
        entries = json.load(f)['packages']
    return {e['package']: e for e in entries}


def classify(installed, enabled, catalog):
    """Groups the installed, enabled packages into what can be offered."""
    found = {g: [] for g in ('ads', 'telemetry', 'junk', 'optional', 'launcher', 'suspect', 'keep')}
    for pkg in installed:
        if pkg not in enabled:
            continue
        e = catalog.get(pkg)
        if e:
            group = 'keep' if is_protected(pkg) and e['group'] != 'launcher' else e['group']
            found[group].append(dict(e))
        elif SUSPECT.search(pkg) and not is_protected(pkg) and not pkg.startswith('com.google.android.gms'):
            found['suspect'].append({'package': pkg, 'group': 'suspect', 'label': pkg,
                                     'why': 'Unknown package; its name suggests ads, tracking or promotions. '
                                            'Look it up before disabling.'})
    return found


# --------------------------------------------------------------------- commands

def local_ipv4s():
    ips = set()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('192.0.2.1', 9))   # no packet is sent; picks the outgoing interface
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    return sorted(ip for ip in ips if not ip.startswith('127.'))


DISCOVER_PORTS = {5555: 'adb (network debugging)', 8009: 'Google Cast', 6466: 'Android TV Remote',
                  6467: 'Android TV Remote pairing', 8008: 'Cast/DIAL'}


def cmd_discover(args):
    if args.subnet:
        nets = [ipaddress.ip_network(args.subnet, strict=False)]
    else:
        ips = local_ipv4s()
        if not ips:
            raise AtvError('could not find this computer\'s network address; pass --subnet 192.168.1.0/24')
        nets = sorted({ipaddress.ip_network(ip + '/24', strict=False) for ip in ips}, key=str)
    ports = [int(p) for p in os.environ.get('ATV_DISCOVER_PORTS', '').split(',') if p] or list(DISCOVER_PORTS)
    hosts = [str(h) for n in nets for h in (n.hosts() if n.num_addresses > 1 else [n.network_address])]
    log('Scanning %s (%d addresses) for TVs...' % (', '.join(map(str, nets)), len(hosts)))
    found = {}
    lock = threading.Lock()

    def probe(host_port):
        host, port = host_port
        try:
            with socket.create_connection((host, port), timeout=args.timeout):
                with lock:
                    found.setdefault(host, []).append(port)
        except OSError:
            pass

    with ThreadPoolExecutor(max_workers=128) as pool:
        list(pool.map(probe, [(h, p) for h in hosts for p in ports]))

    # Android 11+ "Wireless debugging" advertises itself over mDNS instead.
    mdns = []
    try:
        out = run([adb_path(), 'mdns', 'services'], timeout=10, check=False)
        for line in out.splitlines():
            m = re.search(r'(\S+)\s+(_adb-tls-(?:connect|pairing)\._tcp)\S*\s+([\d.]+):(\d+)', line)
            if m:
                mdns.append((m.group(1), m.group(2), m.group(3), m.group(4)))
    except AtvError:
        pass

    if not found and not mdns:
        log('\nNo TV found. On the TV:')
        log('  Settings > Device Preferences (or System) > About > click "Build" 7 times (Developer options),')
        log('  then Developer options > "USB debugging" on, and "Network debugging" (or "ADB over network") on.')
        log('  Newer Google TV: Developer options > "Wireless debugging" > "Pair device with pairing code",')
        log('  then: atv.py pair <ip:port> <code>')
        return 1
    log('')
    best = []
    for host in sorted(found, key=lambda h: tuple(int(x) for x in h.split('.'))):
        name = ''
        try:
            name = socket.gethostbyaddr(host)[0]
        except OSError:
            pass
        services = ', '.join(DISCOVER_PORTS.get(p, str(p)) for p in sorted(found[host]))
        tv = 6466 in found[host] or 8009 in found[host]
        log('  %-15s %-28s %s%s' % (host, name[:28], services, '   <- likely a TV' if tv else ''))
        if 5555 in found[host]:
            best.append(host)
    for name, kind, host, port in mdns:
        log('  %-15s %-28s %s on port %s' % (host, name[:28], 'wireless debugging' if 'connect' in kind
                                             else 'wireless debugging pairing', port))
    log('')
    if len(best) == 1:
        log('Next: ./atv.py connect %s' % best[0])
    elif best:
        log('Several devices accept adb. Next: ./atv.py connect <address>')
    else:
        log('Found TVs, but none accepts adb yet: turn on Network debugging on the TV (see --help).')
    return 0


def cmd_pair(args):
    out = run([adb_path(), 'pair', args.address, args.code], timeout=30, check=False)
    log(out.strip())
    if 'Successfully paired' not in out:
        raise AtvError('pairing failed')
    log('Paired. Now connect to the address shown under "Wireless debugging" (a different port): '
        './atv.py connect <ip:port>')
    return 0


def cmd_connect(args):
    addr = args.address if ':' in args.address else args.address + ':5555'
    adb = adb_path()
    out = run([adb, 'connect', addr], timeout=20, check=False).strip()
    log(out)
    # "failed to authenticate" just means the TV is showing its Allow prompt.
    if 'cannot' in out or ('failed' in out.lower() and 'authenticate' not in out):
        raise AtvError('could not connect to %s' % addr)
    log('If the TV asks "Allow USB debugging?", tick "Always allow from this computer" and choose Allow.')
    deadline = time.time() + args.wait
    state = ''
    while time.time() < deadline:
        state = run([adb, '-s', addr, 'get-state'], timeout=10, check=False).strip()
        if state == 'device':
            break
        time.sleep(2)
    if state != 'device':
        raise AtvError('the TV did not allow the connection (state: %s). Accept the prompt on the TV and '
                       'run connect again.' % (state or 'offline'))
    os.makedirs(STATE, exist_ok=True)
    with open(target_file(), 'w') as f:
        f.write(addr + '\n')
    tv = Tv(addr)
    print_info(tv)
    ws = Workspace(tv)
    ws.shot('connected')
    return 0


def print_info(tv):
    mem = tv.meminfo()
    log('Connected: %s %s (%s), Android %s (API %s)' % (
        tv.prop('ro.product.manufacturer'), tv.prop('ro.product.model'), tv.prop('ro.product.device'),
        tv.prop('ro.build.version.release'), tv.prop('ro.build.version.sdk')))
    feats = tv.features()
    if 'android.software.leanback' not in feats and 'tv' not in tv.prop('ro.build.characteristics'):
        log('  ! this does not report itself as a TV (no leanback feature). Continue only if that is expected.')
    if tv.prop('ro.product.manufacturer').lower() == 'amazon':
        log('  ! Fire TV: Fire OS ignores other home screens; debloating works, install-home may not.')
    if mem:
        log('  memory: %d MB total, %d MB available; %d processes' % (
            mem.get('MemTotal', 0) // 1024, mem.get('MemAvailable', mem.get('MemFree', 0)) // 1024,
            tv.process_count()))
    log('  home screen: %s' % (tv.home() or 'unknown'))


def cmd_info(args):
    tv = Tv(pick_serial(args))
    print_info(tv)
    return 0


def cmd_shot(args):
    tv = Tv(pick_serial(args))
    Workspace(tv).shot(args.name)
    return 0


def cmd_key(args):
    tv = Tv(pick_serial(args))
    for k in args.keys:
        tv.key(k)
        time.sleep(0.4)
    if args.shot:
        time.sleep(1.5)
        Workspace(tv).shot('after-' + '-'.join(args.keys))
    return 0


def cmd_backup(args):
    tv = Tv(pick_serial(args))
    ws = Workspace(tv)
    b = os.path.join(ws.dir, 'backups', now_stamp())
    os.makedirs(b)
    log('Backing up %s to %s' % (tv.device_id(), b))
    allp = tv.packages()
    disabled = tv.disabled()
    data = {
        'created': _dt.datetime.now().isoformat(timespec='seconds'),
        'serial': tv.serial,
        'device_id': tv.device_id(),
        'props': {k: tv.prop(k) for k in (
            'ro.product.manufacturer', 'ro.product.brand', 'ro.product.model', 'ro.product.device',
            'ro.build.version.release', 'ro.build.version.sdk', 'ro.build.fingerprint',
            'ro.build.characteristics')},
        'features': sorted(tv.features()),
        'packages': allp,
        'disabled': sorted(disabled),
        'system': tv.packages('-s'),
        'third_party': tv.packages('-3'),
        'home': tv.home(),
        'meminfo': tv.meminfo(),
        'processes': tv.process_count(),
        'settings': {},
    }
    for ns in ('global', 'secure', 'system'):
        vals = {}
        for line in tv.sh('settings list %s' % ns, check=False).splitlines():
            if '=' in line:
                k, v = line.split('=', 1)
                vals[k] = v
        data['settings'][ns] = vals
    with open(os.path.join(b, 'backup.json'), 'w') as f:
        json.dump(data, f, indent=1, sort_keys=True)
    with open(os.path.join(b, 'enabled-at-backup.txt'), 'w') as f:
        f.write(''.join(p + '\n' for p in allp if p not in disabled))
    with open(os.path.join(b, 'home-at-backup.txt'), 'w') as f:
        f.write((data['home'] or '') + '\n')
    open(os.path.join(b, 'settings-changed.txt'), 'a').close()
    write_restore_script(b, tv.serial)
    if args.apks:
        apkdir = os.path.join(b, 'apks')
        os.makedirs(apkdir)
        for pkg in data['third_party']:
            for line in tv.sh('pm path %s' % pkg, check=False).splitlines():
                if line.startswith('package:'):
                    remote = line.split(':', 1)[1].strip()
                    tv.cmd('pull', remote, os.path.join(apkdir, '%s-%s' % (pkg, os.path.basename(remote))),
                           check=False, timeout=600)
    shot = ws.shot('backup-home-screen', quiet=True)
    if shot:
        shutil.copy(shot, os.path.join(b, 'before.png'))
    ws.journal('backup', path=b, packages=len(allp), disabled=len(disabled), home=data['home'])
    log('  %d packages (%d already disabled), home screen %s, %d settings' % (
        len(allp), len(disabled), data['home'] or 'unknown', sum(len(v) for v in data['settings'].values())))
    log('  restore script: %s' % os.path.join(b, 'restore.sh'))
    return 0


RESTORE_SH = r'''#!/bin/sh
# Restores this TV to how it was at the backup in this folder (made by atv.py):
#   - turns back on every app that was on then and is off now,
#   - puts back every setting atv.py changed (listed in settings-changed.txt),
#   - makes the original home screen the default again.
# Needs only adb. Usage:
#   sh restore.sh                 # the TV at the address saved below
#   sh restore.sh 192.168.1.50    # another address
#   sh restore.sh --remove-glass-home   # also uninstall Glass Home
# Nothing is ever uninstalled except, if you ask, Glass Home itself.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
ADB=${ADB:-adb}
SERIAL=@SERIAL@
REMOVE_GLASS=0
for a in "$@"; do
  case "$a" in
    --remove-glass-home) REMOVE_GLASS=1 ;;
    *:*) SERIAL=$a ;;
    *) SERIAL=$a:5555 ;;
  esac
done
case "$SERIAL" in *:*) "$ADB" connect "$SERIAL" >/dev/null 2>&1 ;; esac
if [ "$("$ADB" -s "$SERIAL" get-state 2>/dev/null)" != device ]; then
  echo "Can't reach the TV at $SERIAL. Turn it on, check Network debugging is on, accept the prompt, retry." >&2
  exit 1
fi
sh_tv() { "$ADB" -s "$SERIAL" shell "$@"; }

echo "Restoring $SERIAL from $HERE"
DISABLED=$(sh_tv pm list packages -d | tr -d '\r' | sed 's/^package://')
n=0; failed=0
while IFS= read -r pkg; do
  [ -n "$pkg" ] || continue
  if printf '%s\n' "$DISABLED" | grep -qxF "$pkg"; then
    sh_tv pm enable --user 0 "$pkg" >/dev/null 2>&1 || sh_tv pm enable "$pkg" >/dev/null 2>&1
    if sh_tv pm list packages -d | tr -d '\r' | grep -qxF "package:$pkg"; then
      echo "  ! could not re-enable $pkg"; failed=$((failed + 1))
    else
      echo "  re-enabled $pkg"; n=$((n + 1))
    fi
  fi
done < "$HERE/enabled-at-backup.txt"

s=0
if [ -f "$HERE/settings-changed.txt" ]; then
  while IFS=' ' read -r ns key old; do
    [ -n "$key" ] || continue
    if [ "$old" = "<unset>" ]; then sh_tv settings delete "$ns" "$key" >/dev/null 2>&1
    else sh_tv settings put "$ns" "$key" "$old" >/dev/null 2>&1; fi
    echo "  setting $ns $key -> $old"; s=$((s + 1))
  done < "$HERE/settings-changed.txt"
fi

HOME_COMP=$(head -n 1 "$HERE/home-at-backup.txt" 2>/dev/null | tr -d '\r')
case "$HOME_COMP" in
  */*ResolverActivity*|"") ;;
  */*)
    sh_tv cmd package set-home-activity --user 0 "$HOME_COMP" >/dev/null 2>&1 \
      || sh_tv cmd package set-home-activity "$HOME_COMP" >/dev/null 2>&1 \
      || sh_tv pm set-home-activity "$HOME_COMP" >/dev/null 2>&1
    echo "  home screen -> $HOME_COMP" ;;
esac
if [ "$REMOVE_GLASS" = 1 ]; then
  "$ADB" -s "$SERIAL" uninstall @GLASS@ >/dev/null 2>&1 && echo "  removed Glass Home"
fi
sh_tv input keyevent KEYCODE_HOME >/dev/null 2>&1
sleep 2
"$ADB" -s "$SERIAL" exec-out screencap -p > "$HERE/after-restore.png" 2>/dev/null \
  && echo "  screenshot: $HERE/after-restore.png"
echo "Done: $n apps re-enabled, $s settings restored, $failed failed."
[ "$failed" = 0 ]
'''


def write_restore_script(backup, serial):
    path = os.path.join(backup, 'restore.sh')
    with open(path, 'w') as f:
        f.write(RESTORE_SH.replace('@SERIAL@', shlex.quote(serial)).replace('@GLASS@', GLASS_PKG))
    os.chmod(path, 0o755)
    # A copy next to the TV's state, always the newest backup's.
    shutil.copy(path, os.path.join(os.path.dirname(os.path.dirname(backup)), 'restore.sh'))
    # restore.sh in state/<tv>/ must find the backup's files: point it at them.
    latest = os.path.join(os.path.dirname(os.path.dirname(backup)), 'restore.sh')
    with open(latest) as f:
        text = f.read()
    with open(latest, 'w') as f:
        f.write(text.replace('HERE=$(cd "$(dirname "$0")" && pwd)',
                             'HERE=$(cd "$(dirname "$0")/backups/%s" && pwd)' % os.path.basename(backup)))
    os.chmod(latest, 0o755)


def audit_data(tv):
    catalog = load_catalog()
    installed = tv.packages()
    enabled = set(installed) - tv.disabled()
    return catalog, installed, enabled, classify(installed, enabled, catalog)


def cmd_audit(args):
    tv = Tv(pick_serial(args))
    catalog, installed, enabled, found = audit_data(tv)
    log('%s: %d packages, %d enabled.\n' % (tv.device_id(), len(installed), len(enabled)))
    for g in ('ads', 'telemetry', 'junk', 'optional', 'suspect', 'launcher', 'keep'):
        title = GROUP_TITLES.get(g, {'launcher': 'Stock home screen (handled by install-home)',
                                     'keep': 'Known and kept'}.get(g))
        log('%s:' % title)
        if not found[g]:
            log('  (none)')
        for e in found[g]:
            log('  %-48s %s' % (e['package'], e['label']))
            if g not in ('keep',):
                log('  %-48s   %s' % ('', e['why']))
        log('')
    already = sorted(set(installed) - enabled)
    if already:
        log('Already disabled before atv.py: %s\n' % ', '.join(already))
    if args.all:
        known = set(catalog) | {e['package'] for e in found['suspect']}
        rest = [p for p in sorted(enabled) if p not in known]
        log('Everything else (not touched):')
        for p in rest:
            log('  %s%s' % (p, '  [protected]' if is_protected(p) else ''))
        log('')
    if args.write_plan:
        plan = {
            'device_id': tv.device_id(),
            'how': 'Set "disable" to true for each package to turn off, then run: atv.py debloat --plan THIS_FILE. '
                   'Ads, telemetry and junk default to true; everything you might use defaults to false.',
            'packages': [dict(package=e['package'], group=g, label=e['label'], why=e['why'],
                              disable=g in GROUPS)
                         for g in ('ads', 'telemetry', 'junk', 'optional', 'suspect') for e in found[g]],
            'speed': {'animation_scale': None,
                      'how': 'null leaves animations alone; 0.5 makes menus feel about twice as quick; 1 is normal.'},
            'dns': {'private_dns': None,
                    'how': 'null leaves DNS alone; "%s" blocks ad and tracking servers for the whole TV '
                           '(Android 9+). atv.py switches it back if the TV then cannot resolve names.' % AD_BLOCK_DNS},
        }
        with open(args.write_plan, 'w') as f:
            json.dump(plan, f, indent=2)
        log('Plan written to %s' % args.write_plan)
    return 0


def ask(prompt, choices='yn', default='n'):
    """Asks on the terminal. Choices are single letters; Enter picks the default."""
    hint = '/'.join(c.upper() if c == default else c for c in choices)
    while True:
        try:
            a = input('%s [%s] ' % (prompt, hint)).strip().lower()
        except EOFError:
            a = ''
        if not a:
            return default
        if a[0] in choices:
            return a[0]
        log('  please answer one of: %s' % ', '.join(choices))


def choose_interactively(found, sdk):
    """Asks which packages to disable. Returns (packages, animation_scale or None, dns host or None)."""
    chosen = []
    for g in GROUPS:
        items = found[g]
        if not items:
            continue
        log('\n%s:' % GROUP_TITLES[g])
        for e in items:
            log('  - %s (%s): %s' % (e['label'], e['package'], e['why']))
        a = ask('Disable %s? a = all, c = choose one by one, n = none' %
                ('it' if len(items) == 1 else 'these %d' % len(items)), 'acn', 'n')
        if a == 'a':
            chosen += [e['package'] for e in items]
        elif a == 'c':
            for e in items:
                if ask('  Disable %s?' % e['label'], 'yn', 'n') == 'y':
                    chosen.append(e['package'])
    for g in ('optional', 'suspect'):
        if found[g]:
            log('\n%s (one by one; Enter keeps it):' % GROUP_TITLES[g])
        for e in found[g]:
            name = e['package'] if e['label'] == e['package'] else '%s (%s)' % (e['label'], e['package'])
            log('  %s\n    %s' % (name, e['why']))
            if ask('  Disable it?', 'yn', 'n') == 'y':
                chosen.append(e['package'])
    log('\nAnimations: 1 is normal, 0.5 makes every menu and app switch feel about twice as quick.')
    scale = 0.5 if ask('Set animation speed to 0.5?', 'yn', 'n') == 'y' else None
    dns = None
    if sdk >= 28:
        log('\nAd blocking for the whole TV: Private DNS with %s (AdGuard DNS, free) stops apps and' % AD_BLOCK_DNS)
        log('the system reaching known ad and tracking servers. It can\'t remove ads inside video streams')
        log('(YouTube), and a few apps may miss a banner image. atv.py checks the TV still resolves names')
        log('afterwards and switches it back if not; restore.sh also undoes it.')
        if ask('Turn on ad-blocking DNS?', 'yn', 'n') == 'y':
            dns = AD_BLOCK_DNS
    return chosen, scale, dns


def set_private_dns(tv, ws, backup, host):
    """Switches Private DNS to `host`; reverts it if the TV can no longer resolve names."""
    old = {k: tv.setting('global', k) for k in ('private_dns_mode', 'private_dns_specifier')}
    for k, v in old.items():
        ws.record_setting(backup, 'global', k, v)
    tv.sh('settings put global private_dns_specifier %s' % shlex.quote(host))
    tv.sh('settings put global private_dns_mode hostname')
    ws.journal('setting', namespace='global', key='private_dns', old=old, new=host, backup=backup)
    log('  Private DNS: %s -> %s' % (old['private_dns_mode'], host))
    time.sleep(4)
    out = tv.sh('ping -c 1 -W 5 www.google.com 2>&1', check=False, timeout=30).lower()
    if 'unknown host' in out or 'bad address' in out:
        log('  ! the TV can\'t resolve names with it; switching back.')
        for k, v in old.items():
            tv.sh('settings delete global %s' % k if v == UNSET else
                  'settings put global %s %s' % (k, shlex.quote(v)), check=False)
        ws.journal('setting', namespace='global', key='private_dns', new=old, reason='resolution failed')
        return False
    if 'not found' in out or 'inaccessible' in out:
        log('  (this TV has no ping to check with; if apps lose internet, run restore.sh)')
    else:
        log('  names still resolve.')
    return True


def health(tv):
    """Problems that mean the last change broke something, or []. Unknown is not a problem."""
    problems = []
    if tv.home() == '':
        problems.append('nothing answers the Home button')
    if 'com.android.systemui' in tv.packages():
        out = tv.sh('pidof com.android.systemui || (ps -A 2>/dev/null || ps) | grep com.android.systemui',
                    check=False).strip()
        if not out:
            problems.append('the system UI is not running')
    return problems


def cmd_debloat(args):
    tv = Tv(pick_serial(args))
    ws = Workspace(tv)
    backup = ws.latest_backup()
    if not backup:
        log('No backup of this TV yet; making one first.')
        cmd_backup(argparse.Namespace(serial=tv.serial, apks=False))
        backup = ws.require_backup()
    catalog, installed, enabled, found = audit_data(tv)
    offered = {e['package']: e for g in ('ads', 'telemetry', 'junk', 'optional', 'suspect') for e in found[g]}

    if args.plan:
        with open(args.plan) as f:
            plan = json.load(f)
        if plan.get('device_id') and plan['device_id'] != tv.device_id():
            raise AtvError('that plan was written for %s, not this TV (%s)' % (plan['device_id'], tv.device_id()))
        chosen = [p['package'] for p in plan.get('packages', []) if p.get('disable')]
        scale = (plan.get('speed') or {}).get('animation_scale')
        dns = (plan.get('dns') or {}).get('private_dns')
    else:
        if not offered:
            log('No known ads, telemetry or junk is enabled on this TV.')
        chosen, scale, dns = choose_interactively(found, tv.sdk)

    refused = [p for p in chosen if p not in offered]
    for p in refused:
        why = ('protected (system, input, updates or setup)' if is_protected(p) else
               'the stock home screen: use install-home' if catalog.get(p, {}).get('group') == 'launcher' else
               'not installed or already disabled' if p not in enabled else
               'not in the catalogue and not ad/telemetry-like')
        log('  skipping %s: %s' % (p, why))
    chosen = [p for p in chosen if p in offered]
    if dns and tv.sdk < 28:
        log('  skipping Private DNS: it needs Android 9 or later')
        dns = None
    if not chosen and scale is None and not dns:
        log('Nothing chosen; no changes made.')
        return 0

    before_mem = tv.meminfo().get('MemAvailable', 0)
    before_procs = tv.process_count()
    ws.shot('before-debloat')
    log('\nDisabling %d package(s). Each can be turned back on with restore.sh.' % len(chosen))
    done, failed = [], []
    order = ('ads', 'telemetry', 'junk', 'suspect', 'optional')
    for g in order:
        batch = [p for p in chosen if offered[p]['group'] == g]
        if not batch:
            continue
        log('\n%s:' % GROUP_TITLES[g])
        group_done = []
        for p in batch:
            ok, out = tv.disable(p)
            if ok:
                log('  disabled %-46s %s' % (p, offered[p]['label']))
                ws.journal('disable', package=p, group=g, backup=backup)
                group_done.append(p)
            else:
                log('  ! %s could not be disabled: %s' % (p, out or 'the TV refused'))
                failed.append(p)
        problems = health(tv)
        if problems:
            log('  ! after this group: %s. Turning the group back on.' % '; '.join(problems))
            for p in group_done:
                tv.enable(p)
                ws.journal('enable', package=p, reason='health check failed: ' + '; '.join(problems))
            failed += group_done
            group_done = []
        done += group_done
        tv.key('HOME')
        time.sleep(2)
        ws.shot('after-' + g)

    if scale is not None:
        log('\nAnimation speed:')
        for key in ANIMATION_KEYS:
            old = tv.setting('global', key)
            ws.record_setting(backup, 'global', key, old)
            tv.sh('settings put global %s %s' % (key, scale))
            ws.journal('setting', namespace='global', key=key, old=old, new=str(scale), backup=backup)
            log('  %s: %s -> %s' % (key, old, scale))

    if dns:
        log('\nAd-blocking DNS:')
        if not set_private_dns(tv, ws, backup, dns):
            failed.append('private-dns')

    time.sleep(3)
    after_mem = tv.meminfo().get('MemAvailable', 0)
    after_procs = tv.process_count()
    log('\nDone: %d disabled, %d failed.' % (len(done), len(failed)))
    if before_mem and after_mem:
        log('Memory available: %d MB -> %d MB; processes: %d -> %d (more is freed after a restart).' % (
            before_mem // 1024, after_mem // 1024, before_procs, after_procs))
    log('Screenshots: %s' % ws.shots)
    log('Undo everything: sh %s' % os.path.join(backup, 'restore.sh'))
    return 0 if not failed else 2


def cmd_install_home(args):
    tv = Tv(pick_serial(args))
    ws = Workspace(tv)
    backup = ws.require_backup()
    apk = args.apk or DEFAULT_APK
    if not os.path.exists(apk):
        raise AtvError('%s not found. Build it: launcher/build.sh' % apk)
    original = tv.home()
    log('Installing %s' % apk)
    out = tv.cmd('install', '-r', apk, check=False, timeout=300)
    if 'Success' not in out:
        raise AtvError('install failed: %s' % out.strip())
    ws.journal('install', package=GLASS_PKG, apk=apk, backup=backup)

    # 1. It starts and draws.
    tv.sh('am start -W -n %s' % GLASS_HOME, check=False, timeout=60)
    time.sleep(3)
    if tv.focused_app() != GLASS_PKG:
        ws.shot('glass-home-failed-to-start')
        raise AtvError('Glass Home did not come to the front after starting it. Nothing else was changed; '
                       'see the screenshot.')
    ws.shot('glass-home-started')

    # 2. Make it the default home screen.
    if tv.set_home(GLASS_HOME):
        ws.journal('set-home', old=original, new=GLASS_HOME, backup=backup)
    tv.key('HOME')
    time.sleep(3)
    if tv.focused_app() == GLASS_PKG:
        log('Home button opens Glass Home.')
        ws.shot('home-button-glass-home')
        return finish_install(ws, backup)

    # 3. The stock home screen insisted (Google TV does). Offer to switch it off.
    stock = [e for e in classify(tv.packages(), set(tv.packages()) - tv.disabled(), load_catalog())['launcher']]
    if not stock:
        ws.shot('home-button')
        log('The Home button still opens %s, which atv.py does not know. Glass Home is installed and '
            'listed with your apps.' % tv.focused_app())
        return 0
    names = ', '.join('%s (%s)' % (e['label'], e['package']) for e in stock)
    log('The Home button still opens the stock home screen: %s.' % names)
    log('To make Glass Home the home screen, the stock one has to be disabled (not uninstalled).')
    log('Side effects: its suggestion rows are gone; on Google TV the remote\'s dedicated Home and')
    log('Google TV buttons open Glass Home; restore.sh turns the stock home screen back on.')
    decision = args.disable_stock
    if decision == 'ask':
        decision = 'yes' if ask('Disable the stock home screen?', 'yn', 'n') == 'y' else 'no'
    if decision != 'yes':
        log('Left as is. Glass Home is installed and opens from the apps list.')
        ws.shot('stock-home-kept')
        return 0
    for e in stock:
        ok, out = tv.disable(e['package'])
        if ok:
            ws.journal('disable', package=e['package'], group='launcher', backup=backup)
            log('  disabled %s' % e['package'])
        else:
            log('  ! %s could not be disabled: %s' % (e['package'], out or 'refused'))
    tv.set_home(GLASS_HOME)
    tv.key('HOME')
    time.sleep(3)
    if tv.focused_app() == GLASS_PKG and tv.home():
        log('Home button opens Glass Home.')
        ws.shot('home-button-glass-home')
        return finish_install(ws, backup)
    # Never leave the TV without a working home screen.
    log('  ! the Home button did not open Glass Home; turning the stock home screen back on.')
    for e in stock:
        tv.enable(e['package'])
        ws.journal('enable', package=e['package'], reason='Glass Home did not take over Home')
    if original:
        tv.set_home(original)
    tv.key('HOME')
    time.sleep(2)
    ws.shot('stock-home-restored')
    return 2


def finish_install(ws, backup):
    tv = ws.tv
    for k in ('DPAD_RIGHT', 'DPAD_RIGHT'):
        tv.key(k)
        time.sleep(0.6)
    time.sleep(1)
    ws.shot('glass-home-dock-focus-moved')
    tv.key('HOME')
    log('Glass Home is the home screen. Hold OK on a tile to move or remove it; "Apps" lists everything.')
    log('Undo: sh %s  (add --remove-glass-home to uninstall it too)' % os.path.join(backup, 'restore.sh'))
    return 0


def cmd_photos(args):
    tv = Tv(pick_serial(args))
    ws = Workspace(tv)
    tv.sh('mkdir -p %s' % OWN_PHOTOS)
    for path in args.files:
        if not re.search(r'\.(jpe?g|png|webp)$', path, re.I):
            log('  skipping %s (JPEG, PNG or WebP only)' % path)
            continue
        tv.cmd('push', path, OWN_PHOTOS + '/' + os.path.basename(path), timeout=300)
        log('  added %s' % os.path.basename(path))
    tv.sh('am force-stop %s' % GLASS_PKG, check=False)
    tv.key('HOME')
    time.sleep(3)
    ws.shot('photos-added')
    return 0


def cmd_restore(args):
    tv = Tv(pick_serial(args))
    ws = Workspace(tv)
    backup = args.backup or ws.require_backup()
    with open(os.path.join(backup, 'backup.json')) as f:
        data = json.load(f)
    if data.get('device_id') != tv.device_id():
        raise AtvError('that backup is of %s, not this TV (%s)' % (data.get('device_id'), tv.device_id()))
    log('Restoring from %s' % backup)
    ws.shot('before-restore')
    was_enabled = set(data['packages']) - set(data['disabled'])
    now_disabled = tv.disabled()
    failed = 0
    for p in sorted(was_enabled & now_disabled):
        ok, out = tv.enable(p)
        log('  %s %s' % ('re-enabled' if ok else '! could not re-enable', p))
        failed += 0 if ok else 1
        if ok:
            ws.journal('enable', package=p, reason='restore')
    path = os.path.join(backup, 'settings-changed.txt')
    if os.path.exists(path):
        for line in open(path):
            parts = line.rstrip('\n').split(' ', 2)
            if len(parts) < 3:
                continue
            ns, key, old = parts
            if old == UNSET:
                tv.sh('settings delete %s %s' % (ns, key), check=False)
            else:
                tv.sh('settings put %s %s %s' % (ns, key, shlex.quote(old)), check=False)
            ws.journal('setting', namespace=ns, key=key, new=old, reason='restore')
            log('  setting %s %s -> %s' % (ns, key, old))
    home = data.get('home') or ''
    if '/' in home and 'ResolverActivity' not in home:
        tv.set_home(home)
        log('  home screen -> %s' % home)
    if args.remove_glass_home:
        out = tv.cmd('uninstall', GLASS_PKG, check=False)
        log('  removed Glass Home' if 'Success' in out else '  Glass Home was not installed')
    tv.key('HOME')
    time.sleep(3)
    ws.shot('after-restore')
    log('Done%s.' % ('' if not failed else ', %d failed' % failed))
    return 0 if not failed else 2


# --------------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser(prog='atv.py', description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('-s', '--serial', help='the TV (ip:port); default: the last one connected')
    sub = ap.add_subparsers(dest='command', required=True)

    p = sub.add_parser('discover', help='find TVs on this network')
    p.add_argument('--subnet', help='e.g. 192.168.1.0/24 (default: this computer\'s /24)')
    p.add_argument('--timeout', type=float, default=0.5)
    p.set_defaults(fn=cmd_discover)

    p = sub.add_parser('pair', help='pair with Android 11+ "Wireless debugging" (ip:port and 6-digit code)')
    p.add_argument('address')
    p.add_argument('code')
    p.set_defaults(fn=cmd_pair)

    p = sub.add_parser('connect', help='connect and remember the TV')
    p.add_argument('address', help='ip or ip:port')
    p.add_argument('--wait', type=int, default=90, help='seconds to wait for you to accept on the TV')
    p.set_defaults(fn=cmd_connect)

    p = sub.add_parser('info', help='model, Android version, memory, home screen')
    p.set_defaults(fn=cmd_info)

    p = sub.add_parser('shot', help='take a screenshot from the TV')
    p.add_argument('name', nargs='?', default='screen')
    p.set_defaults(fn=cmd_shot)

    p = sub.add_parser('key', help='press remote keys, e.g. HOME DPAD_RIGHT ENTER BACK')
    p.add_argument('keys', nargs='+')
    p.add_argument('--shot', action='store_true', help='screenshot afterwards')
    p.set_defaults(fn=cmd_key)

    p = sub.add_parser('backup', help='back up the package and settings state and write restore.sh')
    p.add_argument('--apks', action='store_true', help='also copy every installed app\'s APK (slow, large)')
    p.set_defaults(fn=cmd_backup)

    p = sub.add_parser('audit', help='list what can be disabled')
    p.add_argument('--write-plan', metavar='FILE', help='write a plan to review and pass to debloat --plan')
    p.add_argument('--all', action='store_true', help='also list every other enabled package')
    p.set_defaults(fn=cmd_audit)

    p = sub.add_parser('debloat', help='disable ads, telemetry and junk (asks first)')
    p.add_argument('--plan', metavar='FILE', help='use a reviewed plan instead of asking')
    p.set_defaults(fn=cmd_debloat)

    p = sub.add_parser('install-home', help='install Glass Home and make it the home screen')
    p.add_argument('--apk', help='default: launcher/build/glass-home.apk')
    p.add_argument('--disable-stock', choices=('ask', 'yes', 'no'), default='ask',
                   help='if the stock home screen keeps the Home button (default: ask)')
    p.set_defaults(fn=cmd_install_home)

    p = sub.add_parser('photos', help='add your own photos to Glass Home')
    p.add_argument('files', nargs='+')
    p.set_defaults(fn=cmd_photos)

    p = sub.add_parser('restore', help='undo: back to the latest (or a given) backup')
    p.add_argument('backup', nargs='?', help='a backup folder (default: the latest)')
    p.add_argument('--remove-glass-home', action='store_true', help='also uninstall Glass Home')
    p.set_defaults(fn=cmd_restore)

    args = ap.parse_args(argv)
    try:
        return args.fn(args) or 0
    except AtvError as e:
        print('atv.py: %s' % e, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('\nStopped. Anything already changed is in the journal; restore.sh undoes it.', file=sys.stderr)
        return 130


if __name__ == '__main__':
    sys.exit(main())
