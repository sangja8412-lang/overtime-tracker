#!/usr/bin/env python3
"""네이티브 브리지를 가짜로 주입해 iOS 앱 경로를 검증 (헤드리스 Chromium).

실제 아이폰 없이 확인 가능한 것:
 - 앱(네이티브) 모드에서 권한 상태/회사 위치 화면이 플러그인 응답으로 그려지는지
 - 현재 위치로 저장 → setGeofence(latitude/longitude/radius) 호출
 - 감시 중지 → clearGeofence 호출
 - 알림 테스트 → testNotification(type) 호출
 - 앱 시작 시 getPendingAction 으로 알림 탭 payload 를 받아 배너/프리필로 이어지는지
 - CSV 저장 → Filesystem.writeFile + Share.share 경로 사용
 - 저장 시 clearDelivered 호출

실행: PLAYWRIGHT_BROWSERS_PATH=/workspace/work/pw-browsers python3 tests/verify-bridge-mock.py
"""
import functools
import http.server
import json
import os
import socketserver
import threading

from playwright.sync_api import sync_playwright

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WWW = os.path.join(ROOT, 'www')
PORT = 8124
URL = f'http://127.0.0.1:{PORT}/index.html'
SHOTS = os.path.join(ROOT, 'tests', 'screens')

results = []


def check(name, ok, detail=''):
    results.append((name, bool(ok), detail))
    print(('PASS  ' if ok else 'FAIL  ') + name + (f'  — {detail}' if detail else ''))


FAKE_NATIVE = r"""
(() => {
  const calls = [];
  window.__calls = calls;
  const pending = { type: 'out', timestamp: '2026-11-25T18:31:00+09:00',
                    epoch: Date.parse('2026-11-25T18:31:00+09:00'), date: '2026-11-25',
                    action: 'RECORD' };
  let pendingServed = false;
  const listeners = {};
  const record = (name) => (args) => { calls.push([name, args || {}]); return Promise.resolve({}); };

  const GeofenceCommute = {
    getStatus: () => { calls.push(['getStatus', {}]); return Promise.resolve({
      location: 'always', notifications: 'granted', monitoring: true, backgroundUpdates: true,
      geofence: { latitude: 37.2416, longitude: 127.1414, radius: 200, label: '램리서치 지곡동' }
    }); },
    requestLocationPermission: () => { calls.push(['requestLocationPermission', {}]); return Promise.resolve({ location: 'whenInUse', hint: '' }); },
    requestNotificationPermission: () => { calls.push(['requestNotificationPermission', {}]); return Promise.resolve({ notifications: 'granted', granted: true }); },
    getCurrentLocation: () => { calls.push(['getCurrentLocation', {}]); return Promise.resolve({ latitude: 37.241612, longitude: 127.141455, accuracy: 12.5 }); },
    setGeofence: (args) => { calls.push(['setGeofence', args]); return Promise.resolve({
      location: 'always', notifications: 'granted', monitoring: true, saved: true,
      geofence: { latitude: args.latitude, longitude: args.longitude, radius: args.radius, label: args.label } }); },
    clearGeofence: () => { calls.push(['clearGeofence', {}]); return Promise.resolve({ location: 'always', notifications: 'granted', monitoring: false }); },
    getPendingAction: () => {
      calls.push(['getPendingAction', {}]);
      if (pendingServed) return Promise.resolve({});
      pendingServed = true;
      return Promise.resolve(pending);
    },
    clearDelivered: () => { calls.push(['clearDelivered', {}]); return Promise.resolve({}); },
    testNotification: (args) => { calls.push(['testNotification', args]); return Promise.resolve({ scheduled: true }); },
    addListener: (event, cb) => { listeners[event] = cb; calls.push(['addListener', { event }]); return Promise.resolve({ remove() {} }); },
    removeAllListeners: () => Promise.resolve()
  };
  const Filesystem = {
    writeFile: (args) => { calls.push(['Filesystem.writeFile', { path: args.path, directory: args.directory }]); return Promise.resolve({ uri: 'file:///cache/' + args.path }); },
    getUri: (args) => Promise.resolve({ uri: 'file:///cache/' + args.path })
  };
  const Share = { share: (args) => { calls.push(['Share.share', { title: args.title, url: args.url }]); return Promise.resolve({}); } };

  const plugins = { GeofenceCommute, Filesystem, Share };
  window.Capacitor = {
    isNativePlatform: () => true,
    getPlatform: () => 'ios',
    Plugins: plugins,
    registerPlugin: (name) => plugins[name] || (plugins[name] = {}),
    isPluginAvailable: () => true,
    convertFileSrc: (u) => u,
    PluginHeaders: []
  };
})();
"""


