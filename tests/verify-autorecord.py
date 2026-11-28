#!/usr/bin/env python3
"""자동 출퇴근 기록(선택 옵션) 검증.

검증 항목
 A. 규칙(순수 계산): 자동 출근/자동 퇴근이 켜졌을 때만 기록, 이미 기록된 시각은 덮어쓰지 않음,
    자동 퇴근은 출근 기록이 있을 때만, 자동 퇴근 시 독촉·깜빡 알림 취소
 B. 정적 검사: 플러그인 메서드 4개·UserDefaults 기본 OFF·자동 기록 문구·기준선 미기록·
    종료 상태 대기 큐·웹 화면(토글 2개·기본 OFF)/빌드 번호 8
 C. 헤드리스 Chromium + 가짜 네이티브:
    - 토글 기본 OFF, 켜면 syncAutoRecordSettings 로 즉시 동기화
    - 앱이 꺼져 있는 동안 만들어진 기록(대기 큐)을 앱 시작 시 기록에 반영하고 큐에서 지움
    - 이미 기록된 시간은 덮어쓰지 않음
    - 앱이 떠 있을 때 자동 기록 이벤트(푸시)를 받으면 즉시 반영 + clockOut 동기화

실행:
  PLAYWRIGHT_BROWSERS_PATH=/workspace/work/pw-browsers \
  LD_LIBRARY_PATH=/workspace/build/pwlibs/usr/lib/x86_64-linux-gnu \
  python3 tests/verify-autorecord.py
"""
import functools
import http.server
import json
import os
import re
import socketserver
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WWW = os.path.join(ROOT, 'www')
PORT = 8127
URL = f'http://127.0.0.1:{PORT}/index.html'
SHOTS = os.path.join(ROOT, 'tests', 'screens')
SWIFT_DIR = os.path.join(ROOT, 'native', 'geofence-commute', 'ios', 'Sources', 'GeofenceCommutePlugin')

TODAY = datetime.now(ZoneInfo('Asia/Seoul')).strftime('%Y-%m-%d')

results = []


def check(name, ok, detail=''):
    results.append((name, bool(ok), detail))
    print(('PASS  ' if ok else 'FAIL  ') + name + (f'  — {detail}' if detail else ''))


def read(path):
    with open(os.path.join(ROOT, path), encoding='utf-8') as f:
        return f.read()


# ---------- A. 네이티브 규칙과 같은 계산 ----------

class AutoStore:
    """CommuteAutoRecordStore 와 같은 규칙(설정·오늘 상태·대기 큐)."""

    def __init__(self, auto_in=False, auto_out=False):
        self.settings = {'autoClockIn': auto_in, 'autoClockOut': auto_out}
        self.day = None          # {'date','clockIn','clockOut'}
        self.pending = []

    def state(self, date):
        if self.day and self.day['date'] == date:
            return dict(self.day)
        return {'date': date, 'clockIn': '', 'clockOut': ''}

    def record(self, date, kind, time, epoch):
        current = self.state(date)
        if kind == 'in':
            if current['clockIn']:
                return None
            current['clockIn'] = time
        else:
            if not current['clockIn'] or current['clockOut']:
                return None
            current['clockOut'] = time
        self.day = current
        entry = {'id': f'{date}-{kind}-{int(epoch)}', 'date': date, 'type': kind,
                 'time': time, 'epoch': epoch * 1000.0}
        self.pending = [e for e in self.pending if not (e['date'] == date and e['type'] == kind)]
        self.pending.append(entry)
        return entry

    def merge(self, date, web_in, web_out):
        current = self.state(date)
        waiting_in = any(e['date'] == date and e['type'] == 'in' for e in self.pending)
        waiting_out = any(e['date'] == date and e['type'] == 'out' for e in self.pending)
        if web_in:
            current['clockIn'] = web_in
        elif not waiting_in:
            current['clockIn'] = ''
        if web_out:
            current['clockOut'] = web_out
        elif not waiting_out:
            current['clockOut'] = ''
        self.day = current

    def ack(self, ids):
        self.pending = [e for e in self.pending if e['id'] not in ids]


