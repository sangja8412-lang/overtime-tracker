#!/usr/bin/env python3
"""네이티브 구성 정적 검사 (Xcode 없이 확인 가능한 부분).

검사 항목
 - Info.plist: 위치 권한 문구 3종, UIBackgroundModes=location, 번들 ID/이름
 - Swift 플러그인: CAPBridgedPlugin 등록 정보(identifier/jsName/pluginMethods)
   ↔ www/index.html 에서 호출하는 메서드가 모두 존재하는지
 - 알림 payload: type / timestamp / epoch / date 포함
 - AppDelegate: 알림 delegate 조기 설치 + 플러그인 모듈 import
 - codemagic.yaml: 워크플로·번들 ID·TestFlight 업로드 설정
 - Podfile / capacitor.config.json: GeofenceCommute 연결

실행: python3 tests/verify-native-config.py
"""
import json
import os
import plistlib
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
results = []


def check(name, ok, detail=''):
    results.append((name, bool(ok), detail))
    print(('PASS  ' if ok else 'FAIL  ') + name + (f'  — {detail}' if detail else ''))


def read(path):
    with open(os.path.join(ROOT, path), encoding='utf-8') as f:
        return f.read()


def main():
    info_path = os.path.join(ROOT, 'ios/App/App/Info.plist')
    info = plistlib.load(open(info_path, 'rb'))
    check('번들 ID', info.get('CFBundleIdentifier') == '$(PRODUCT_BUNDLE_IDENTIFIER)')
    check('앱 표시 이름', info.get('CFBundleDisplayName') == '퇴근했니?', str(info.get('CFBundleDisplayName')))
    check('NSLocationWhenInUseUsageDescription',
          'NSLocationWhenInUseUsageDescription' in info, str(info.get('NSLocationWhenInUseUsageDescription', ''))[:40])
    check('NSLocationAlwaysAndWhenInUseUsageDescription',
          'NSLocationAlwaysAndWhenInUseUsageDescription' in info, str(info.get('NSLocationAlwaysAndWhenInUseUsageDescription', ''))[:40])
    check('UIBackgroundModes = location', info.get('UIBackgroundModes') == ['location'], str(info.get('UIBackgroundModes')))
    check('수출 규정(비면제 암호화 없음)', info.get('ITSAppUsesNonExemptEncryption') is False)

    swift = read('native/geofence-commute/ios/Sources/GeofenceCommutePlugin/GeofenceCommutePlugin.swift')
    check('플러그인 @objc 이름', '@objc(GeofenceCommutePlugin)' in swift)
    check('CAPBridgedPlugin 채택', 'CAPPlugin, CAPBridgedPlugin' in swift)
    js_name = re.search(r'public let jsName = "([^"]+)"', swift)
    check('jsName = GeofenceCommute', js_name and js_name.group(1) == 'GeofenceCommute', js_name.group(1) if js_name else 'none')
    methods = re.findall(r'CAPPluginMethod\(name: "([^"]+)"', swift)
    check('플러그인 메서드 23개(기본 9 + 퇴근 독촉 3 + 기록 깜빡 2 + 자동 기록 4 + 위젯 5)', len(methods) == 23, ', '.join(methods))

    html = read('www/index.html')
    used = set(re.findall(r"P\.([a-zA-Z]+)\(", html))
    missing = sorted(m for m in used if m not in methods and m not in ('addListener',))
    check('웹에서 호출하는 플러그인 메서드가 모두 존재', not missing, f'호출: {sorted(used)} / 없음: {missing}')

    for key in ['"type": type', '"timestamp": formatter.string(from: date)', '"epoch": date.timeIntervalSince1970 * 1000.0', '"date": dateFormatter.string(from: date)']:
        check(f'알림 payload {key.split(":")[0]}', key in swift)
    check('출근 알림 문구', '회사 반경에 들어왔어요. 출근 기록할까요?' in swift)
    check('퇴근 알림 문구', '회사 반경에서 나왔어요. 퇴근 기록할까요?' in swift)
    check('CLCircularRegion 진입/이탈 감시',
          'notifyOnEntry = true' in swift and 'notifyOnExit = true' in swift and 'startMonitoring(for: region)' in swift)
    check('잠금 화면 알림(UNUserNotificationCenter)',
          'UNUserNotificationCenter.current()' in swift and 'center.delegate = self' in swift
          and 'UNTimeIntervalNotificationTrigger' in swift)
    check('알림 액션 카테고리', '"COMMUTE"' in swift and 'UNNotificationCategory' in swift)
    check('알림 탭 payload 보관 + 이벤트', 'CommuteStore.setPending(payload)' in swift and '.commuteActionTapped' in swift)
    check('반경 100~500m 범위', 'min(radius, 1000)' in swift or 'min(500' in swift)

    delegate = read('ios/App/App/AppDelegate.swift')
    check('AppDelegate: 알림 delegate 조기 설치', 'CommuteNotificationHandler.shared.start()' in delegate)
    check('AppDelegate: GeofenceCommute import', 'import GeofenceCommute' in delegate)

    cm = read('codemagic.yaml')
    check('codemagic: 번들 ID', 'com.kyeore.overtimetracker' in cm)
    check('codemagic: ASC 앱 ID', 'APP_STORE_APP_ID: "6839007688"' in cm)
    check('codemagic: TestFlight 업로드 / 외부 심사·App Store 제출 안 함',
          'submit_to_app_store: false' in cm and 'submit_to_testflight: false' in cm
          and 'beta_groups:' not in cm)
    check('codemagic: 배포 서명 타입', 'distribution_type: app_store' in cm)
    check('codemagic: 두 워크플로(ios-release, ios-check)',
          'ios-release:' in cm and 'ios-check:' in cm)

    podfile = read('ios/App/Podfile')
    check('Podfile: GeofenceCommute', "pod 'GeofenceCommute'" in podfile)
    check('Podfile: Filesystem/Share', "pod 'CapacitorFilesystem'" in podfile and "pod 'CapacitorShare'" in podfile)

    cap = json.load(open(os.path.join(ROOT, 'ios/App/App/capacitor.config.json')))
    check('capacitor.config: appId', cap.get('appId') == 'com.kyeore.overtimetracker')
    check('capacitor.config: 플러그인 등록', 'GeofenceCommutePlugin' in cap.get('packageClassList', []))

    ipa_dir = os.environ.get('IPA_DIR')
    if ipa_dir and os.path.isdir(ipa_dir):
        app_info = plistlib.load(open(os.path.join(ipa_dir, 'Payload/App.app/Info.plist'), 'rb'))
        check('IPA: 번들 ID', app_info.get('CFBundleIdentifier') == 'com.kyeore.overtimetracker')
        check('IPA: UIBackgroundModes', app_info.get('UIBackgroundModes') == ['location'])
        check('IPA: 버전/빌드', f"{app_info.get('CFBundleShortVersionString')} ({app_info.get('CFBundleVersion')})",
              f"{app_info.get('CFBundleShortVersionString')} ({app_info.get('CFBundleVersion')})")
        fw = os.listdir(os.path.join(ipa_dir, 'Payload/App.app/Frameworks'))
        check('IPA: GeofenceCommute.framework 임베드', 'GeofenceCommute.framework' in fw, ', '.join(sorted(fw)))
        raw = open(os.path.join(ipa_dir, 'Payload/App.app/embedded.mobileprovision'), 'rb').read()
        start, end = raw.find(b'<?xml'), raw.rfind(b'</plist>') + 8
        prof = plistlib.loads(raw[start:end])
        ent = prof.get('Entitlements', {})
        check('IPA: 배포 프로파일', prof.get('Name') == 'Overtime Tracker App Store'
              and ent.get('get-task-allow') is False
              and ent.get('application-identifier', '').endswith('com.kyeore.overtimetracker'),
              f"{prof.get('Name')} / {ent.get('application-identifier')}")

    failed = [r for r in results if not r[1]]
    print('\n' + '=' * 60)
    print(f'{len(results) - len(failed)}/{len(results)} 통과')
    for name, _, detail in failed:
        print(f'  실패: {name} — {detail}')
    print('=' * 60)
    return 0 if not failed else 1


if __name__ == '__main__':
    sys.exit(main())
