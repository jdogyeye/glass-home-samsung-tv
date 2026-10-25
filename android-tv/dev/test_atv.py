#!/usr/bin/env python3
"""
Tests atv.py and its restore.sh end to end against dev/fake_adb.py (a pretend
Google TV). No TV or real adb needed:  python3 android-tv/dev/test_atv.py
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ATV = os.path.join(HERE, '..', 'atv.py')
FAKE = os.path.join(HERE, 'fake_adb.py')

PROTECTED_SAMPLE = ['android', 'com.android.systemui', 'com.android.tv.settings', 'com.google.android.gms',
                    'com.android.vending', 'com.google.android.leanback.ime', 'com.android.providers.tv',
                    'com.google.android.tv.remote.service', 'com.google.android.tungsten.setupwraith']


class Tv(object):
    def __init__(self, tmp):
        self.tmp = tmp
        self.state_file = os.path.join(tmp, 'fake.json')
        self.env = dict(os.environ, ADB=FAKE, ATV_STATE=os.path.join(tmp, 'atv-state'),
                        FAKE_ADB_STATE=self.state_file)
        self.apk = os.path.join(tmp, 'glass-home.apk')
        with open(self.apk, 'wb') as f:
            f.write(b'not really an apk')

    def atv(self, *args, stdin=''):
        p = subprocess.run([sys.executable, ATV] + list(args), env=self.env, input=stdin,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, timeout=120)
        return p.returncode, p.stdout

    def state(self):
        with open(self.state_file) as f:
            return json.load(f)

    def patch(self, **kw):
        st = self.state()
        st.update(kw)
        with open(self.state_file, 'w') as f:
            json.dump(st, f)

    def calls(self):
        with open(self.state_file + '.log') as f:
            return [json.loads(l) for l in f]

    def disable_calls(self):
        return [c[-1] for c in self.calls() if 'disable-user' in ' '.join(c)]

    def tv_dir(self):
        root = self.env['ATV_STATE']
        return os.path.join(root, [d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d))][0])

    def backup_dir(self):
        b = os.path.join(self.tv_dir(), 'backups')
        return os.path.join(b, sorted(os.listdir(b))[-1])

    def shots(self):
        return sorted(os.listdir(os.path.join(self.tv_dir(), 'shots')))


class AtvTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tv = Tv(self._tmp.name)
        code, out = self.tv.atv('connect', '192.168.1.50')
        self.assertEqual(code, 0, out)
        self.assertIn('Chromecast', out)

    def tearDown(self):
        self._tmp.cleanup()

    def test_full_flow_with_a_reviewed_plan_then_restore(self):
        tv = self.tv
        # Nothing changes before a backup exists.
        code, out = tv.atv('install-home', '--apk', tv.apk)
        self.assertEqual(code, 1, out)
        self.assertIn('no backup', out)

        code, out = tv.atv('backup')
        self.assertEqual(code, 0, out)
        b = tv.backup_dir()
        for f in ('backup.json', 'restore.sh', 'enabled-at-backup.txt', 'home-at-backup.txt', 'before.png'):
            self.assertTrue(os.path.exists(os.path.join(b, f)), f)
        baseline_disabled = tv.state()['disabled']
        self.assertEqual(baseline_disabled, ['com.android.egg'])

        plan_path = os.path.join(tv.tmp, 'plan.json')
        code, out = tv.atv('audit', '--write-plan', plan_path)
        self.assertEqual(code, 0, out)
        with open(plan_path) as f:
            plan = json.load(f)
        by = {p['package']: p for p in plan['packages']}
        self.assertEqual(by['com.google.android.adservices.api']['group'], 'ads')
        self.assertEqual(by['com.google.android.feedback']['group'], 'telemetry')
        self.assertEqual(by['com.android.printspooler']['group'], 'junk')
        self.assertEqual(by['com.google.android.katniss']['group'], 'optional')
        self.assertEqual(by['com.example.vendor.adtracker']['group'], 'suspect')
        self.assertTrue(by['com.android.printspooler']['disable'])
        self.assertFalse(by['com.google.android.katniss']['disable'], 'things you might use default to No')
        self.assertFalse(by['com.example.vendor.adtracker']['disable'], 'unknown packages default to No')
        for absent in ['com.example.vendor.photos', 'com.android.egg',
                       'com.google.android.apps.tv.launcherx'] + PROTECTED_SAMPLE:
            self.assertNotIn(absent, by, absent)

        # The user also says yes to Ambient mode; a tampered plan tries protected things.
        by['com.google.android.apps.tv.dreamx']['disable'] = True
        plan['packages'] += [dict(package=p, disable=True) for p in PROTECTED_SAMPLE]
        plan['packages'].append(dict(package='com.google.android.apps.tv.launcherx', disable=True))
        plan['packages'].append(dict(package='com.example.vendor.photos', disable=True))
        plan['speed']['animation_scale'] = 0.5
        plan['dns']['private_dns'] = 'dns.adguard-dns.com'
        with open(plan_path, 'w') as f:
            json.dump(plan, f)

        code, out = tv.atv('debloat', '--plan', plan_path)
        self.assertEqual(code, 2, out)   # the TV refused one package
        self.assertIn('com.google.android.tv.bugreportsender could not be disabled', out)
        st = tv.state()
        for p in ('com.google.android.adservices.api', 'com.google.android.feedback', 'com.android.printspooler',
                  'com.android.bips', 'com.google.android.apps.tv.dreamx'):
            self.assertIn(p, st['disabled'], p)
        for p in PROTECTED_SAMPLE + ['com.google.android.katniss', 'com.example.vendor.adtracker',
                                     'com.example.vendor.photos', 'com.google.android.apps.tv.launcherx']:
            self.assertNotIn(p, st['disabled'], p)
            self.assertNotIn(p, tv.disable_calls(), 'never even tried: ' + p)
        self.assertEqual(st['settings']['global']['window_animation_scale'], '0.5')
        self.assertEqual(st['settings']['global']['transition_animation_scale'], '0.5')
        self.assertEqual(st['settings']['global']['private_dns_mode'], 'hostname')
        self.assertEqual(st['settings']['global']['private_dns_specifier'], 'dns.adguard-dns.com')
        self.assertIn('names still resolve', out)
        shots = tv.shots()
        for name in ('before-debloat', 'after-ads', 'after-telemetry', 'after-junk', 'after-optional'):
            self.assertTrue(any(name in s for s in shots), (name, shots))
        with open(os.path.join(tv.tv_dir(), 'journal.jsonl')) as f:
            journal = [json.loads(l) for l in f]
        self.assertTrue(any(j['action'] == 'disable' and j['package'] == 'com.android.bips' for j in journal))
        self.assertTrue(any(j['action'] == 'setting' and j['old'] == '<unset>' for j in journal))
        self.assertNotIn('uninstall', json.dumps(tv.calls()), 'debloat never uninstalls')

        # Glass Home: the Google TV launcher keeps Home until it is disabled.
        code, out = tv.atv('install-home', '--apk', tv.apk, '--disable-stock', 'yes')
        self.assertEqual(code, 0, out)
        self.assertIn('Home button opens Glass Home', out)
        st = tv.state()
        self.assertIn('com.google.android.apps.tv.launcherx', st['disabled'])
        self.assertEqual(st['foreground'], 'app.glasshome.tv')
        self.assertTrue(any('glass-home-started' in s for s in tv.shots()))
        self.assertTrue(any('home-button-glass-home' in s for s in tv.shots()))

        # The standalone restore script puts everything back.
        p = subprocess.run(['sh', os.path.join(b, 'restore.sh')], env=tv.env, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, universal_newlines=True, timeout=120)
        self.assertEqual(p.returncode, 0, p.stdout)
        st = tv.state()
        self.assertEqual(sorted(st['disabled']), baseline_disabled, p.stdout)
        g = st['settings']['global']
        self.assertEqual(g['window_animation_scale'], '1.0')
        self.assertEqual(g['animator_duration_scale'], '1.0')
        self.assertNotIn('transition_animation_scale', g, 'a setting that was unset is unset again')
        self.assertNotIn('private_dns_mode', g)
        self.assertNotIn('private_dns_specifier', g)
        self.assertEqual(st['preferred_home'], 'com.google.android.apps.tv.launcherx/.MainActivity')
        self.assertEqual(st['foreground'], 'com.google.android.apps.tv.launcherx')
        self.assertIn('app.glasshome.tv', st['packages'], 'Glass Home stays unless asked')
        self.assertTrue(os.path.exists(os.path.join(b, 'after-restore.png')))

        # The copy in state/<tv>/ finds the same backup, and can remove Glass Home.
        p = subprocess.run(['sh', os.path.join(tv.tv_dir(), 'restore.sh'), '--remove-glass-home'], env=tv.env,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, timeout=120)
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertNotIn('app.glasshome.tv', tv.state()['packages'])

    def test_interactive_asks_and_respects_no(self):
        tv = self.tv
        self.assertEqual(tv.atv('backup')[0], 0)
        # ads: all; telemetry: choose (feedback yes, bug reports no); junk: none;
        # then Enter (No) for every app you might use, and for animations.
        code, out = tv.atv('debloat', stdin='a\nc\ny\nn\nn\n')
        self.assertEqual(code, 0, out)
        st = tv.state()
        self.assertIn('com.google.android.adservices.api', st['disabled'])
        self.assertIn('com.google.android.feedback', st['disabled'])
        for p in ('com.google.android.tv.bugreportsender', 'com.android.printspooler', 'com.google.android.katniss',
                  'com.google.android.apps.mediashell', 'com.netflix.ninja', 'com.example.vendor.adtracker'):
            self.assertNotIn(p, st['disabled'], p)
        self.assertEqual(st['settings']['global']['window_animation_scale'], '1.0')
        self.assertIn('Voice search', out)   # it explained what each optional app does

    def test_debloat_makes_a_backup_first_if_missing(self):
        code, out = self.tv.atv('debloat', stdin='n\nn\nn\n')
        self.assertEqual(code, 0, out)
        self.assertIn('making one first', out)
        self.assertTrue(os.path.exists(os.path.join(self.tv.backup_dir(), 'restore.sh')))

    def test_install_home_changes_nothing_if_glass_home_does_not_start(self):
        tv = self.tv
        tv.atv('backup')
        tv.patch(glass_crashes=True)
        code, out = tv.atv('install-home', '--apk', tv.apk, '--disable-stock', 'yes')
        self.assertEqual(code, 1, out)
        self.assertIn('did not come to the front', out)
        st = tv.state()
        self.assertNotIn('com.google.android.apps.tv.launcherx', st['disabled'])
        self.assertIsNone(st['preferred_home'])

    def test_install_home_rolls_back_if_tv_would_have_no_home(self):
        tv = self.tv
        tv.atv('backup')
        tv.patch(glass_not_home=True)
        code, out = tv.atv('install-home', '--apk', tv.apk, '--disable-stock', 'yes')
        self.assertEqual(code, 2, out)
        self.assertIn('turning the stock home screen back on', out)
        st = tv.state()
        self.assertNotIn('com.google.android.apps.tv.launcherx', st['disabled'])
        self.assertEqual(st['foreground'], 'com.google.android.apps.tv.launcherx')

    def test_install_home_asks_and_no_keeps_stock(self):
        tv = self.tv
        tv.atv('backup')
        code, out = tv.atv('install-home', '--apk', tv.apk, stdin='n\n')
        self.assertEqual(code, 0, out)
        self.assertIn('Disable the stock home screen?', out)
        self.assertNotIn('com.google.android.apps.tv.launcherx', tv.state()['disabled'])

    def test_old_android_without_cmd_is_not_mistaken_for_broken(self):
        tv = self.tv
        tv.patch(sdk=23)
        tv.atv('backup')
        code, out = tv.atv('debloat', stdin='a\nn\nn\n')
        self.assertEqual(code, 0, out)
        self.assertNotIn('Turning the group back on', out)
        self.assertIn('com.google.android.adservices.api', tv.state()['disabled'])

    def test_dns_that_breaks_the_internet_is_switched_back(self):
        tv = self.tv
        tv.atv('backup')
        plan = os.path.join(tv.tmp, 'dns.json')
        with open(plan, 'w') as f:
            json.dump({'packages': [], 'dns': {'private_dns': 'broken.example'}}, f)
        code, out = tv.atv('debloat', '--plan', plan)
        self.assertEqual(code, 2, out)
        self.assertIn('switching back', out)
        g = tv.state()['settings']['global']
        self.assertNotIn('private_dns_mode', g)
        self.assertNotIn('private_dns_specifier', g)

    def test_plan_for_another_tv_is_refused(self):
        tv = self.tv
        tv.atv('backup')
        plan = os.path.join(tv.tmp, 'other.json')
        with open(plan, 'w') as f:
            json.dump({'device_id': 'SomeOtherTV-123', 'packages': []}, f)
        code, out = tv.atv('debloat', '--plan', plan)
        self.assertEqual(code, 1, out)
        self.assertIn('not this TV', out)

    def test_python_restore_matches_script(self):
        tv = self.tv
        tv.atv('backup')
        tv.atv('debloat', stdin='a\na\na\n')
        self.assertGreater(len(tv.state()['disabled']), 3)
        code, out = tv.atv('restore')
        self.assertEqual(code, 0, out)
        self.assertEqual(tv.state()['disabled'], ['com.android.egg'])

    def test_discover_finds_an_adb_port(self):
        srv = socket.socket()
        srv.bind(('127.0.0.1', 0))
        srv.listen(5)
        port = srv.getsockname()[1]
        try:
            env = dict(self.tv.env, ATV_DISCOVER_PORTS=str(port))
            p = subprocess.run([sys.executable, ATV, 'discover', '--subnet', '127.0.0.1/32'], env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, timeout=60)
            self.assertEqual(p.returncode, 0, p.stdout)
            self.assertIn('127.0.0.1', p.stdout)
        finally:
            srv.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
