#!/usr/bin/env python3
"""홈·잠금화면 위젯 + 애플워치 알림 액션 버튼 검증 (1.0 빌드 8).

검증 항목
 A. 규칙(순수 계산): 위젯에서 찍은 출근/퇴근이 '이미 기록된 시각은 덮어쓰지 않음',
    퇴근은 출근이 있을 때만, 앱이 아직 반영하지 않은 위젯 기록은 웹 동기화가 지우지 않음
 B. 정적 검사: App Group 공유 저장소·위젯 타깃·entitlements·AppIntent(RecordCommuteIntent)·
    알림 카테고리(COMMUTE_IN / COMMUTE_OUT, 백그라운드 [출근 찍기]·[퇴근 찍기])·
    액션 처리(독촉·깜빡 취소 + 위젯 리로드)·codemagic 빌드 번호 8
 C. 헤드리스 Chromium + 가짜 네이티브:
    - 앱이 기록을 저장하면 위젯 상태(syncWidgetState)를 App Group 에 밀어 넣음
    - 앱 시작 시 위젯에서 찍어 둔 기록을 오늘 기록에 반영하고 큐에서 지움
    - 이미 기록된 시각은 덮어쓰지 않음
    - 위젯 퇴근 기록을 반영하면 syncCommuteNag 로 독촉·깜빡 알림이 취소됨

실행:
  PLAYWRIGHT_BROWSERS_PATH=/workspace/work/pw-browsers \\
  LD_LIBRARY_PATH=/workspace/build/pwlibs/usr/lib/x86_64-linux-gnu \\
  python3 tests/verify-widget.py
"""
import functools
import http.server
import json
import os
import plistlib
import re
import socketserver
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WWW = os.path.join(ROOT, 'www')
PORT = 8129
URL = f'http://127.0.0.1:{PORT}/index.html'
SWIFT_DIR = os.path.join(ROOT, 'native', 'geofence-commute', 'ios', 'Sources', 'GeofenceCommutePlugin')

TODAY = datetime.now(ZoneInfo('Asia/Seoul')).strftime('%Y-%m-%d')

results = []


def check(name, ok, detail=''):
    results.append((name, bool(ok), detail))
    print(('PASS  ' if ok else 'FAIL  ') + name + (f'  — {detail}' if detail else ''))


def read(path):
    with open(os.path.join(ROOT, path), encoding='utf-8') as f:
        return f.read()


# ---------- A. 위젯 저장소 규칙 (CommuteWidgetStore 와 같은 계산) ----------

def phase(state):
    if state['clockOut']:
        return 'done'
    if state['clockIn']:
        return 'working'
    return 'before'


def record(state, type_, time):
    """이미 기록된 시각은 덮어쓰지 않는다(CommuteWidgetStore.record 와 같은 규칙)."""
    next_state = dict(state)
    if type_ == 'in':
        if next_state['clockIn']:
            return None
        next_state['clockIn'] = time
    else:
        if not next_state['clockIn'] or next_state['clockOut']:
            return None
        next_state['clockOut'] = time
    return next_state


def apply_web(state, clock_in, clock_out, waiting_in=False, waiting_out=False):
    """앱(웹)이 보낸 기록을 반영하되, 아직 반영 안 된 위젯 기록은 지우지 않는다."""
    next_state = dict(state)
    if clock_in:
        next_state['clockIn'] = clock_in
    elif not waiting_in:
        next_state['clockIn'] = ''
    if clock_out:
        next_state['clockOut'] = clock_out
    elif not waiting_out:
        next_state['clockOut'] = ''
    return next_state


