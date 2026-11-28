#!/usr/bin/env python3
"""퇴근 기록 깜빡 알림(회사 반경 이탈 후 1시간마다 3회) 검증.

검증 항목
 A. 문구 150개: 3단계 × 50개, 총 150개, 중복 없음, 길이·한글 확인
 B. 날짜별 선택: 하루 3개가 서로 다르고, 하루만 넘어가도 3개 모두 달라지며,
    50일 동안 150개를 한 번씩 다 쓴다 (Swift 와 같은 식을 그대로 계산)
 C. 예약 규칙: 이탈 +1·+2·+3시간, 자정을 넘는 회차·이미 지난 회차는 제외
 D. 정적 검사: 플러그인 메서드/예약·취소 API/알림 payload/지오펜스 이탈 연동/
    퇴근 저장 시 즉시 취소/웹 화면(카드·이탈 시각 프리필)/빌드 번호 8
 E. 헤드리스 Chromium + 가짜 네이티브: 설정 카드 표시, 1분 테스트 호출,
    깜빡 알림을 누르면 **회사를 나선 시각**이 퇴근 칸에 채워지는지, 저장 시 clockOut 전달

실행:
  PLAYWRIGHT_BROWSERS_PATH=/workspace/work/pw-browsers \
  LD_LIBRARY_PATH=/workspace/build/pwlibs/usr/lib/x86_64-linux-gnu \
  python3 tests/verify-forget.py
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
PORT = 8126
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
    src = open(os.path.join(SWIFT_DIR, 'CommuteForgetCopy.swift'), encoding='utf-8').read()
    tiers = []
    for n in (1, 2, 3):
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
    offset = day_index(day) + round_no * 17
    return pool[((offset % len(pool)) + len(pool)) % len(pool)]


def forget_rounds(exit_dt, now):
    """CommuteForgetPlanner.rounds 와 같은 계산."""
    day = exit_dt.date()
    out = []
    for k in (1, 2, 3):
        fire = exit_dt + datetime.timedelta(minutes=60 * k)
        if fire.date() != day:          # 자정을 넘는 회차는 예약하지 않는다
            continue
        if (fire - now).total_seconds() <= 30:
            continue
        out.append((k, fire.strftime('%H:%M')))
    return out


def dt(text):
    return datetime.datetime.strptime(text, '%Y-%m-%d %H:%M')


def main():
    os.makedirs(SHOTS, exist_ok=True)
    copy_src, tiers = load_tiers()

    # ---------- A ----------
    check('문구 3단계 × 50개', [len(t) for t in tiers] == [50, 50, 50], str([len(t) for t in tiers]))
    flat = [p for t in tiers for p in t]
    check('문구 총 150개', len(flat) == 150, str(len(flat)))
    check('문구 중복 없음', len(set(flat)) == len(flat), f'고유 {len(set(flat))}개')
    check('문구가 비어 있지 않고 60자 이하',
          all(1 <= len(p) <= 60 and p == p.strip() for p in flat),
          f'최장 {max(len(p) for p in flat)}자')
    check('문구에 한글이 들어 있음', all(re.search(r'[가-힣]', p) for p in flat))
    check('Swift 쪽 총 개수 상수 150', 'static let totalCount = 150' in copy_src)
    check('문구에 "기록" 표현이 들어감', sum('기록' in p for p in flat) >= 100,
          f'{sum("기록" in p for p in flat)}개')

    # ---------- B ----------
    samples = []
    d = datetime.date(2026, 11, 28)
    for i in range(50):
        day = (d + datetime.timedelta(days=i)).isoformat()
        samples.append([phrase(tiers, day, k) for k in (1, 2, 3)])
    check('하루 3개가 서로 다름', all(len(set(s)) == 3 for s in samples))
    check('하루만 넘어가도 3개 모두 달라짐',
          all(all(samples[i][k] != samples[i + 1][k] for k in range(3)) for i in range(len(samples) - 1)))
    check('50일 동안 150개를 모두 사용', len(set(p for s in samples for p in s)) == 150,
          str(len(set(p for s in samples for p in s))))
    check('단계별 50일이면 50개를 모두 사용',
          all(len(set(s[k] for s in samples)) == 50 for k in range(3)))
    print('    오늘(2026-11-28) 깜빡 문구: ' + ' / '.join(
        f'{k+1}시간:{samples[0][k]}' for k in range(3)))

    # ---------- C ----------
    check('18:10 이탈 → 19:10·20:10·21:10 3회',
          forget_rounds(dt('2026-11-28 18:10'), dt('2026-11-28 18:12')) ==
          [(1, '19:10'), (2, '20:10'), (3, '21:10')],
          str(forget_rounds(dt('2026-11-28 18:10'), dt('2026-11-28 18:12'))))
    check('21:30 이탈 → 22:30·23:30 2회(자정 넘는 회차 제외)',
          forget_rounds(dt('2026-11-28 21:30'), dt('2026-11-28 21:35')) ==
          [(1, '22:30'), (2, '23:30')],
          str(forget_rounds(dt('2026-11-28 21:30'), dt('2026-11-28 21:35'))))
    check('22:30 이탈 → 23:30 1회',
          forget_rounds(dt('2026-11-28 22:30'), dt('2026-11-28 22:31')) == [(1, '23:30')],
          str(forget_rounds(dt('2026-11-28 22:30'), dt('2026-11-28 22:31'))))
    check('23:00 이탈 → 자정을 넘어 예약 없음',
          forget_rounds(dt('2026-11-28 23:00'), dt('2026-11-28 23:01')) == [])
    check('앱을 늦게 열어도 지난 회차는 건너뜀',
          forget_rounds(dt('2026-11-28 18:10'), dt('2026-11-28 19:40')) ==
          [(2, '20:10'), (3, '21:10')],
          str(forget_rounds(dt('2026-11-28 18:10'), dt('2026-11-28 19:40'))))
    check('모든 회차가 지났으면 예약 없음',
          forget_rounds(dt('2026-11-28 18:10'), dt('2026-11-28 21:20')) == [])

    # ---------- D ----------
    swift = read('native/geofence-commute/ios/Sources/GeofenceCommutePlugin/GeofenceCommutePlugin.swift')
    forget = read('native/geofence-commute/ios/Sources/GeofenceCommutePlugin/CommuteForgetReminder.swift')
    methods = re.findall(r'CAPPluginMethod\(name: "([^"]+)"', swift)
    for name in ('syncCommuteNag', 'getForgetStatus', 'testForgetNotification'):
        check(f'플러그인 메서드 {name}', name in methods)
    check('깜빡 알림은 이탈 시각 + 1시간 간격 3회', 'static let roundCount = 3' in forget
          and 'static let intervalMinutes = 60' in forget)
    check('예약: UNCalendarNotificationTrigger', 'UNCalendarNotificationTrigger' in forget)
    check('취소: removePendingNotificationRequests', 'removePendingNotificationRequests' in forget)
    check('취소: 알림 센터의 알림도 정리', 'removeDeliveredNotifications' in forget)
    check('자정을 넘는 회차는 제외', 'CommuteNagPlanner.dayKey(fire, calendar: calendar) == exitDay' in forget)
    check('알림 payload 에 forget/exitEpoch/type',
          all(k in forget for k in ['"forget": true', '"exitEpoch":', '"type": "out"']))
    check('알림 payload 에 퇴근 시각(exitTime)·대상 날짜',
          '"exitTime":' in forget and '"date": day' in forget)
    check('깜빡 알림은 퇴근 찍기 버튼이 달린 카테고리(COMMUTE_OUT)',
          'categoryIdentifier = CommuteNotificationHandler.clockOutCategory' in forget)
    check('알림 식별자 prefix 로 기존 독촉과 분리', '"commute-forget-"' in forget)
    check('퇴근 기록(clockOut)이 있으면 예약하지 않음',
          '!day.clockIn.isEmpty' in forget and 'day.clockOut.isEmpty' in forget)
    check('퇴근 기록 저장 시 즉시 취소(clockOut)',
          'day.clockOut.isEmpty {' in forget and 'cancelAll(clear: true)' in forget)
    check('지오펜스 이탈에서 깜빡 알림 예약', 'CommuteForgetScheduler.scheduleAfterExit' in swift
          and 'witnessedExit' in swift)
    check('기존 야근 독촉은 이탈 시 취소', 'CommuteNagScheduler.cancelAll()' in swift)
    check('앱 재실행 시 깜빡 알림 재정렬', 'CommuteForgetScheduler.refresh()' in swift)
    check('감시 중지 시 깜빡 알림 정리', 'CommuteStore.resetRegionState()\n        CommuteForgetScheduler.cancelAll' in swift)
    check('syncCommuteNag 응답에 forget 상태 포함', 'payload["forget"] = CommuteForgetScheduler.status()' in swift)
    check('알림 탭 payload 에 forget/exitEpoch 전달',
          'info["forget"]' in swift and 'info["exitEpoch"]' in swift)

    html = read('www/index.html')
    check('웹: 깜빡 알림 설정 카드', all(f'id="{i}"' in html for i in ('forgetPlan', 'forgetTag', 'forgetTest')))
    check('웹: getForgetStatus 호출', 'P.getForgetStatus(' in html and 'syncForget' in html)
    check('웹: 1분 테스트 → testForgetNotification', 'P.testForgetNotification(' in html)
    check('웹: 깜빡 알림 탭 구분(forget)', 'const isForget=!!payload.forget' in html and '퇴근 기록 깜빡 · ' in html)
    check('웹: 깜빡 탭은 회사를 나선 시각으로 프리필', 'exitEpoch' in html
          and 'isForget?((exitDate&&!isNaN(exitDate.getTime()))?exitDate:new Date())' in html)
    check('웹: 저장 시 syncCommuteNag 로 clockOut 전달', 'P.syncCommuteNag(payload)' in html
          and "clockOut:r.actualEnd||''" in html)

    cm = read('codemagic.yaml')
    check('codemagic: 빌드 번호 8 고정', 'APP_BUILD_NUMBER: "8"' in cm and '$BUILD" != "8"' in cm)
    pbx = read('ios/App/App.xcodeproj/project.pbxproj')
    check('Xcode: CURRENT_PROJECT_VERSION = 8 (앱·위젯)', pbx.count('CURRENT_PROJECT_VERSION = 8;') >= 2
          and 'CURRENT_PROJECT_VERSION = 7;' not in pbx)
    check('Xcode: MARKETING_VERSION = 1.0 (앱·위젯)', pbx.count('MARKETING_VERSION = 1.0;') >= 2)

    # ---------- E. 헤드리스 ----------
    fake = r"""
