#!/usr/bin/env python3
"""퇴근 독촉 알림 검증.

검증 항목
 A. 문구 200개: 4단계 × 50개, 총 200개, 중복 없음, 길이·한글 확인
 B. 날짜별 선택: 하루 4개가 서로 다르고, 하루만 넘어가도 4개 모두 달라지며,
    50일 동안 200개를 한 번씩 다 쓴다 (Swift 와 같은 식을 그대로 계산)
 C. 예약 규칙: 정규 퇴근 +1~+4시간, 이미 지난 회차·자정 이후 회차는 제외,
    출근이 늦으면 남은 회차만 — Swift 계산식과 같은 순수 함수로 검증
 D. 정적 검사: 플러그인 메서드/예약·취소 API/알림 payload/지오펜스 연동,
    웹 화면(설정 카드·syncCommuteNag 호출 지점), 빌드 번호 5
 E. 헤드리스 Chromium + 가짜 네이티브: 웹이 오늘 기록을 넘겨 예약을 맞추는지,
    독촉 알림을 누르면 지금 시각으로 퇴근 칸이 채워지는지

실행:
  PLAYWRIGHT_BROWSERS_PATH=/workspace/work/pw-browsers \
  LD_LIBRARY_PATH=/workspace/build/pwlibs/usr/lib/x86_64-linux-gnu \
  python3 tests/verify-nag.py
"""
import datetime
import functools
import http.server
import json
import os
import re
import socketserver
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WWW = os.path.join(ROOT, 'www')
PORT = 8125
URL = f'http://127.0.0.1:{PORT}/index.html'
SHOTS = os.path.join(ROOT, 'tests', 'screens')
SWIFT_DIR = os.path.join(ROOT, 'native', 'geofence-commute', 'ios', 'Sources', 'GeofenceCommutePlugin')

results = []


def check(name, ok, detail=''):
    results.append((name, bool(ok), detail))
    print(('PASS  ' if ok else 'FAIL  ') + name + (f'  — {detail}' if detail else ''))


def read(path):
    with open(os.path.join(ROOT, path), encoding='utf-8') as f:
        return f.read()


# ---------- A. 문구 ----------

def load_tiers():
    src = open(os.path.join(SWIFT_DIR, 'CommuteNagCopy.swift'), encoding='utf-8').read()
    tiers = []
    for n in (1, 2, 3, 4):
        m = re.search(r'static let tier%d: \[String\] = \[(.*?)\n    \]' % n, src, re.S)
        tiers.append(re.findall(r'"([^"]*)"', m.group(1)) if m else [])
    return src, tiers


# ---------- B/C. Swift 와 같은 규칙 ----------

REFERENCE_DAY = datetime.date(2020, 1, 1)


def day_index(day):
    y, m, d = (int(x) for x in day.split('-'))
    return (datetime.date(y, m, d) - REFERENCE_DAY).days


def phrase(tiers, day, round_no):
    pool = tiers[round_no - 1]
    offset = day_index(day) + round_no * 13
    return pool[((offset % len(pool)) + len(pool)) % len(pool)]


