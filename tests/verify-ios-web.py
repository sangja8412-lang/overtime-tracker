#!/usr/bin/env python3
"""퇴근했니? iOS 웹 레이어 검증 (헤드리스 Chromium).

검증 항목
 1) 기존 웹앱 기능 유지: 즉시 출퇴근, 조기 출근/퇴근 후 계산, 달력, 월 합계, 엑셀 복사용 TSV/CSV
 2) 기존 기록 가져오기: 브라우저 CSV 가져오기, 기존 날짜 유지, 덮어쓰기 옵션, JSON 백업 가져오기
 3) 알림 탭 시뮬레이션: 알림 시각 기본값 채움 / 저장 전에는 기록 안 됨 / 저장 시에만 반영
 4) 기존 시간 덮어쓰기 방지: 이미 기록된 시각은 자동으로 바뀌지 않고 명시적 버튼 + 저장 필요
 5) 달력에서 저장된 기록 수정

실행: PLAYWRIGHT_BROWSERS_PATH=/workspace/work/pw-browsers python3 tests/verify-ios-web.py
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
PORT = 8123
URL = f'http://127.0.0.1:{PORT}/index.html'
SHOTS = os.path.join(ROOT, 'tests', 'screens')

results = []


def check(name, ok, detail=''):
    results.append((name, bool(ok), detail))
    print(('PASS  ' if ok else 'FAIL  ') + name + (f'  — {detail}' if detail else ''))


def serve():
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=WWW)
    socketserver.TCPServer.allow_reuse_address = True
    httpd = socketserver.TCPServer(('127.0.0.1', PORT), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


CSV_BROWSER = """\ufeff"날짜","정규 출근","실제 출근","정규 퇴근","실제 퇴근","조기 출근(분)","퇴근 후(분)","총 초과근무(분)","메모"
"2026-11-03","08:30","08:05","17:30","19:00",25,90,115,"월말 마감"
"2026-11-04","08:30","08:30","17:30","18:10",0,40,40,""
"2026-11-10","08:30","09:10","17:30","20:15",0,165,165,"긴급 대응, 야근"
"""

JSON_BACKUP = json.dumps({
    "app": "overtime-tracker", "version": 1,
    "settings": {"startTime": "08:30", "regularEnd": "17:30"},
    "records": {
        "2026-11-12": {"startTime": "08:20", "actualEnd": "19:25",
                       "regularStart": "08:30", "regularEnd": "17:30", "memo": "백업에서"}
    }
}, ensure_ascii=False)


def main():
    os.makedirs(SHOTS, exist_ok=True)
    httpd = serve()
    console_errors = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            ctx = browser.new_context(viewport={"width": 393, "height": 852},
                                      device_scale_factor=2, is_mobile=True, has_touch=True,
                                      locale="ko-KR", timezone_id="Asia/Seoul")
            page = ctx.new_page()
            page.on('console', lambda m: console_errors.append(m.text) if m.type == 'error' else None)
            page.on('pageerror', lambda e: console_errors.append(str(e)))
            page.goto(URL, wait_until='load')
            page.wait_for_timeout(500)

            # ---------- 1. 기존 기능 ----------
            check('카피바라 이미지 표시', page.eval_on_selector('.mascot', 'img => img.complete && img.naturalWidth > 0'))
            check('탭 3개(출퇴근/달력/설정)', page.eval_on_selector_all('.app-nav button', 'bs => bs.map(b=>b.textContent)') == ['출퇴근', '달력', '설정'])

            page.click('#quickStart')
            page.click('#quickBtn')
            today = page.evaluate('window.__ot.today()')
            rec = page.evaluate(f'window.__ot.records["{today}"] || null')
            check('지금 출근/퇴근 즉시 기록', rec is not None and rec['startTime'] and rec['actualEnd'],
                  json.dumps(rec, ensure_ascii=False) if rec else 'no record')

            # 조기 출근 / 퇴근 후 계산: 07:30~19:00, 정규 08:30~17:30 → 조기 60분, 이후 90분
            page.fill('#dateInput', '2026-11-20')
            page.dispatch_event('#dateInput', 'change')
            page.fill('#startTime', '07:30')
            page.fill('#actualEnd', '19:00')
            page.fill('#regularStart', '08:30')
            page.fill('#regularEnd', '17:30')
            page.click('#saveBtn')
            calc = page.evaluate('''() => { const r = window.__ot.records["2026-11-20"]; return r.overtime; }''')
            check('조기 출근 60분 + 퇴근 후 90분 = 150분', calc == 150, f'총 {calc}분')
            page.screenshot(path=os.path.join(SHOTS, '01-record.png'), full_page=True)

            page.click('#calendarTab')
            page.wait_for_timeout(200)
            days = page.eval_on_selector_all('.cal-day', 'bs => bs.length')
            has_rec = page.eval_on_selector_all('.cal-day.has-record', 'bs => bs.length')
            check('달력 렌더링 + 기록 표시', days >= 28 and has_rec >= 1, f'{days}칸, 기록 {has_rec}일')

            month = page.inner_text('#monthSum')
            check('월 합계 표시', 'h' in month, month)

            tsv = page.evaluate('window.__ot.tsv()')
            check('엑셀 복사용 TSV 헤더', tsv.split('\n')[0].startswith('날짜\t정규 출근'), tsv.split('\n')[0][:60])
            csv = page.evaluate('window.__ot.csv()')
            check('CSV 헤더/구분자', csv.startswith('"날짜","정규 출근"'), csv.split('\n')[0][:60])

            # ---------- 2. 가져오기 ----------
            page.click('#settingsTab')
            page.wait_for_timeout(200)
            page.evaluate('(t) => window.__ot.importText(t, "browser.csv")', CSV_BROWSER)
            page.wait_for_timeout(200)
            imported = page.evaluate('''() => ({r: window.__ot.records, msg: document.getElementById('importResult').textContent})''')
            r = imported['r']
            ok = (r['2026-11-03']['startTime'] == '08:05' and r['2026-11-03']['actualEnd'] == '19:00'
                  and r['2026-11-03']['memo'] == '월말 마감'
                  and r['2026-11-10']['memo'] == '긴급 대응, 야근'
                  and r['2026-11-10']['actualEnd'] == '20:15')
            check('브라우저 CSV 가져오기(3건)', ok, imported['msg'])
            check('가져온 기록 초과근무 계산', page.evaluate('window.__ot.records["2026-11-03"].overtime') == 115,
                  str(page.evaluate('window.__ot.records["2026-11-03"].overtime')))

            # 기존 날짜는 유지 (덮어쓰기 체크 없이)
            before_1120 = page.evaluate('JSON.stringify(window.__ot.records["2026-11-20"])')
            page.evaluate('''(t) => window.__ot.importText('날짜,정규 출근,실제 출근,정규 퇴근,실제 퇴근,메모\\n2026-11-20,08:30,09:59,17:30,21:00,덮어쓰면 안 됨', 'x.csv')''', None)
            page.wait_for_timeout(150)
            after_1120 = page.evaluate('JSON.stringify(window.__ot.records["2026-11-20"])')
            msg = page.inner_text('#importResult')
            check('기존 기록은 덮어쓰지 않음', before_1120 == after_1120 and '건너뜀' in msg, msg)

            # 덮어쓰기 옵션을 켜면 반영
            page.check('#importOverwrite')
            page.evaluate('''() => window.__ot.importText('날짜,정규 출근,실제 출근,정규 퇴근,실제 퇴근,메모\\n2026-11-20,08:30,09:59,17:30,21:00,덮어씀', 'x.csv')''')
            page.wait_for_timeout(150)
            overwritten = page.evaluate('window.__ot.records["2026-11-20"]')
            check('덮어쓰기 옵션 시 반영', overwritten['startTime'] == '09:59' and overwritten['memo'] == '덮어씀',
                  json.dumps(overwritten, ensure_ascii=False))
            page.uncheck('#importOverwrite')

            # JSON 백업 가져오기
            page.evaluate('(t) => window.__ot.importText(t, "backup.json")', JSON_BACKUP)
            page.wait_for_timeout(150)
            jr = page.evaluate('window.__ot.records["2026-11-12"]')
            check('JSON 백업 가져오기', jr and jr['actualEnd'] == '19:25' and jr['memo'] == '백업에서',
                  json.dumps(jr, ensure_ascii=False))
            page.screenshot(path=os.path.join(SHOTS, '02-settings-import.png'), full_page=True)

            # ---------- 3. 알림 탭 시뮬레이션 ----------
            page.evaluate('''() => {
                localStorage.clear();
                location.reload();
            }''')
            page.wait_for_timeout(700)

            # 11-25 18:31 퇴근 알림 (기록 없는 날)
            page.evaluate('''() => window.__ot.handleCommuteAction({type:'out', timestamp:'2026-11-25T18:31:00+09:00', epoch: Date.parse('2026-11-25T18:31:00+09:00'), date:'2026-11-25'})''')
            page.wait_for_timeout(200)
            banner_visible = page.is_visible('#actionBanner')
            field_val = page.input_value('#actualEnd')
            date_val = page.input_value('#dateInput')
            stored = page.evaluate('window.__ot.records["2026-11-25"] || null')
            check('알림 탭 → 퇴근 칸에 알림 시각(18:31) 기본값', banner_visible and field_val == '18:31',
                  f'banner={banner_visible}, actualEnd={field_val}, date={date_val}')
            check('알림 탭만으로는 기록되지 않음(저장 전)', stored is None, json.dumps(stored, ensure_ascii=False))
            check('배너 안내 문구', '저장' in page.inner_text('#bannerMsg'), page.inner_text('#bannerMsg'))
            page.screenshot(path=os.path.join(SHOTS, '03-notification-prefill.png'), full_page=True)

            # 사용자가 시간을 수정한 뒤 저장
            page.fill('#actualEnd', '18:47')
            page.click('#saveBtn')
            page.wait_for_timeout(150)
            saved = page.evaluate('window.__ot.records["2026-11-25"]')
            check('사용자가 수정한 시각으로 저장(18:47)', saved and saved['actualEnd'] == '18:47',
                  json.dumps(saved, ensure_ascii=False))
            check('저장 후 배너 닫힘', not page.is_visible('#actionBanner'))

            # 같은 날 다시 알림 (퇴근 19:05) → 기존 18:47 유지
            page.evaluate('''() => window.__ot.handleCommuteAction({type:'out', timestamp:'2026-11-25T19:05:00+09:00', date:'2026-11-25'})''')
            page.wait_for_timeout(200)
            kept_val = page.input_value('#actualEnd')
            apply_visible = page.is_visible('#bannerApply')
            check('이미 기록된 시간은 자동으로 덮어쓰지 않음', kept_val == '18:47' and apply_visible,
                  f'actualEnd={kept_val}, 변경버튼={apply_visible}')
            check('덮어쓰기 방지 안내', '자동으로 바꾸지 않았' in page.inner_text('#bannerMsg'), page.inner_text('#bannerMsg'))
            page.screenshot(path=os.path.join(SHOTS, '04-no-overwrite.png'), full_page=True)

            # 명시적 버튼을 눌러야만 알림 시각으로 변경 (저장은 별도)
            page.click('#bannerApply')
            page.wait_for_timeout(150)
            applied = page.input_value('#actualEnd')
            still_saved = page.evaluate('window.__ot.records["2026-11-25"].actualEnd')
            check('명시적 버튼으로만 알림 시각 반영', applied == '19:05', f'입력칸={applied}')
            check('버튼 눌러도 저장 전에는 기록 그대로', still_saved == '18:47', still_saved)
            page.click('#saveBtn')
            page.wait_for_timeout(150)
            check('저장 후 19:05로 갱신', page.evaluate('window.__ot.records["2026-11-25"].actualEnd') == '19:05')

            # 출근 알림: startTime 이 비어 있는 날
            page.evaluate('''() => window.__ot.handleCommuteAction({type:'in', timestamp:'2026-11-24T08:31:00+09:00', date:'2026-11-24'})''')
            page.wait_for_timeout(200)
            in_val = page.input_value('#startTime')
            check('출근 알림 → 출근 칸 기본값(08:31)', in_val == '08:31', in_val)
            check('출근 알림은 퇴근 칸을 건드리지 않음', page.input_value('#actualEnd') == '', page.input_value('#actualEnd'))
            page.click('#saveBtn')
            page.wait_for_timeout(150)
            check('출근 알림 저장', page.evaluate('window.__ot.records["2026-11-24"].startTime') == '08:31')

            # 날짜가 다른 알림(과거 날짜)도 해당 날짜로 이동
            page.evaluate('''() => window.__ot.handleCommuteAction({type:'out', timestamp:'2026-11-10T20:40:00+09:00', date:'2026-11-10'})''')
            page.wait_for_timeout(250)
            check('다른 날짜 알림 → 그 날짜로 이동', page.input_value('#dateInput') == '2026-11-10'
                  and page.input_value('#actualEnd') == '20:40',
                  f'{page.input_value("#dateInput")} / {page.input_value("#actualEnd")}')

            # ---------- 4. 달력에서 수정 ----------
            page.click('#calendarTab')
            page.wait_for_timeout(250)
            page.evaluate('''() => { const bs=[...document.querySelectorAll('.cal-day')]; const b=bs.find(x=>x.textContent.trim().startsWith('23')); if(b) b.click(); }''')
            page.wait_for_timeout(200)
            edit_btn = page.inner_text('#editCalendarDay')
            check('달력 상세에 기록 수정 버튼', edit_btn in ('기록 수정', '기록 추가'), edit_btn)
            page.click('#editCalendarDay')
            page.wait_for_timeout(200)
            check('달력 → 출퇴근 탭 이동 + 날짜 선택', page.is_visible('#recordPanel') and page.input_value('#dateInput').endswith('-23'),
                  page.input_value('#dateInput'))
            page.fill('#startTime', '07:55')
            page.fill('#actualEnd', '18:05')
            page.click('#saveBtn')
            page.wait_for_timeout(150)
            edited = page.evaluate('window.__ot.records[document.getElementById("dateInput").value]')
            check('달력에서 연 기록 수정 저장', edited['startTime'] == '07:55' and edited['actualEnd'] == '18:05',
                  json.dumps(edited, ensure_ascii=False))
            page.screenshot(path=os.path.join(SHOTS, '05-calendar-edit.png'), full_page=True)

            # ---------- 5. 설정 화면 (웹 모드 안내) ----------
            page.click('#settingsTab')
            page.wait_for_timeout(200)
            perm = page.inner_text('#permStatus')
            check('웹 미리보기에서는 권한 상태 안내', '웹 미리보기' in perm, perm.replace('\n', ' | '))
            page.fill('#companyLat', '37.241600')
            page.fill('#companyLon', '127.141400')
            page.click('#saveGeofence')
            page.wait_for_timeout(300)
            page.on('dialog', lambda d: d.accept())
            geo = page.evaluate('localStorage.getItem("ot.geofence.v1")')
            check('회사 위치 저장(웹)', geo is not None and '37.2416' in geo, str(geo))

            check('콘솔 오류 없음', not console_errors, '; '.join(console_errors[:3]))
            browser.close()
    finally:
        httpd.shutdown()

    failed = [r for r in results if not r[1]]
    print('\n' + '=' * 60)
    print(f'{len(results) - len(failed)}/{len(results)} 통과')
    if failed:
        for name, _, detail in failed:
            print(f'  실패: {name} — {detail}')
    print('=' * 60)
    return 0 if not failed else 1


if __name__ == '__main__':
    raise SystemExit(main())