def serve():
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=WWW)
    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.TCPServer(('127.0.0.1', PORT), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def main():
    os.makedirs(SHOTS, exist_ok=True)
    httpd = serve()
    errors = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            ctx = browser.new_context(viewport={"width": 393, "height": 852}, device_scale_factor=2,
                                      is_mobile=True, has_touch=True, locale="ko-KR", timezone_id="Asia/Seoul")
            ctx.add_init_script(FAKE_NATIVE)
            page = ctx.new_page()
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.on('dialog', lambda d: d.accept())
            page.goto(URL, wait_until='load')
            page.wait_for_timeout(600)

            # 앱 시작 시 대기 중인 알림 payload 처리
            banner = page.is_visible('#actionBanner')
            field = page.input_value('#actualEnd')
            check('앱 시작 시 getPendingAction → 배너 표시', banner)
            check('앱 시작 시 알림 시각 프리필(18:31)', field == '18:31', field)
            calls = page.evaluate('window.__calls.map(c=>c[0])')
            check('플러그인 호출: addListener/commuteAction', 'addListener' in calls, ', '.join(sorted(set(calls))))

            # 설정 탭: 권한 상태가 네이티브 응답으로 표시
            page.click('#settingsTab')
            page.wait_for_timeout(400)
            perm = page.inner_text('#permStatus')
            check('권한 상태 표시: 항상 허용/허용/감시 중',
                  '항상 허용' in perm and '허용' in perm and '감시 중' in perm, perm.replace('\n', ' | '))
            check('감시 중 태그 표시', page.is_visible('#monitorTag'))
            check('저장된 회사 위치가 폼에 채워짐', page.input_value('#companyLat').startswith('37.2416'),
                  page.input_value('#companyLat'))
            page.screenshot(path=os.path.join(SHOTS, '06-settings-native.png'), full_page=True)

            # 현재 위치로 저장
            page.click('#useCurrent')
            page.wait_for_timeout(300)
            lat = page.input_value('#companyLat')
            check('현재 위치로 저장 → 위도 채움', lat.startswith('37.241612'), lat)
            check('getCurrentLocation 호출', 'getCurrentLocation' in page.evaluate('window.__calls.map(c=>c[0])'))

            # 반경 변경 후 저장
            page.eval_on_selector('#radius', "el => { el.value = 300; el.dispatchEvent(new Event('input')); }")
            page.click('#saveGeofence')
            page.wait_for_timeout(300)
            call = page.evaluate("window.__calls.filter(c=>c[0]==='setGeofence').pop()")
            check('위치 저장 → setGeofence(위도/경도/반경 300)', call and call[1]['radius'] == 300 and call[1]['longitude'] > 0,
                  json.dumps(call, ensure_ascii=False) if call else 'none')

            # 알림 테스트
            page.click('#testOut')
            page.wait_for_timeout(200)
            t = page.evaluate("window.__calls.filter(c=>c[0]==='testNotification').pop()")
            check('퇴근 알림 테스트 → testNotification(type=out)', t and t[1]['type'] == 'out', json.dumps(t, ensure_ascii=False) if t else 'none')

            # 감시 중지
            page.click('#stopGeofence')
            page.wait_for_timeout(300)
            check('감시 중지 → clearGeofence', 'clearGeofence' in page.evaluate('window.__calls.map(c=>c[0])'))

            # CSV 저장 → Filesystem + Share
            page.click('#recordTab')
            page.wait_for_timeout(200)
            page.click('#csvBtn')
            page.wait_for_timeout(400)
            calls = page.evaluate('window.__calls')
            names = [c[0] for c in calls]
            check('CSV 저장 → Filesystem.writeFile(CACHE)', 'Filesystem.writeFile' in names,
                  json.dumps([c for c in calls if c[0].startswith('Filesystem')], ensure_ascii=False)[:200])
            check('CSV 저장 → Share.share', 'Share.share' in names)

            # 저장 후 알림 정리
            page.click('#saveBtn')
            page.wait_for_timeout(300)
            check('알림에서 저장하면 clearDelivered 호출', 'clearDelivered' in page.evaluate('window.__calls.map(c=>c[0])'))
            check('저장 후 배너 닫힘', not page.is_visible('#actionBanner'))
            page.screenshot(path=os.path.join(SHOTS, '07-native-saved.png'), full_page=True)

            check('자바스크립트 오류 없음', not errors, '; '.join(errors[:3]))
            browser.close()
    finally:
        httpd.shutdown()

    failed = [r for r in results if not r[1]]
    print('\n' + '=' * 60)
    print(f'{len(results) - len(failed)}/{len(results)} 통과')
    for name, _, detail in failed:
        print(f'  실패: {name} — {detail}')
    print('=' * 60)
    return 0 if not failed else 1


if __name__ == '__main__':
    raise SystemExit(main())