def transition(store, inside, now='09:00', web=None):
    """GeofenceCommutePlugin.handleTransition 과 같은 결정.

    돌려주는 값: ('auto', kind) / ('prompt', kind) / ('silent', kind)
    """
    date = '2026-11-28'
    hhmm = now
    if web is not None:
        store.merge(date, web.get('clockIn', ''), web.get('clockOut', ''))
    if inside and store.settings['autoClockIn']:
        if store.record(date, 'in', hhmm, 1) is not None:
            return ('auto', 'in')
        return ('silent', 'in')
    if not inside and store.settings['autoClockOut']:
        state = store.state(date)
        if store.record(date, 'out', hhmm, 1) is not None:
            return ('auto', 'out')
        if state['clockOut']:
            return ('silent', 'out')
    return ('prompt', 'in' if inside else 'out')


def web_decide(records, evt):
    """www/index.html 의 autoRecordDecision 과 같은 규칙."""
    r = records.get(evt['date'], {})
    if evt['type'] == 'in':
        if r.get('startTime'):
            return 'same' if r['startTime'] == evt['time'] else 'skipped'
        return 'apply'
    if not r.get('startTime'):
        return 'skipped'
    if r.get('actualEnd'):
        return 'same' if r['actualEnd'] == evt['time'] else 'skipped'
    return 'apply'


def rules():
    # 1. 기본은 둘 다 꺼짐 → 기존 확인 알림만
    s = AutoStore()
    check('OFF: 반경 진입은 기존 확인 알림', transition(s, True) == ('prompt', 'in'))
    check('OFF: 기록을 만들지 않음', s.day is None and not s.pending)

    s = AutoStore()
    s.merge('2026-11-28', '08:30', '')
    check('OFF: 반경 이탈은 기존 확인 알림', transition(s, False) == ('prompt', 'out'))
    check('OFF: 퇴근 기록을 만들지 않음', s.state('2026-11-28')['clockOut'] == '')

    # 2. 자동 출근 ON
    s = AutoStore(auto_in=True)
    check('자동 출근 ON: 기록이 없으면 자동 출근', transition(s, True, '08:45') == ('auto', 'in'))
    check('자동 출근 ON: 시각과 대기 큐', s.state('2026-11-28')['clockIn'] == '08:45'
          and len(s.pending) == 1 and s.pending[0]['time'] == '08:45')

    s = AutoStore(auto_in=True)
    s.merge('2026-11-28', '08:30', '')
    check('자동 출근 ON: 이미 출근 기록이 있으면 덮어쓰지 않고 조용히', transition(s, True, '08:45') == ('silent', 'in'))
    check('자동 출근 ON: 기존 시각 유지', s.state('2026-11-28')['clockIn'] == '08:30' and not s.pending)

    # 3. 자동 퇴근 ON
    s = AutoStore(auto_out=True)
    s.merge('2026-11-28', '08:30', '')
    check('자동 퇴근 ON: 출근 기록이 있으면 자동 퇴근', transition(s, False, '18:10') == ('auto', 'out'))
    check('자동 퇴근 ON: 이탈 시각 기록', s.state('2026-11-28')['clockOut'] == '18:10')

    s = AutoStore(auto_out=True)
    s.merge('2026-11-28', '08:30', '17:40')
    check('자동 퇴근 ON: 이미 퇴근 기록이 있으면 조용히', transition(s, False, '18:10') == ('silent', 'out'))
    check('자동 퇴근 ON: 기존 퇴근 시각 유지', s.state('2026-11-28')['clockOut'] == '17:40')

    s = AutoStore(auto_out=True)
    check('자동 퇴근 ON: 출근 기록이 없으면 기존 안내 알림', transition(s, False, '18:10') == ('prompt', 'out'))
    check('자동 퇴근 ON: 출근 없으면 기록 없음', s.state('2026-11-28')['clockOut'] == '')

    # 4. 같은 날 재진입·재이탈(중복 방지)
    s = AutoStore(auto_in=True, auto_out=True)
    transition(s, True, '08:45')
    transition(s, False, '18:10')
    before = list(s.pending)
    transition(s, True, '18:40')
    transition(s, False, '18:50')
    check('같은 날 두 번째 진입/이탈은 기록을 더 만들지 않음',
          [e['type'] for e in s.pending] == [e['type'] for e in before] and len(s.pending) == 2,
          str([(e['type'], e['time']) for e in s.pending]))
    check('대기 큐는 날짜·종류별 한 건', len(s.pending) == len({(e['date'], e['type']) for e in s.pending}))

    # 5. 웹 기록 동기화가 대기 큐를 지우지 않음
    s = AutoStore(auto_in=True)
    transition(s, True, '08:45')
    s.merge('2026-11-28', '', '')
    check('웹이 아직 반영 전(빈 기록)이어도 대기 기록이 지워지지 않음',
          s.state('2026-11-28')['clockIn'] == '08:45' and len(s.pending) == 1)
    s.ack([s.pending[0]['id']])
    s.merge('2026-11-28', '08:45', '')
    check('반영·확인 후 큐에서 지워짐', not s.pending and s.state('2026-11-28')['clockIn'] == '08:45')

    # 6. 앱이 꺼져 있던 동안의 큐 → 웹 기록 (들어온 순서대로 적용)
    records = {}
    queue = [{'id': 'a', 'date': '2026-11-28', 'type': 'in', 'time': '08:45'},
             {'id': 'b', 'date': '2026-11-28', 'type': 'out', 'time': '18:10'}]
    decisions = []
    for evt in queue:
        decision = web_decide(records, evt)
        decisions.append(decision)
        if decision == 'apply':
            record = records.setdefault(evt['date'], {'startTime': '', 'actualEnd': ''})
            if evt['type'] == 'in':
                record['startTime'] = evt['time']
            else:
                record['actualEnd'] = evt['time']
    check('종료 상태 큐(출근·퇴근) 모두 반영 대상', decisions == ['apply', 'apply'], str(decisions))
    check('종료 상태 큐 적용 결과', records['2026-11-28'] == {'startTime': '08:45', 'actualEnd': '18:10'},
          str(records['2026-11-28']))

    existing = {'2026-11-28': {'startTime': '08:30', 'actualEnd': ''}}
    check('웹: 이미 출근 기록이 있으면 덮어쓰지 않음',
          web_decide(existing, queue[0]) == 'skipped', web_decide(existing, queue[0]))
    check('웹: 같은 시각이면 중복 저장하지 않음',
          web_decide({'2026-11-28': {'startTime': '08:45'}}, queue[0]) == 'same')
    check('웹: 출근 기록이 없으면 퇴근만 남기지 않음',
          web_decide({}, queue[1]) == 'skipped')