def rounds(regular_end_minutes, now, day):
    out = []
    for k in range(1, 5):
        fire = regular_end_minutes + 60 * k
        if fire >= 24 * 60:
            continue
        fire_dt = datetime.datetime.combine(
            datetime.date(*[int(x) for x in day.split('-')]),
            datetime.time(fire // 60, fire % 60))
        if (fire_dt - now).total_seconds() <= 30:
            continue
        out.append((k, fire_dt.strftime('%H:%M')))
    return out


def main():
    os.makedirs(SHOTS, exist_ok=True)
    copy_src, tiers = load_tiers()

    # ---------- A ----------
    check('문구 4단계 × 50개', [len(t) for t in tiers] == [50, 50, 50, 50], str([len(t) for t in tiers]))
    flat = [p for t in tiers for p in t]
    check('문구 총 200개', len(flat) == 200, str(len(flat)))
    check('문구 중복 없음', len(set(flat)) == len(flat), f'고유 {len(set(flat))}개')
    check('문구가 비어 있지 않고 60자 이하',
          all(1 <= len(p) <= 60 and p == p.strip() for p in flat),
          f'최장 {max(len(p) for p in flat)}자')
    check('문구에 한글이 들어 있음', all(re.search(r'[가-힣]', p) for p in flat))
    check('Swift 쪽 총 개수 상수 200', 'static let totalCount = 200' in copy_src)

    # ---------- B ----------
    samples = []
    d = datetime.date(2026, 11, 28)
    for i in range(50):
        day = (d + datetime.timedelta(days=i)).isoformat()
        samples.append([phrase(tiers, day, k) for k in (1, 2, 3, 4)])
    check('하루 4개가 서로 다름', all(len(set(s)) == 4 for s in samples))
    check('하루만 넘어가도 4개 모두 달라짐',
          all(all(samples[i][k] != samples[i + 1][k] for k in range(4)) for i in range(len(samples) - 1)))
    check('50일 동안 200개를 모두 사용', len(set(p for s in samples for p in s)) == 200,
          str(len(set(p for s in samples for p in s))))
    check('단계별 50일이면 50개를 모두 사용',
          all(len(set(s[k] for s in samples)) == 50 for k in range(4)))
    # 2026-11-28(오늘) 기준 4개 샘플
    print('    오늘(2026-11-28) 문구: ' + ' / '.join(f'{k+1}시간:{samples[0][k]}' for k in range(4)))

    # ---------- C ----------
    day = '2026-11-28'
    base = 17 * 60 + 30
    now = datetime.datetime(2026, 11, 28, 18, 5)
    check('퇴근 17:30 · 18:05 → 4회 예약', rounds(base, now, day) ==
          [(1, '18:30'), (2, '19:30'), (3, '20:30'), (4, '21:30')], str(rounds(base, now, day)))
    now2 = datetime.datetime(2026, 11, 28, 19, 10)
    check('이미 지난 회차는 건너뜀', rounds(base, now2, day) ==
          [(2, '19:30'), (3, '20:30'), (4, '21:30')], str(rounds(base, now2, day)))
    now3 = datetime.datetime(2026, 11, 28, 20, 45)
    check('늦게 출근(20:45)하면 남은 회차만', rounds(base, now3, day) == [(4, '21:30')],
          str(rounds(base, now3, day)))
    now4 = datetime.datetime(2026, 11, 28, 22, 5)
    check('모든 회차가 지나면 예약 없음', rounds(base, now4, day) == [], str(rounds(base, now4, day)))
    check('정규 퇴근 22:00 → 자정 넘는 회차 제외', rounds(22 * 60, datetime.datetime(2026, 11, 28, 22, 5), day) ==
          [(1, '23:00')], str(rounds(22 * 60, datetime.datetime(2026, 11, 28, 22, 5), day)))
    check('정규 퇴근 09:00 → 10~13시 4회', rounds(9 * 60, datetime.datetime(2026, 11, 28, 8, 0), day) ==
          [(1, '10:00'), (2, '11:00'), (3, '12:00'), (4, '13:00')])

    # ---------- D ----------
    swift = read('native/geofence-commute/ios/Sources/GeofenceCommutePlugin/GeofenceCommutePlugin.swift')
    sched = read('native/geofence-commute/ios/Sources/GeofenceCommutePlugin/CommuteNagScheduler.swift')
    methods = re.findall(r'CAPPluginMethod\(name: "([^"]+)"', swift)
    for name in ('syncCommuteNag', 'getNagStatus', 'testNagNotification'):
        check(f'플러그인 메서드 {name}', name in methods)
    check('예약: UNCalendarNotificationTrigger', 'UNCalendarNotificationTrigger' in sched)
    check('취소: removePendingNotificationRequests', 'removePendingNotificationRequests' in sched)
    check('취소: 알림 센터의 독촉 알림도 정리', 'removeDeliveredNotifications' in sched)
    check('알림 payload 에 type/nag/round/date',
          all(k in sched for k in ['"type": "out"', '"nag": true', '"round": round.round', '"date": dateText']))
    check('독촉 알림은 기존 COMMUTE 카테고리 재사용', 'categoryIdentifier = "COMMUTE"' in sched)
    check('퇴근 저장 → 오늘 독촉 중지(clockOut)', '"clockOut"' in sched and 'suppress(date: today, reason: "clockOut")' in swift)
    check('회사 반경 이탈 → 오늘 독촉 중지(geofenceExit)',
          'suppress(date: today, reason: "geofenceExit")' in swift)
    check('반경 재진입 → 독촉 다시 예약', 'clearSuppression()' in swift and 'updateNagState' in swift)
    check('앱 재실행 시 예약 재정렬', 'CommuteNagScheduler.refresh()' in swift)
    check('문구 200개는 Swift 코드에서 예약에 사용', 'CommuteNagCopy.phrase' in sched
          and 'static let tiers' in copy_src and 'CommuteNagCopy.totalCount' in swift)

    html = read('www/index.html')
    check('웹: 독촉 설정 카드', all(f'id="{i}"' in html for i in ('nagEnabled', 'nagPlan', 'nagTest', 'nagTag')))
    check('웹: syncCommuteNag 호출', 'P.syncCommuteNag(' in html and 'typeof P.syncCommuteNag' in html)
    check('웹: 저장(persist) 시 동기화', re.search(r'refresh\(\);\s*syncNag\(\);', html) is not None)
    check('웹: 앱 시작·포그라운드 복귀 시 동기화',
          'refreshStatus();\n  syncNag();' in html and 'drainPending(); syncNag();' in html)
    check('웹: 기본 퇴근시간 저장 시 동기화', "syncNag();alert('저장했어요.')" in html)
    check('웹: 독촉 알림 탭 구분(nag)',
          'const isNag=!!payload.nag' in html and '퇴근 독촉 · ' in html)
    check('웹: 독촉 탭은 지금 시각으로 프리필', 'isNag?new Date():toPayloadTime(payload)' in html)

    cm = read('codemagic.yaml')
    check('codemagic: 빌드 번호 6 고정', 'APP_BUILD_NUMBER: "6"' in cm and 'APP_BUILD_NUMBER:-' in cm)
    pbx = read('ios/App/App.xcodeproj/project.pbxproj')
    check('Xcode: CURRENT_PROJECT_VERSION = 6', pbx.count('CURRENT_PROJECT_VERSION = 6;') == 2)
    check('Xcode: MARKETING_VERSION = 1.0', pbx.count('MARKETING_VERSION = 1.0;') == 2)

    # ---------- E. 헤드리스 ----------
    fake = r"""
(() => {
  const calls = [];
  window.__calls = calls;
  const listeners = {};
  const GeofenceCommute = {
    getStatus: () => { calls.push(['getStatus', {}]); return Promise.resolve({
      location: 'always', notifications: 'granted', monitoring: false, backgroundUpdates: false }); },
    requestLocationPermission: () => Promise.resolve({ location: 'always', hint: '' }),
    requestNotificationPermission: () => Promise.resolve({ notifications: 'granted', granted: true }),
    getCurrentLocation: () => Promise.resolve({ latitude: 37.24, longitude: 127.14, accuracy: 10 }),
    setGeofence: (a) => Promise.resolve({ location: 'always', notifications: 'granted', monitoring: true, saved: true,
      geofence: { latitude: a.latitude, longitude: a.longitude, radius: a.radius, label: a.label } }),
    clearGeofence: () => Promise.resolve({ location: 'always', notifications: 'granted', monitoring: false }),
    getPendingAction: () => Promise.resolve({}),
    clearDelivered: () => { calls.push(['clearDelivered', {}]); return Promise.resolve({}); },
    testNotification: () => Promise.resolve({ scheduled: true }),
    testNagNotification: (a) => { calls.push(['testNagNotification', a]); return Promise.resolve({ scheduled: true }); },
    syncCommuteNag: (a) => {
      calls.push(['syncCommuteNag', a]);
      const scheduled = a.clockIn && !a.clockOut;
      return Promise.resolve({
        enabled: a.enabled !== false, date: a.date, regularEnd: a.regularEnd,
        clockIn: a.clockIn || '', clockOut: a.clockOut || '',
        state: !a.enabled ? 'disabled' : (a.clockOut ? 'clockedOut' : (a.clockIn ? 'scheduled' : 'waitingClockIn')),
        rounds: [{round:1,time:'18:30',phrase:'슬슬 퇴근 시간이 지났어요. 카피바라가 시계를 힐끔 봐요',passed:false},
                 {round:2,time:'19:30',phrase:'두 시간째… 카피바라가 걱정스러운 눈으로 보고 있어요',passed:false},
                 {round:3,time:'20:30',phrase:'세 시간째… 카피바라가 간절하게 기다리고 있어요',passed:false},
                 {round:4,time:'21:30',phrase:'네 시간째! 카피바라가 당장 집에 가라고 외쳐요',passed:false}],
        scheduled: scheduled ? [1,2,3,4] : [],
        scheduledTimes: scheduled ? ['18:30','19:30','20:30','21:30'] : [],
        nextTime: scheduled ? '18:30' : '',
        totalPhrases: 200
      });
    },
    getNagStatus: () => Promise.resolve({ enabled: true, rounds: [], state: 'waitingClockIn', regularEnd: '17:30' }),
    addListener: (event, cb) => { listeners[event] = cb; return Promise.resolve({ remove() {} }); },
    removeAllListeners: () => Promise.resolve()
  };
  window.Capacitor = {
    isNativePlatform: () => true,
    getPlatform: () => 'ios',
    Plugins: { GeofenceCommute, Filesystem: {}, Share: {} },
    registerPlugin: (n) => GeofenceCommute,
    isPluginAvailable: () => true,
    convertFileSrc: (u) => u,
    PluginHeaders: []
  };
})();
"""

    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=WWW)
    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.TCPServer(('127.0.0.1', PORT), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    errors = []
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch()
            ctx = browser.new_context(viewport={"width": 393, "height": 852}, device_scale_factor=2,
                                      is_mobile=True, has_touch=True, locale="ko-KR", timezone_id="Asia/Seoul")
            ctx.add_init_script(fake)
            page = ctx.new_page()
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.on('dialog', lambda d: d.accept())
            page.goto(URL, wait_until='load')
            page.wait_for_timeout(700)

            first = page.evaluate("window.__calls.filter(c=>c[0]==='syncCommuteNag').pop()")
            today = page.evaluate('window.__ot.today()')
            check('웹→네이티브: 시작 시 오늘 기록 전달',
                  first and first[1]['date'] == today and first[1]['clockIn'] == ''
                  and first[1]['enabled'] is True and first[1]['regularEnd'] == '17:30',
                  json.dumps(first[1], ensure_ascii=False) if first else 'none')

            page.click('#quickStart')
            page.wait_for_timeout(300)
            after_in = page.evaluate("window.__calls.filter(c=>c[0]==='syncCommuteNag').pop()")
            check('출근 저장 → clockIn 전달', bool(after_in and after_in[1]['clockIn']) and after_in[1]['clockOut'] == '',
                  json.dumps(after_in[1], ensure_ascii=False) if after_in else 'none')

            page.click('#settingsTab')
            page.wait_for_timeout(300)
            plan = page.inner_text('#nagPlan')
            check('설정 카드에 오늘의 문구 4개 표시',
                  plan.count('퇴근시간') == 0 and plan.count('·') >= 4 and '18:30' in plan,
                  plan.replace('\n', ' | ')[:160])
            check('예약 상태 태그 표시', page.is_visible('#nagTag'))
            page.screenshot(path=os.path.join(SHOTS, '08-nag-settings.png'), full_page=True)

            page.click('#nagTest')
            page.wait_for_timeout(300)
            t = page.evaluate("window.__calls.filter(c=>c[0]==='testNagNotification').pop()")
            check('1분 뒤 독촉 테스트 호출', bool(t and t[1]['delay'] == 60 and t[1]['round'] == 1),
                  json.dumps(t, ensure_ascii=False) if t else 'none')

            page.uncheck('#nagEnabled')
            page.wait_for_timeout(300)
            off = page.evaluate("window.__calls.filter(c=>c[0]==='syncCommuteNag').pop()")
            check('끄면 enabled=false 전달', bool(off and off[1]['enabled'] is False),
                  json.dumps(off[1], ensure_ascii=False) if off else 'none')
            page.check('#nagEnabled')
            page.wait_for_timeout(200)

            # 독촉 알림 탭 → 지금 시각으로 퇴근 칸 프리필 (기록은 저장 전까지 없음)
            page.click('#recordTab')
            page.wait_for_timeout(150)
            page.evaluate("() => window.__ot.handleCommuteAction({type:'out', nag:true, round:2, date:window.__ot.today(), timestamp:'2026-11-28T19:30:00+09:00'})")
            page.wait_for_timeout(300)
            now_hm = page.evaluate("() => { const n=new Date(); return String(n.getHours()).padStart(2,'0')+':'+String(n.getMinutes()).padStart(2,'0'); }")
            field = page.input_value('#actualEnd')
            title = page.inner_text('#bannerTitle')
            check('독촉 알림 탭 → 배너/지금 시각 프리필', page.is_visible('#actionBanner') and field == now_hm,
                  f'{title} / {field} / 지금 {now_hm}')
            check('독촉 배너 제목', '퇴근 독촉' in title, title)
            check('탭만으로는 기록되지 않음',
                  page.evaluate(f'window.__ot.records["{today}"]')['actualEnd'] == '', '아직 저장 전')
            page.screenshot(path=os.path.join(SHOTS, '09-nag-tap.png'), full_page=True)

            page.click('#saveBtn')
            page.wait_for_timeout(300)
            saved = page.evaluate(f'window.__ot.records["{today}"]')
            last = page.evaluate("window.__calls.filter(c=>c[0]==='syncCommuteNag').pop()")
            check('독촉 알림에서 저장 → 퇴근 기록', saved and saved['actualEnd'] == now_hm,
                  json.dumps(saved, ensure_ascii=False))
            check('저장하면 네이티브에 clockOut 전달(남은 독촉 취소)', bool(last and last[1]['clockOut'] == now_hm),
                  json.dumps(last[1], ensure_ascii=False) if last else 'none')

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
