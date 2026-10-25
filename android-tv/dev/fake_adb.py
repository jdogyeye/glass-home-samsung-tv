#!/usr/bin/env python3
"""
A pretend `adb` with a pretend Google TV behind it, for testing atv.py and
restore.sh without a TV. State lives in the JSON file named by
$FAKE_ADB_STATE (created on first use); every call is appended to
$FAKE_ADB_STATE.log.

The TV behaves like the awkward real ones:
  - its stock launcher (Google TV) keeps the Home button even after another
    home screen is set as default, until it is disabled ("stubborn");
  - it refuses to disable one package (as some makers do);
  - one animation setting starts unset; one app is already disabled.
Switches for failure tests (set in the state file): "sdk" (Android API level),
"glass_crashes" (Glass Home never reaches the front), "glass_not_home"
(Glass Home can't be a home screen).
"""
import json
import os
import random
import re
import struct
import sys
import zlib

STATE = os.environ.get('FAKE_ADB_STATE', '/tmp/fake-adb-state.json')
LAUNCHERX = 'com.google.android.apps.tv.launcherx'
GLASS = 'app.glasshome.tv'

PACKAGES = [
    'android', 'com.android.systemui', 'com.android.tv.settings', 'com.android.providers.settings',
    'com.android.providers.tv', 'com.android.shell', 'com.google.android.gms', 'com.google.android.gsf',
    'com.android.vending', 'com.google.android.leanback.ime', 'com.google.android.tv.remote.service',
    'com.google.android.tungsten.setupwraith', LAUNCHERX, 'com.google.android.tvrecommendations',
    'com.google.android.katniss', 'com.google.android.apps.mediashell', 'com.google.android.feedback',
    'com.google.android.tv.bugreportsender', 'com.android.printspooler', 'com.android.bips',
    'com.google.android.adservices.api', 'com.google.android.apps.tv.dreamx', 'com.google.android.youtube.tv',
    'com.google.android.youtube.tvmusic', 'com.netflix.ninja', 'com.amazon.amazonvideo.livingroom',
    'com.disney.disneyplus', 'com.spotify.tv.android', 'tv.pluto.android', 'com.google.android.videos',
    'com.android.egg', 'com.example.vendor.adtracker', 'com.example.vendor.photos',
]
HOME_ACTIVITIES = {LAUNCHERX: LAUNCHERX + '/.MainActivity', GLASS: GLASS + '/' + GLASS + '.HomeActivity'}


def fresh():
    return {
        'packages': list(PACKAGES),
        'disabled': ['com.android.egg'],
        'third_party': ['com.netflix.ninja', 'com.disney.disneyplus', 'com.spotify.tv.android'],
        'settings': {'global': {'window_animation_scale': '1.0', 'animator_duration_scale': '1.0',
                                'adb_enabled': '1'},
                     'secure': {'android_id': 'abc123'}, 'system': {'screen_brightness': '128'}},
        'preferred_home': None,
        'foreground': LAUNCHERX,
        'stubborn': True,
        'refuse_disable': ['com.google.android.tv.bugreportsender'],
        'connected': [],
        'unauthorized_polls': 0,
        'sdk': 31,
        'glass_crashes': False,
        'glass_not_home': False,
    }


def load():
    if os.path.exists(STATE):
        with open(STATE) as f:
            return json.load(f)
    return fresh()


def save(st):
    with open(STATE, 'w') as f:
        json.dump(st, f, indent=1)


def enabled(st):
    return [p for p in st['packages'] if p not in st['disabled']]


def resolve_home(st):
    en = set(enabled(st))
    if st['stubborn'] and LAUNCHERX in en:
        return HOME_ACTIVITIES[LAUNCHERX]
    pref = st['preferred_home']
    if pref and pref.split('/')[0] in en and not (pref.startswith(GLASS) and st.get('glass_not_home')):
        return pref
    for pkg, comp in HOME_ACTIVITIES.items():
        if pkg in en and not (pkg == GLASS and st.get('glass_not_home')):
            return comp
    return ''


def png(seed):
    """A noisy 200 x 120 PNG, like a real (non-black) screenshot."""
    rnd = random.Random(seed)
    w, h = 200, 120
    raw = b''.join(b'\x00' + bytes(rnd.getrandbits(8) for _ in range(w * 3)) for _ in range(h))

    def chunk(tag, data):
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data) & 0xffffffff)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(raw, 1)) + chunk(b'IEND', b''))