def static_checks():
    swift = read('native/geofence-commute/ios/Sources/GeofenceCommutePlugin/GeofenceCommutePlugin.swift')
    auto = read('native/geofence-commute/ios/Sources/GeofenceCommutePlugin/CommuteAutoRecord.swift')
    methods = re.findall(r'CAPPluginMethod\(name: "([^"]+)"', swift)
    for name in ('syncAutoRecordSettings', 'getAutoRecordStatus', 'getAutoRecords', 'ackAutoRecords'):
        check(f'플러그인 메서드 {name}', name in methods)

    check('설정 기본값 OFF', 'static let off = Settings(autoClockIn: false, autoClockOut: false)' in auto)
    check('설정 저장소는 UserDefaults(앱이 꺼져 있어도 읽음)',
          'UserDefaults.standard' in auto and 'autoRecord.settings.v1' in auto)
    check('오늘 상태·대기 큐도 UserDefaults', 'autoRecord.day.v1' in auto and 'autoRecord.pending.v1' in auto)
    check('자동 출근 문구 "HH:MM 출근이 자동으로 기록되었어요!"',
          'content.body = "\\(entry.time) \\(entry.type == "in" ? "출근" : "퇴근")이 자동으로 기록되었어요!"' in swift)
    check('자동 기록 알림은 기록 배너를 띄우지 않음(type 없이 recordType)',
          '"recordType": entry.type' in swift and '"type": entry.type' not in swift)
    check('자동 기록 알림 카테고리(누를 동작 없음)',
          'static let autoCategory = "COMMUTE_AUTO"' in swift and 'actions: []' in swift)
    check('진입 시 자동 출근 기록', 'if inside, settings.autoClockIn {' in swift)
    check('이탈 시 자동 퇴근 기록', 'if !inside, settings.autoClockOut {' in swift)
    check('이미 기록이 있으면 record 가 nil(덮어쓰기 금지)',
          'guard current.clockIn.isEmpty else { return nil }' in auto
          and 'guard !current.clockIn.isEmpty, current.clockOut.isEmpty else { return nil }' in auto)
    check('자동 퇴근 시 야근 독촉 즉시 취소', 'CommuteNagScheduler.cancelAll()' in swift)
    check('자동 퇴근 시 깜빡 알림 3회 즉시 취소', 'CommuteForgetScheduler.cancelAll(clear: true)' in swift)
    check('자동 퇴근을 독촉 상태에도 반영(clockOut)',
          'CommuteNagStore.suppress(date: today, reason: "clockOut")' in swift)
    check('기준선(첫 확인)은 자동 기록하지 않음',
          'let isTransition = previous == nil ? !baselineIfUnknown : (previous != newState)' in swift)
    check('앱이 떠 있으면 웹에 즉시 알림(autoRecord 이벤트)',
          '.commuteAutoRecordCreated' in swift and 'notifyListeners("autoRecord"' in swift)
    check('앱이 꺼져 있던 기록은 대기 큐에 보관', 'pending.append(entry)' in auto or 'list.append(entry)' in auto)
    check('웹이 확인(ack)하면 큐에서 지움', 'static func acknowledge(ids: [String])' in auto)
    check('웹 기록 동기화가 대기 기록을 지우지 않음',
          'let waitingIn = pending.contains' in auto and 'let waitingOut = pending.contains' in auto)
    check('syncCommuteNag 에서 오늘 기록을 자동 기록 상태에 반영',
          'CommuteAutoRecordStore.merge(webClockIn: clockIn, webClockOut: clockOut, date: today)' in swift)

    html = read('www/index.html')
    check('웹: 자동 출퇴근 설정 카드', all(f'id="{i}"' in html for i in
                                        ('autoClockIn', 'autoClockOut', 'autoPlan', 'autoTag')))
    check('웹: 토글 두 개가 기본 OFF', 'autoSettings=Object.assign({clockIn:false,clockOut:false}' in html)
    check('웹: 토글 변경 시 즉시 네이티브 동기화',
          "P.syncAutoRecordSettings({autoClockIn:!!autoSettings.clockIn,autoClockOut:!!autoSettings.clockOut})" in html)
    check('웹: 시작 시 대기 큐 반영', 'await drainAutoRecords();' in html and 'P.getAutoRecords(' in html)
    check('웹: 반영 후 ackAutoRecords', 'P.ackAutoRecords({ids:ack})' in html)
    check('웹: 이미 기록된 시간은 덮어쓰지 않음',
          "if(r.startTime) return r.startTime===evt.time?'same':'skipped';" in html and
          "if(!r.startTime) return 'skipped';" in html)
    check('웹: 자동 기록 푸시 수신', "P.addListener('autoRecord'" in html and 'handleAutoRecordPush' in html)
    check('웹: 자동 기록 후 syncNag 로 clockOut 동기화', 'function finishAutoRecords(applied)' in html
          and 'syncNag();' in html)

    cm = read('codemagic.yaml')
    check('codemagic: 빌드 번호 8 고정', 'APP_BUILD_NUMBER: "8"' in cm and '$BUILD" != "8"' in cm)
    pbx = read('ios/App/App.xcodeproj/project.pbxproj')
    check('Xcode: CURRENT_PROJECT_VERSION = 8 (앱·위젯)', pbx.count('CURRENT_PROJECT_VERSION = 8;') >= 2
          and 'CURRENT_PROJECT_VERSION = 7;' not in pbx)
    check('Xcode: MARKETING_VERSION = 1.0 (앱·위젯)', pbx.count('MARKETING_VERSION = 1.0;') >= 2)