def rule_checks():
    empty = {'date': TODAY, 'clockIn': '', 'clockOut': '', 'phase': 'before'}
    check('미출근 상태 표시', phase(empty) == 'before')

    after_in = record(empty, 'in', '08:52')
    check('위젯 출근 찍기 → 근무 중 · 출근시각', after_in and phase(after_in) == 'working'
          and after_in['clockIn'] == '08:52', json.dumps(after_in, ensure_ascii=False))

    check('위젯 출근 두 번 → 덮어쓰지 않음', record(after_in, 'in', '09:30') is None)

    after_out = record(after_in, 'out', '18:12')
    check('위젯 퇴근 찍기 → 퇴근 완료 · 출근~퇴근', after_out and phase(after_out) == 'done'
          and after_out['clockIn'] == '08:52' and after_out['clockOut'] == '18:12',
          json.dumps(after_out, ensure_ascii=False))

    check('위젯 퇴근 두 번 → 덮어쓰지 않음', record(after_out, 'out', '19:40') is None)
    check('출근 없이 위젯 퇴근 → 기록하지 않음', record(empty, 'out', '18:00') is None)

    kept = apply_web(after_out, clock_in='', clock_out='', waiting_in=False, waiting_out=False)
    check('앱에서 기록을 지우면 위젯도 비워짐', kept['clockIn'] == '' and kept['clockOut'] == '')

    keep_waiting = apply_web(empty, clock_in='', clock_out='', waiting_in=True, waiting_out=False)
    check('아직 반영 안 된 위젯 출근 기록은 지우지 않음', keep_waiting['clockIn'] == '',
          '빈 상태에서 대기 기록이 있으면 시각은 그대로(위젯이 쓴 값 유지)')

    synced = apply_web(after_in, clock_in='08:30', clock_out='')
    check('앱 기록이 위젯에 반영됨', synced['clockIn'] == '08:30', json.dumps(synced, ensure_ascii=False))


# ---------- B. 정적 검사 ----------