def shell(st, cmd):
    out = []
    p = lambda s='': out.append(s)
    m = None

    def has(pattern):
        nonlocal m
        m = re.match(pattern, cmd)
        return m is not None

    if cmd == 'getprop':
        for k, v in (('ro.product.manufacturer', 'Google'), ('ro.product.model', 'Chromecast'),
                     ('ro.product.device', 'sabrina'), ('ro.product.brand', 'google'),
                     ('ro.build.version.release', '12'), ('ro.build.version.sdk', str(st.get('sdk', 31))),
                     ('ro.build.characteristics', 'tv'), ('ro.serialno', 'FAKE0001'),
                     ('ro.build.fingerprint', 'google/sabrina/sabrina:12/fake')):
            p('[%s]: [%s]' % (k, v))
    elif has(r'pm list packages ?(-[deus3])?$'):
        flag = m.group(1)
        pk = {None: st['packages'], '-d': st['disabled'], '-e': enabled(st), '-u': st['packages'],
              '-3': st['third_party'], '-s': [x for x in st['packages'] if x not in st['third_party']]}[flag]
        for x in pk:
            p('package:' + x)
    elif cmd == 'pm list features':
        p('feature:android.software.leanback')
        p('feature:android.hardware.type.television')
    elif has(r'pm disable-user --user 0 (\S+)$'):
        pkg = m.group(1)
        if pkg not in st['packages']:
            p('Error: Unknown package: ' + pkg)
        elif pkg in st['refuse_disable']:
            p('Error: java.lang.SecurityException: Shell cannot change component state for %s to 3' % pkg)
        else:
            if pkg not in st['disabled']:
                st['disabled'].append(pkg)
            if st['foreground'] == pkg:
                st['foreground'] = resolve_home(st).split('/')[0]
            p('Package %s new state: disabled-user' % pkg)
    elif has(r'pm enable (?:--user 0 )?(\S+)$'):
        pkg = m.group(1)
        if pkg in st['disabled']:
            st['disabled'].remove(pkg)
        p('Package %s new state: enabled' % pkg)
    elif has(r'pm path (\S+)$'):
        p('package:/data/app/%s/base.apk' % m.group(1))
    elif has(r'settings list (\w+)$'):
        for k, v in sorted(st['settings'].get(m.group(1), {}).items()):
            p('%s=%s' % (k, v))
    elif has(r'settings get (\w+) (\S+)$'):
        p(st['settings'].get(m.group(1), {}).get(m.group(2), 'null'))
    elif has(r"settings put (\w+) (\S+) '?([^']*)'?$"):
        st['settings'].setdefault(m.group(1), {})[m.group(2)] = m.group(3)
    elif has(r'settings delete (\w+) (\S+)$'):
        st['settings'].get(m.group(1), {}).pop(m.group(2), None)
        p('Deleted 1 rows')
    elif cmd.startswith('cmd package') and st.get('sdk', 31) < 24:
        sys.stderr.write("/system/bin/sh: cmd: not found\n")
        return '', 127
    elif cmd.startswith('cmd package resolve-activity') and 'category.HOME' in cmd:
        comp = resolve_home(st)
        if comp:
            p('priority=0 preferredOrder=0 match=0x108000 specificIndex=-1 isDefault=true')
            p(comp)
        else:
            p('No activity found')
    elif has(r'(?:cmd package|pm) set-home-activity (?:--user 0 )?(\S+)$'):
        comp = m.group(1).strip("'")
        if comp.split('/')[0] not in st['packages']:
            p('Error: Unknown component')
        else:
            st['preferred_home'] = comp
            p('Success')
    elif has(r'input keyevent (\S+)$'):
        if m.group(1) in ('KEYCODE_HOME', '3'):
            st['foreground'] = resolve_home(st).split('/')[0]
    elif cmd == 'dumpsys window':
        fg = st['foreground']
        if fg:
            p('  mCurrentFocus=Window{1a2b3c u0 %s/%s.Main}' % (fg, fg))
            p('  mFocusedApp=ActivityRecord{4d5e u0 %s/.Main t12}' % fg)
        else:
            p('  mCurrentFocus=null')
    elif cmd == 'cat /proc/meminfo':
        total = 2000000
        avail = 600000 + 20000 * len(st['disabled'])
        p('MemTotal:        %d kB' % total)
        p('MemFree:         %d kB' % (avail // 2))
        p('MemAvailable:    %d kB' % avail)
    elif cmd.startswith('ps'):
        p('USER PID PPID VSZ RSS WCHAN ADDR S NAME')
        for i, x in enumerate(enabled(st)):
            p('u0_a%d %d 1 0 0 0 0 S %s' % (i, 1000 + i, x))
    elif cmd.startswith('pidof com.android.systemui'):
        if 'com.android.systemui' in enabled(st):
            p('1234')
    elif has(r'am start (?:-W )?-n (\S+)$'):
        pkg = m.group(1).split('/')[0]
        if pkg == GLASS and st.get('glass_crashes'):
            p('Status: ok')   # starts, then dies: the launcher stays in front
        elif pkg in enabled(st):
            st['foreground'] = pkg
            p('Status: ok')
        else:
            p('Error: Activity not started, unable to resolve Intent')
    elif has(r'am force-stop (\S+)$'):
        if st['foreground'] == m.group(1):
            st['foreground'] = ''
    elif cmd.startswith('ping '):
        g = st['settings'].get('global', {})
        if g.get('private_dns_mode') == 'hostname' and g.get('private_dns_specifier') == 'broken.example':
            p('ping: unknown host www.google.com')
        else:
            p('PING www.google.com (142.250.1.1) 56(84) bytes of data.')
            p('1 packets transmitted, 1 received, 0% packet loss')
    elif cmd.startswith(('mkdir ', 'rm ', 'screencap ')):
        pass
    else:
        sys.stderr.write('fake adb: unknown shell command: %s\n' % cmd)
        return '\n'.join(out), 1
    return '\n'.join(out) + ('\n' if out else ''), 0


def main(argv):
    st = load()
    with open(STATE + '.log', 'a') as f:
        f.write(json.dumps(argv) + '\n')
    args = list(argv)
    if args[:1] == ['-s']:
        args = args[2:]   # one pretend TV, whatever the serial
    if not args:
        return 1
    c = args[0]
    code = 0
    out = ''
    if c == 'version':
        out = 'Android Debug Bridge version 1.0.41 (fake)\n'
    elif c == 'connect':
        st['connected'].append(args[1])
        out = 'connected to %s\n' % args[1]
    elif c == 'pair':
        out = 'Successfully paired to %s [guid=adb-fake]\n' % args[1]
    elif c == 'mdns':
        out = 'List of discovered mdns services\n'
    elif c == 'devices':
        out = 'List of devices attached\n' + ''.join('%s\tdevice\n' % d for d in st['connected'])
    elif c == 'get-state':
        if st['unauthorized_polls'] > 0:
            st['unauthorized_polls'] -= 1
            sys.stderr.write('error: device unauthorized.\n')
            out, code = 'unauthorized\n', 1
        else:
            out = 'device\n'
    elif c == 'shell':
        out, code = shell(st, ' '.join(args[1:]))
    elif c == 'exec-out':
        if args[1:3] == ['screencap', '-p'] or args[1] == 'cat':
            save(st)
            sys.stdout.buffer.write(png(st['foreground'] + str(len(st['disabled']))))
            return 0
    elif c == 'install':
        apk = args[-1]
        if not os.path.exists(apk):
            out, code = 'adb: failed to stat %s: No such file or directory\n' % apk, 1
        else:
            if GLASS not in st['packages']:
                st['packages'].append(GLASS)
                st['third_party'].append(GLASS)
            out = 'Performing Streamed Install\nSuccess\n'
    elif c == 'uninstall':
        pkg = args[1]
        if pkg in st['packages']:
            st['packages'].remove(pkg)
            st['third_party'] = [x for x in st['third_party'] if x != pkg]
            if st['preferred_home'] and st['preferred_home'].startswith(pkg + '/'):
                st['preferred_home'] = None
            out = 'Success\n'
        else:
            out, code = 'Failure [DELETE_FAILED_INTERNAL_ERROR]\n', 1
    elif c == 'push':
        out = '%s: 1 file pushed.\n' % args[1]
    elif c == 'pull':
        open(args[2], 'wb').write(b'fake apk')
        out = '1 file pulled.\n'
    else:
        sys.stderr.write('fake adb: unknown command %s\n' % c)
        code = 1
    save(st)
    sys.stdout.write(out)
    return code


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