FAKE = r"""
(() => {
  const calls = [];
  window.__calls = calls;
  const listeners = {};
  let settings = { autoClockIn: false, autoClockOut: false };
  let day = { date: '__TODAY__', clockIn: '', clockOut: '' };
  const queue = (window.__autoPendingSeed || []).map(e => Object.assign({}, e));
  let nagIn = '', nagOut = '';
  const status = () => ({
    settings: Object.assign({}, settings),
    date: day.date, clockIn: day.clockIn, clockOut: day.clockOut,
    pending: queue.map(e => Object.assign({}, e))
  });
  const GeofenceCommute = {
    getStatus: () => Promise.resolve({ location: 'always', notifications: 'granted', monitoring: true,
      backgroundUpdates: false, autoRecord: Object.assign({}, settings) }),
    requestLocationPermission: () => Promise.resolve({ location: 'always', hint: '' }),
    requestNotificationPermission: () => Promise.resolve({ notifications: 'granted', granted: true }),
    getCurrentLocation: () => Promise.resolve({ latitude: 37.24, longitude: 127.14, accuracy: 10 }),
    setGeofence: (a) => Promise.resolve({ location: 'always', notifications: 'granted', monitoring: true, saved: true,
      geofence: { latitude: a.latitude, longitude: a.longitude, radius: a.radius, label: a.label } }),
    clearGeofence: () => Promise.resolve({ location: 'always', notifications: 'granted', monitoring: false }),
    getPendingAction: () => Promise.resolve({}),
    clearDelivered: () => Promise.resolve({}),
    testNotification: () => Promise.resolve({ scheduled: true }),
    testNagNotification: () => Promise.resolve({ scheduled: true }),
    testForgetNotification: () => Promise.resolve({ scheduled: true }),
    syncCommuteNag: (a) => {
      calls.push(['syncCommuteNag', a]);
      nagIn = a.clockIn || ''; nagOut = a.clockOut || '';
      return Promise.resolve({ enabled: a.enabled !== false, date: a.date, regularEnd: a.regularEnd,
        clockIn: nagIn, clockOut: nagOut,
        state: !nagIn ? 'waitingClockIn' : (nagOut ? 'clockedOut' : 'scheduled'),
        rounds: [], scheduled: [], scheduledTimes: [], nextTime: '', totalPhrases: 200,
        forget: { enabled: true, date: a.date, clockIn: nagIn, clockOut: nagOut,
          state: nagOut ? 'clockedOut' : 'waitingExit', exitTime: '', rounds: [], scheduled: [],
          scheduledTimes: [], nextTime: '', totalPhrases: 150 } });
    },
    getNagStatus: () => Promise.resolve({ enabled: true, rounds: [], state: 'waitingClockIn', regularEnd: '17:30' }),
    getForgetStatus: () => Promise.resolve({ enabled: true, date: day.date, clockIn: nagIn, clockOut: nagOut,
      state: nagOut ? 'clockedOut' : 'waitingExit', exitTime: '', rounds: [], scheduled: [], scheduledTimes: [],
      nextTime: '', totalPhrases: 150 }),
    syncAutoRecordSettings: (a) => {
      calls.push(['syncAutoRecordSettings', a]);
      settings = { autoClockIn: !!a.autoClockIn, autoClockOut: !!a.autoClockOut };
      return Promise.resolve(status());
    },
    getAutoRecordStatus: () => { calls.push(['getAutoRecordStatus', {}]); return Promise.resolve(status()); },
    getAutoRecords: () => { calls.push(['getAutoRecords', {}]);
      return Promise.resolve({ records: queue.map(e => Object.assign({}, e)), settings: Object.assign({}, settings) }); },
    ackAutoRecords: (a) => {
      calls.push(['ackAutoRecords', a]);
      const ids = (a && a.ids) || [];
      for (let i = queue.length - 1; i >= 0; i--) if (ids.indexOf(queue[i].id) >= 0) queue.splice(i, 1);
      for (const id of ids) {
        const e = (window.__autoPendingSeed || []).find(x => x.id === id);
        if (e) { if (e.type === 'in') day.clockIn = e.time; else day.clockOut = e.time; }
      }
      return Promise.resolve({ remaining: queue.length });
    },
    addListener: (event, cb) => { listeners[event] = cb; return Promise.resolve({ remove() {} }); },
    removeAllListeners: () => Promise.resolve()
  };
  window.__listeners = listeners;
  window.__fakeAuto = { get settings() { return settings; }, get day() { return day; }, queue };
  window.Capacitor = {
    isNativePlatform: () => true,
    getPlatform: () => 'ios',
    Plugins: { GeofenceCommute, Filesystem: {}, Share: {} },
    registerPlugin: () => GeofenceCommute,
    isPluginAvailable: () => true,
    convertFileSrc: (u) => u,
    PluginHeaders: []
  };
})();
"""