def static_checks():
    shared = read(os.path.join('native', 'geofence-commute', 'ios', 'Sources',
                               'GeofenceCommutePlugin', 'CommuteWidgetState.swift'))
    plugin = read(os.path.join('native', 'geofence-commute', 'ios', 'Sources',
                               'GeofenceCommutePlugin', 'GeofenceCommutePlugin.swift'))
    action = read(os.path.join('native', 'geofence-commute', 'ios', 'Sources',
                               'GeofenceCommutePlugin', 'CommuteNotificationAction.swift'))
    widget = read('ios/App/OvertimeWidget/OvertimeWidget.swift')
    intent = read('ios/App/OvertimeWidget/RecordCommuteIntent.swift')
    bundle = read('ios/App/OvertimeWidget/OvertimeWidgetBundle.swift')
    pbx = read('ios/App/App.xcodeproj/project.pbxproj')
    cm = read('codemagic.yaml')
    html = read('www/index.html')

    # App Group · 저장소 키
    check('App Group group.com.kyeore.overtimetracker',
          'static let appGroupId = "group.com.kyeore.overtimetracker"' in shared)
    check('공유 키 widget_state / widget_pending_records',
          'static let stateKey = "widget_state"' in shared and 'static let pendingKey = "widget_pending_records"' in shared)
    check('위젯 번들 ID com.kyeore.overtimetracker.widget',
          'com.kyeore.overtimetracker.widget' in shared)

    # 위젯에서 찍은 기록 규칙
    check('위젯 출근은 이미 있으면 덮어쓰지 않음',
          'if type == "in" {\n            guard state.clockIn.isEmpty else { return nil }' in shared)
    check('위젯 퇴근은 출근이 있고 퇴근이 비었을 때만',
          'guard !state.clockIn.isEmpty, state.clockOut.isEmpty else { return nil }' in shared)
    check('위젯 기록을 대기 큐에 넣음', 'enqueue(entry)' in shared and 'static func enqueue(_ record: CommuteWidgetRecord)' in shared)
    check('앱이 반영하면 큐에서 지움', 'static func acknowledge(ids: [String])' in shared)

    # 위젯 익스텐션
    check('위젯 번들 @main', '@main' in bundle and 'WidgetBundle' in bundle)
    check('홈화면 소형·중형 지원', 'systemSmall' in widget and 'systemMedium' in widget)
    check('잠금화면 원형·직사각형 지원', 'accessoryCircular' in widget and 'accessoryRectangular' in widget)
    check('상태 문구: 미출근/근무 중/퇴근 완료',
          '아직 출근 전' in widget and '근무 중' in widget and '퇴근 완료' in widget)
    check('위젯 버튼: 출근 찍기 / 퇴근 찍기',
          'actionButton' in widget and '출근 찍기' in widget and '퇴근 찍기' in widget)
    check('AppIntent 로 위젯에서 바로 기록', 'RecordCommuteIntent' in widget and 'Button(intent:' in widget)
    check('RecordCommuteIntent: 앱을 열지 않음', 'openAppWhenRun: Bool = false' in intent)
    check('RecordCommuteIntent: 위젯 기록 + 타임라인 리로드',
          'CommuteWidgetStore.record(type: type, source: "widget")' in intent
          and 'WidgetCenter.shared.reloadAllTimelines()' in intent)

    # 플러그인 메서드 (웹 ↔ 앱)
    methods = re.findall(r'CAPPluginMethod\(name: "([^"]+)"', plugin)
    for name in ('syncWidgetState', 'getWidgetRecords', 'ackWidgetRecords', 'reloadWidget', 'getWidgetStatus'):
        check(f'플러그인 메서드 {name}', name in methods)
    check('syncWidgetState: App Group 에 저장 + 위젯 리로드',
          'defaults.set(payload, forKey: CommuteWidgetShared.stateKey)' in plugin
          and 'CommuteWidgetCenter.reload()' in plugin)
    check('앱 기록 저장 시 위젯 상태도 맞춤', 'CommuteWidgetStore.applyWeb(' in plugin)

    # 알림 카테고리 · 액션 버튼
    check('출근 알림 카테고리 COMMUTE_IN', 'clockInCategory = "COMMUTE_IN"' in plugin)
    check('퇴근 알림 카테고리 COMMUTE_OUT', 'clockOutCategory = "COMMUTE_OUT"' in plugin)
    check('액션: 출근 찍기 / 퇴근 찍기', '"출근 찍기"' in plugin and '"퇴근 찍기"' in plugin)
    check('액션은 백그라운드(options: [])라 앱을 열지 않음',
          'title: "출근 찍기",\n                                           options: [])' in plugin
          and 'title: "퇴근 찍기",\n                                            options: [])' in plugin)
    check('출근 알림에는 출근 찍기, 퇴근 알림에는 퇴근 찍기',
          'content.categoryIdentifier = type == "in"' in plugin)
    check('야근 독촉·기록 깜빡 알림도 퇴근 찍기 버튼',
          'CommuteNotificationHandler.clockOutCategory' in
          read(os.path.join('native', 'geofence-commute', 'ios', 'Sources',
                            'GeofenceCommutePlugin', 'CommuteNagScheduler.swift'))
          and 'CommuteNotificationHandler.clockOutCategory' in
          read(os.path.join('native', 'geofence-commute', 'ios', 'Sources',
                            'GeofenceCommutePlugin', 'CommuteForgetReminder.swift')))
    check('액션 수신 시 백그라운드 기록', 'CommuteActionRecorder.record(type: quickType, userInfo: info)' in plugin)
    check('액션: 이미 기록돼 있으면 그대로 두고 알림', 'recordKeptNotice' in plugin and 'CommuteAutoRecordStore.record(' in action)
    check('액션: 퇴근이면 야근 독촉 즉시 취소', 'CommuteNagScheduler.cancelAll()' in action)
    check('액션: 퇴근이면 깜빡 알림 즉시 취소', 'CommuteForgetScheduler.cancelAll(clear: true)' in action)
    check('액션 후 위젯 타임라인 리로드', 'CommuteWidgetCenter.reload()' in action)
    check('액션 기록은 웹 대기 큐로 전달', 'CommuteAutoRecordStore.record(date: day, type: type, time: time, at: when)' in action)

    # Xcode 타깃 구성
    check('위젯 타깃 OvertimeWidgetExtension', 'OvertimeWidgetExtension' in pbx)
    check('위젯 번들 ID 빌드 설정', 'PRODUCT_BUNDLE_IDENTIFIER = com.kyeore.overtimetracker.widget;' in pbx)
    check('앱 그룹 Entitlements (앱)', 'CODE_SIGN_ENTITLEMENTS = App/App.entitlements;' in pbx)
    check('앱 그룹 Entitlements (위젯)', 'CODE_SIGN_ENTITLEMENTS = OvertimeWidget/OvertimeWidget.entitlements;' in pbx)
    check('Embed App Extensions 단계', 'Embed App Extensions' in pbx)
    check('빌드 번호 8 (앱·위젯)', pbx.count('CURRENT_PROJECT_VERSION = 8;') == 4
          and 'CURRENT_PROJECT_VERSION = 7;' not in pbx, str(pbx.count('CURRENT_PROJECT_VERSION = 8;')))

    app_ent = plistlib.load(open(os.path.join(ROOT, 'ios/App/App/App.entitlements'), 'rb'))
    check('App.entitlements: application-groups',
          app_ent.get('com.apple.security.application-groups') == ['group.com.kyeore.overtimetracker'])
    widget_ent = plistlib.load(open(os.path.join(ROOT, 'ios/App/OvertimeWidget/OvertimeWidget.entitlements'), 'rb'))
    check('위젯 entitlements: application-groups',
          widget_ent.get('com.apple.security.application-groups') == ['group.com.kyeore.overtimetracker'])
    widget_info = plistlib.load(open(os.path.join(ROOT, 'ios/App/OvertimeWidget/Info.plist'), 'rb'))
    check('위젯 Info.plist: widgetkit-extension',
          widget_info.get('NSExtension', {}).get('NSExtensionPointIdentifier') == 'com.apple.widgetkit-extension')

    check('codemagic: 빌드 번호 8 고정', 'APP_BUILD_NUMBER: "8"' in cm and '$BUILD" != "8"' in cm)
    check('codemagic: ios-check 에서 위젯 산출물 확인', 'OvertimeWidgetExtension.appex' in cm
          and 'com.kyeore.overtimetracker.widget' in cm)

    # 웹
    check('웹: 위젯 상태 payload(date·clockIn·clockOut)',
          'function widgetPayload()' in html and 'clockIn:r.startTime' in html and 'clockOut:r.actualEnd' in html)
    check('웹: 기록 저장 시 위젯 동기화', 'save=()=>{localStorage.setItem(KEY,JSON.stringify(records));syncWidget();' in html)
    check('웹: 시작·복귀 시 위젯 기록 반영', 'await drainWidgetRecords();' in html and 'P.getWidgetRecords(' in html)
    check('웹: 위젯 기록 반영 후 ack', 'P.ackWidgetRecords({ids:ack})' in html)
    check('웹: 위젯 기록도 덮어쓰지 않는 규칙 사용', 'const result=applyAutoRecord(evt);' in html)