(() => {
  const calls = [];
  window.__calls = calls;
  const listeners = {};
  let clockIn = '', clockOut = '';
  const state = () => (!clockIn ? 'waitingClockIn' : (clockOut ? 'clockedOut' : 'scheduled'));
  const forgetStatus = () => {
    const hasExit = true;
    return {
      enabled: true, date: window.__ot ? window.__ot.today() : '2026-11-28',
      clockIn, clockOut,
      state: clockOut ? 'clockedOut' : 'scheduled',
      exitTime: '18:10',
      rounds: [
        {round:1,time:'19:10',phrase:'회사 반경을 벗어났어요. 퇴근 기록은 아직 비어 있어요',passed:false,skipped:false},
        {round:2,time:'20:10',phrase:'한 시간 전에 회사 반경을 벗어나셨는데, 퇴근 기록이 아직 없어요',passed:false,skipped:false},
        {round:3,time:'21:10',phrase:'두 시간째예요. 퇴근 기록이 아직 비어 있어요',passed:false,skipped:false}
      ],
      scheduled: clockOut ? [] : [1,2,3],
      scheduledTimes: clockOut ? [] : ['19:10','20:10','21:10'],
      nextTime: clockOut ? '' : '19:10',
      totalPhrases: 150
    };
  };
  window.__fakeForget = forgetStatus;
  const GeofenceCommute = {
    getStatus: () => { calls.push(['getStatus', {}]); return Promise.resolve({
      location: 'always', notifications: 'granted', monitoring: true, backgroundUpdates: false }); },
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
      clockIn = a.clockIn || ''; clockOut = a.clockOut || '';
      return Promise.resolve({
        enabled: a.enabled !== false, date: a.date, regularEnd: a.regularEnd,
        clockIn, clockOut, state: state(),
        rounds: [], scheduled: [], scheduledTimes: [], nextTime: '', totalPhrases: 200,
        forget: forgetStatus()
      });
    },
    getNagStatus: () => Promise.resolve({ enabled: true, rounds: [], state: 'waitingClockIn', regularEnd: '17:30' }),
    getForgetStatus: () => { calls.push(['getForgetStatus', {}]); return Promise.resolve(forgetStatus()); },
    testForgetNotification: (a) => { calls.push(['testForgetNotification', a]); return Promise.resolve({ scheduled: true }); },
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

            today = page.evaluate('window.__ot.today()')
            check('웹→네이티브: 시작 시 오늘 기록 전달(syncCommuteNag)',
                  page.evaluate("window.__calls.filter(c=>c[0]==='syncCommuteNag').length") >= 1)

            # 출근 기록 후 설정 탭에서 깜빡 카드 확인
            page.click('#quickStart')
            page.wait_for_timeout(300)
            page.click('#settingsTab')
            page.wait_for_timeout(300)
            plan = page.inner_text('#forgetPlan')
            check('깜빡 카드: 이탈 시각·다음 알림·문구 3개 표시',
                  '18:10' in plan and '19:10' in plan and plan.count('·') >= 3,
                  plan.replace('\n', ' | ')[:200])
            check('깜빡 카드 예약 태그 표시', page.is_visible('#forgetTag'))
            page.screenshot(path=os.path.join(SHOTS, '10-forget-settings.png'), full_page=True)

            page.click('#forgetTest')
            page.wait_for_timeout(300)
            t = page.evaluate("window.__calls.filter(c=>c[0]==='testForgetNotification').pop()")
            check('1분 뒤 깜빡 테스트 호출', bool(t and t[1]['delay'] == 60 and t[1]['round'] == 1),
                  json.dumps(t, ensure_ascii=False) if t else 'none')

            # 깜빡 알림 탭 → 회사를 나선 시각(18:10)으로 퇴근 칸 프리필
            page.click('#recordTab')
            page.wait_for_timeout(150)
            page.evaluate(f"""() => window.__ot.handleCommuteAction({{
                type:'out', nag:true, forget:true, round:2, date:'{today}',
                exitEpoch: new Date('{today}T18:10:00+09:00').getTime(),
                timestamp:'{today}T20:10:00+09:00' }})""")
            page.wait_for_timeout(300)
            field = page.input_value('#actualEnd')
            title = page.inner_text('#bannerTitle')
            banner = page.inner_text('#bannerMsg')
            check('깜빡 알림 탭 → 회사를 나선 시각(18:10) 프리필',
                  page.is_visible('#actionBanner') and field == '18:10',
                  f'{title} / {field}')
            check('깜빡 배너 제목', '퇴근 기록 깜빡' in title, title)
            check('깜빡 배너 안내 문구', '18:10' in banner and '퇴근 칸' in banner, banner)
            check('탭만으로는 기록되지 않음',
                  page.evaluate(f'window.__ot.records["{today}"]')['actualEnd'] == '', '아직 저장 전')
            page.screenshot(path=os.path.join(SHOTS, '11-forget-tap.png'), full_page=True)

            page.click('#saveBtn')
            page.wait_for_timeout(400)
            saved = page.evaluate(f'window.__ot.records["{today}"]')
            last = page.evaluate("window.__calls.filter(c=>c[0]==='syncCommuteNag').pop()")
            check('깜빡 알림에서 저장 → 퇴근 기록 18:10', saved and saved['actualEnd'] == '18:10',
                  json.dumps(saved, ensure_ascii=False))
            check('저장하면 네이티브에 clockOut 전달(남은 깜빡 알림 취소)',
                  bool(last and last[1]['clockOut'] == '18:10'),
                  json.dumps(last[1], ensure_ascii=False) if last else 'none')

            page.click('#settingsTab')
            page.wait_for_timeout(300)
            after = page.inner_text('#forgetPlan')
            check('퇴근 기록 후 카드: 더 보내지 않음 표시',
                  '퇴근 기록이 있어서' in after, after.replace('\n', ' | ')[:160])
            check('퇴근 기록 후 카드: 예약 태그 숨김', not page.is_visible('#forgetTag'))
            page.screenshot(path=os.path.join(SHOTS, '12-forget-done.png'), full_page=True)

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