def headless():
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
                                      is_mobile=True, has_touch=True, locale="ko-KR",
                                      timezone_id="Asia/Seoul")
            pending = [
                {'id': f'{TODAY}-in-1795900000', 'date': TODAY, 'type': 'in', 'time': '08:45',
                 'epoch': 1795900000000.0},
            ]
            ctx.add_init_script(f'window.__autoPendingSeed = {json.dumps(pending)};')
            ctx.add_init_script(FAKE.replace('__TODAY__', TODAY))
            page = ctx.new_page()
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.on('dialog', lambda d: d.accept())
            page.goto(URL, wait_until='load')
            page.wait_for_timeout(800)

            check('앱 시작: 대기 큐를 가져옴(getAutoRecords)',
                  page.evaluate("window.__calls.filter(c=>c[0]==='getAutoRecords').length") >= 1)
            saved = page.evaluate(f'window.__ot.records["{TODAY}"]')
            check('종료 상태 기록 복원: 출근 08:45', bool(saved) and saved['startTime'] == '08:45',
                  json.dumps(saved, ensure_ascii=False))
            check('종료 상태 기록 복원: 정규 시간 기본값',
                  bool(saved) and saved['regularStart'] == '08:30' and saved['regularEnd'] == '17:30')
            ack = page.evaluate("window.__calls.filter(c=>c[0]==='ackAutoRecords').pop()")
            check('반영한 기록은 큐에서 지움(ackAutoRecords)', bool(ack and ack[1]['ids']), json.dumps(ack, ensure_ascii=False))
            first_sync = page.evaluate("window.__calls.filter(c=>c[0]==='syncCommuteNag').pop()")
            check('반영 후 네이티브에 출근 시각 전달', bool(first_sync and first_sync[1]['clockIn'] == '08:45'),
                  json.dumps(first_sync[1], ensure_ascii=False) if first_sync else 'none')

            page.click('#settingsTab')
            page.wait_for_timeout(400)
            check('설정: 자동 출근 토글 기본 OFF', not page.is_checked('#autoClockIn'))
            check('설정: 자동 퇴근 토글 기본 OFF', not page.is_checked('#autoClockOut'))
            plan = page.inner_text('#autoPlan')
            check('설정 카드: 켜짐/꺼짐과 오늘 자동 기록 표시',
                  '자동 출근 기록' in plan and '오늘 자동 기록' in plan and '08:45' in plan,
                  plan.replace('\n', ' | ')[:200])
            check('설정 카드: 토글 꺼짐이면 태그 숨김', not page.is_visible('#autoTag'))
            page.screenshot(path=os.path.join(SHOTS, '20-auto-settings.png'), full_page=True)

            page.check('#autoClockIn')
            page.wait_for_timeout(400)
            call = page.evaluate("window.__calls.filter(c=>c[0]==='syncAutoRecordSettings').pop()")
            check('자동 출근 ON → 즉시 네이티브 동기화',
                  bool(call and call[1]['autoClockIn'] is True and call[1]['autoClockOut'] is False),
                  json.dumps(call[1], ensure_ascii=False) if call else 'none')
            check('자동 출근 ON → 켜짐 태그', page.is_visible('#autoTag'))
            check('자동 출근 ON → localStorage 저장',
                  page.evaluate("JSON.parse(localStorage.getItem('ot.autoRecord.v1')).clockIn") is True)
            page.check('#autoClockOut')
            page.wait_for_timeout(400)
            call = page.evaluate("window.__calls.filter(c=>c[0]==='syncAutoRecordSettings').pop()")
            check('자동 퇴근 ON → 즉시 네이티브 동기화', bool(call and call[1]['autoClockOut'] is True),
                  json.dumps(call[1], ensure_ascii=False) if call else 'none')
            page.screenshot(path=os.path.join(SHOTS, '21-auto-settings-on.png'), full_page=True)

            # 앱이 떠 있을 때 자동 퇴근 푸시
            page.click('#recordTab')
            page.wait_for_timeout(200)
            page.evaluate(f"""() => window.__ot.handleAutoRecordPush({{
                id:'{TODAY}-out-1795930000', date:'{TODAY}', type:'out', time:'18:10', epoch:1795930000000 }})""")
            page.wait_for_timeout(500)
            saved = page.evaluate(f'window.__ot.records["{TODAY}"]')
            check('자동 퇴근 푸시 → 퇴근 18:10 기록', bool(saved) and saved['actualEnd'] == '18:10',
                  json.dumps(saved, ensure_ascii=False))
            check('자동 기록 안내 문구', '18:10' in page.inner_text('#feedback')
                  and '자동으로 기록' in page.inner_text('#feedback'), page.inner_text('#feedback'))
            ack = page.evaluate("window.__calls.filter(c=>c[0]==='ackAutoRecords').pop()")
            check('푸시로 처리한 기록도 큐에서 지움', bool(ack and f'{TODAY}-out-1795930000' in ack[1]['ids']),
                  json.dumps(ack, ensure_ascii=False) if ack else 'none')
            last = page.evaluate("window.__calls.filter(c=>c[0]==='syncCommuteNag').pop()")
            check('자동 퇴근 후 clockOut 동기화(독촉·깜빡 취소)', bool(last and last[1]['clockOut'] == '18:10'),
                  json.dumps(last[1], ensure_ascii=False) if last else 'none')
            page.screenshot(path=os.path.join(SHOTS, '22-auto-record.png'), full_page=True)

            # 같은 날 다시 진입/이탈 → 덮어쓰지 않음
            page.evaluate(f"""() => window.__ot.handleAutoRecordPush({{
                id:'{TODAY}-in-1795909999', date:'{TODAY}', type:'in', time:'09:20', epoch:1795909999000 }})""")
            page.wait_for_timeout(300)
            saved = page.evaluate(f'window.__ot.records["{TODAY}"]')
            check('이미 있는 출근 시각은 덮어쓰지 않음', saved['startTime'] == '08:45', saved['startTime'])
            page.evaluate(f"""() => window.__ot.handleAutoRecordPush({{
                id:'{TODAY}-out-1795939999', date:'{TODAY}', type:'out', time:'19:00', epoch:1795939999000 }})""")
            page.wait_for_timeout(300)
            saved = page.evaluate(f'window.__ot.records["{TODAY}"]')
            check('이미 있는 퇴근 시각은 덮어쓰지 않음', saved['actualEnd'] == '18:10', saved['actualEnd'])

            check('자바스크립트 오류 없음', not errors, '; '.join(errors[:3]))

            # 두 번째 페이지: 이미 기록이 있는 상태에서 종료 상태 큐가 도착
            ctx2 = browser.new_context(viewport={"width": 393, "height": 852}, device_scale_factor=2,
                                       is_mobile=True, has_touch=True, locale="ko-KR",
                                       timezone_id="Asia/Seoul")
            seeded = {TODAY: {'startTime': '08:30', 'actualEnd': '', 'regularStart': '08:30',
                              'regularEnd': '17:30', 'memo': '', 'overtime': 0}}
            ctx2.add_init_script(f"localStorage.setItem('ot.local.records', {json.dumps(json.dumps(seeded))});")
            ctx2.add_init_script(f'window.__autoPendingSeed = {json.dumps(pending)};')
            ctx2.add_init_script(FAKE.replace('__TODAY__', TODAY))
            page2 = ctx2.new_page()
            page2.on('pageerror', lambda e: errors.append('p2:' + str(e)))
            page2.goto(URL, wait_until='load')
            page2.wait_for_timeout(800)
            kept = page2.evaluate(f'window.__ot.records["{TODAY}"]')
            check('앱 시작: 기존 출근 08:30 은 그대로', kept['startTime'] == '08:30', kept['startTime'])
            ack2 = page2.evaluate("window.__calls.filter(c=>c[0]==='ackAutoRecords').pop()")
            check('덮어쓰지 않은 큐도 정리(ack)', bool(ack2), json.dumps(ack2, ensure_ascii=False) if ack2 else 'none')
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


def main():
    os.makedirs(SHOTS, exist_ok=True)
    rules()
    static_checks()
    return headless()


if __name__ == '__main__':
    raise SystemExit(main())