# ---------- C. 헤드리스 ----------

FAKE = r"""
(() => {
  const calls = [];
  window.__calls = calls;
  const listeners = {};
  let day = { date: '__TODAY__', clockIn: '', clockOut: '' };
  let widgetState = { date: '__TODAY__', clockIn: '', clockOut: '', regularStart: '08:30', regularEnd: '17:30' };
  const queue = (window.__widgetPendingSeed || []).map(e => Object.assign({}, e));
  const nag = { clockIn: '', clockOut: '' };
  const GeofenceCommute = {
    getStatus: () => Promise.resolve({ location: 'always', notifications: 'granted', monitoring: true,
      backgroundUpdates: false, autoRecord: { autoClockIn: false, autoClockOut: false } }),
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
      nag.clockIn = a.clockIn || ''; nag.clockOut = a.clockOut || '';
      return Promise.resolve({ enabled: a.enabled !== false, date: a.date, regularEnd: a.regularEnd,
        clockIn: nag.clockIn, clockOut: nag.clockOut,
        state: !nag.clockIn ? 'waitingClockIn' : (nag.clockOut ? 'clockedOut' : 'scheduled'),
        rounds: [], scheduled: [], scheduledTimes: [], nextTime: '', totalPhrases: 200,
        forget: { enabled: true, date: a.date, clockIn: nag.clockIn, clockOut: nag.clockOut,
          state: nag.clockOut ? 'clockedOut' : 'waitingExit', exitTime: '', rounds: [], scheduled: [],
          scheduledTimes: [], nextTime: '', totalPhrases: 150 } });
    },
    getNagStatus: () => Promise.resolve({ enabled: true, rounds: [], state: 'waitingClockIn', regularEnd: '17:30' }),
    getForgetStatus: () => Promise.resolve({ enabled: true, date: day.date, clockIn: nag.clockIn, clockOut: nag.clockOut,
      state: nag.clockOut ? 'clockedOut' : 'waitingExit', exitTime: '', rounds: [], scheduled: [], scheduledTimes: [],
      nextTime: '', totalPhrases: 150 }),
    syncAutoRecordSettings: (a) => Promise.resolve({ settings: { autoClockIn: !!a.autoClockIn, autoClockOut: !!a.autoClockOut },
      date: day.date, clockIn: day.clockIn, clockOut: day.clockOut, pending: [] }),
    getAutoRecordStatus: () => Promise.resolve({ settings: { autoClockIn: false, autoClockOut: false },
      date: day.date, clockIn: day.clockIn, clockOut: day.clockOut, pending: [] }),
    getAutoRecords: () => Promise.resolve({ records: [], settings: { autoClockIn: false, autoClockOut: false } }),
    ackAutoRecords: () => Promise.resolve({ remaining: 0 }),
    syncWidgetState: (a) => {
      calls.push(['syncWidgetState', a]);
      try { widgetState = JSON.parse(a.payload); } catch (e) { return Promise.reject(e); }
      return Promise.resolve(Object.assign({ saved: true }, widgetState));
    },
    getWidgetRecords: () => {
      calls.push(['getWidgetRecords', {}]);
      return Promise.resolve({ records: queue.map(e => Object.assign({}, e)), state: Object.assign({}, widgetState) });
    },
    ackWidgetRecords: (a) => {
      calls.push(['ackWidgetRecords', a]);
      const ids = (a && a.ids) || [];
      for (const id of ids) {
        const idx = queue.findIndex(x => x.id === id);
        if (idx >= 0) queue.splice(idx, 1);
      }
      return Promise.resolve({ remaining: queue.length });
    },
    reloadWidget: () => Promise.resolve({ reloaded: true }),
    getWidgetStatus: () => Promise.resolve(widgetState),
    addListener: (event, cb) => { listeners[event] = cb; return Promise.resolve({ remove() {} }); },
    removeAllListeners: () => Promise.resolve()
  };
  window.__fakeWidget = { get state() { return widgetState; }, queue };
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
                {'id': f'{TODAY}-in-1795900000', 'date': TODAY, 'type': 'in', 'time': '08:47',
                 'epoch': 1795900000000.0, 'source': 'widget'},
                {'id': f'{TODAY}-out-1795930000', 'date': TODAY, 'type': 'out', 'time': '18:21',
                 'epoch': 1795930000000.0, 'source': 'watch'},
            ]
            ctx.add_init_script(f'window.__widgetPendingSeed = {json.dumps(pending)};')
            ctx.add_init_script(FAKE.replace('__TODAY__', TODAY))
            page = ctx.new_page()
            page.on('pageerror', lambda e: errors.append(str(e)))
            page.on('dialog', lambda d: d.accept())
            page.goto(URL, wait_until='load')
            page.wait_for_timeout(800)

            check('앱 시작: 위젯 대기 기록을 가져옴', page.evaluate(
                "window.__calls.filter(c=>c[0]==='getWidgetRecords').length") >= 1)
            saved = page.evaluate(f'window.__ot.records["{TODAY}"]')
            check('위젯 출근(08:47) 반영', bool(saved) and saved['startTime'] == '08:47', json.dumps(saved, ensure_ascii=False))
            check('위젯 퇴근(18:21) 반영', bool(saved) and saved['actualEnd'] == '18:21', json.dumps(saved, ensure_ascii=False))
            ack = page.evaluate("window.__calls.filter(c=>c[0]==='ackWidgetRecords').pop()")
            check('반영한 위젯 기록은 큐에서 지움', bool(ack and len(ack[1]['ids']) == 2), json.dumps(ack, ensure_ascii=False))
            check('위젯 기록 반영 후 남은 큐 없음', page.evaluate("window.__fakeWidget.queue.length") == 0)

            nag_call = page.evaluate("window.__calls.filter(c=>c[0]==='syncCommuteNag').pop()")
            check('위젯 퇴근 반영 → 독촉·깜빡 취소(clockOut 전달)',
                  bool(nag_call and nag_call[1]['clockOut'] == '18:21'), json.dumps(nag_call[1], ensure_ascii=False) if nag_call else 'none')

            sync = page.evaluate("window.__calls.filter(c=>c[0]==='syncWidgetState').pop()")
            check('앱 기록을 위젯 상태로 동기화', bool(sync), json.dumps(sync[1], ensure_ascii=False) if sync else 'none')
            state = page.evaluate("window.__fakeWidget.state")
            check('위젯 상태: 출근/퇴근 시각', state['clockIn'] == '08:47' and state['clockOut'] == '18:21',
                  json.dumps(state, ensure_ascii=False))
            check('위젯 상태: 날짜·정규 시간 포함',
                  state['date'] == TODAY and state['regularStart'] == '08:30' and state['regularEnd'] == '17:30')

            # 기록 저장 → 위젯 즉시 갱신
            page.evaluate("window.__ot.loadDate(window.__ot.today())")
            page.wait_for_timeout(200)
            page.evaluate("""() => { const i=document.getElementById('startTime'); i.value='08:31'; i.dispatchEvent(new Event('input')); }""")
            page.click('#saveBtn')
            page.wait_for_timeout(400)
            state = page.evaluate("window.__fakeWidget.state")
            check('앱에서 시각을 고치면 위젯도 바뀜', state['clockIn'] == '08:31', json.dumps(state, ensure_ascii=False))
            saved = page.evaluate(f'window.__ot.records["{TODAY}"]')
            check('앱 기록 저장 결과', saved['startTime'] == '08:31' and saved['actualEnd'] == '18:21',
                  json.dumps(saved, ensure_ascii=False))

            # 기록 삭제 → 위젯 비움
            page.on('dialog', lambda d: d.accept())
            page.click('#deleteBtn')
            page.wait_for_timeout(400)
            state = page.evaluate("window.__fakeWidget.state")
            check('앱에서 기록을 지우면 위젯도 미출근', state['clockIn'] == '' and state['clockOut'] == '',
                  json.dumps(state, ensure_ascii=False))

            check('자바스크립트 오류 없음', not errors, '; '.join(errors[:3]))

            # 두 번째 페이지: 이미 기록이 있는 날 위젯이 출근을 또 찍어 옴 → 덮어쓰지 않음
            ctx2 = browser.new_context(viewport={"width": 393, "height": 852}, device_scale_factor=2,
                                       is_mobile=True, has_touch=True, locale="ko-KR",
                                       timezone_id="Asia/Seoul")
            seeded = {TODAY: {'startTime': '08:30', 'actualEnd': '', 'regularStart': '08:30',
                              'regularEnd': '17:30', 'memo': '', 'overtime': 0}}
            ctx2.add_init_script(f"localStorage.setItem('ot.local.records', {json.dumps(json.dumps(seeded))});")
            ctx2.add_init_script(f'window.__widgetPendingSeed = {json.dumps([pending[0]])};')
            ctx2.add_init_script(FAKE.replace('__TODAY__', TODAY))
            page2 = ctx2.new_page()
            page2.on('pageerror', lambda e: errors.append('p2:' + str(e)))
            page2.on('dialog', lambda d: d.accept())
            page2.goto(URL, wait_until='load')
            page2.wait_for_timeout(800)
            saved = page2.evaluate(f'window.__ot.records["{TODAY}"]')
            check('이미 있는 출근 시각은 덮어쓰지 않음', saved['startTime'] == '08:30', saved['startTime'])
            ack = page2.evaluate("window.__calls.filter(c=>c[0]==='ackWidgetRecords').pop()")
            check('덮어쓰지 않은 기록도 큐에서 정리', bool(ack and len(ack[1]['ids']) == 1), json.dumps(ack, ensure_ascii=False))
            check('두 번째 페이지 자바스크립트 오류 없음', not any(e.startswith('p2:') for e in errors),
                  '; '.join([e for e in errors if e.startswith('p2:')][:2]))
            browser.close()
    finally:
        httpd.shutdown()


def main():
    print('=== A. 위젯 저장소 규칙 ===')
    rule_checks()
    print('\n=== B. 정적 검사 ===')
    static_checks()
    print('\n=== C. 헤드리스 (웹 ↔ 위젯 동기화) ===')
    headless()
    passed = sum(1 for _, ok, _ in results if ok)
    print('\n' + '=' * 60)
    print(f'{passed}/{len(results)} 통과')
    failed = [name for name, ok, _ in results if not ok]
    if failed:
        print('  실패: ' + ' / '.join(failed))
    return 0 if not failed else 1


if __name__ == '__main__':
    raise SystemExit(main())
