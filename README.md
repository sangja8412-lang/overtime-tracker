# 퇴근했니? — iOS 앱 (Capacitor + 네이티브 지오펜스)

기존 웹앱(v4, `main` 브랜치의 `index.html`)을 아이폰 앱으로 감싸고, **회사 반경 진입/이탈을 잠금 화면 알림으로 알려 주는** 네이티브 기능을 더한 버전입니다.
`main` 브랜치(웹앱/PWA)는 그대로 두고, 이 작업은 `ios-testflight` 브랜치에서만 이루어졌습니다.

## 구성

| 경로 | 설명 |
|---|---|
| `www/index.html` | 앱 웹 레이어. 기존 웹앱 기능(카피바라·골드 디자인, 즉시 출퇴근, 직접 입력·수정, 조기 출근/퇴근 후 초과근무, 달력, 월 합계, 엑셀 복사·CSV/JSON 저장) + 설정 탭 + 가져오기 + 알림 배너 |
| `native/geofence-commute/` | 로컬 Capacitor 플러그인 `GeofenceCommute` (Swift). `CLLocationManager` 영역 감시 + `UNUserNotificationCenter` 로컬 알림 |
| `ios/` | Capacitor iOS 프로젝트 (`com.kyeore.overtimetracker`) |
| `codemagic.yaml` | Codemagic 워크플로 (`ios-check` 시뮬레이터 빌드, `ios-release` TestFlight 업로드) |
| `resources/icon.png` | 앱 아이콘 원본(1024) — `office-capybara.webp`에서 생성 |

## 네이티브 플러그인 API (`GeofenceCommute`)

| 메서드 | 설명 |
|---|---|
| `getStatus()` | 위치 권한(always/whenInUse/denied/notDetermined), 알림 권한, 감시 여부, 저장된 회사 위치 |
| `requestLocationPermission()` | 위치 권한 요청(필요 시 '항상 허용' 승격 요청) |
| `requestNotificationPermission()` | 알림 권한 요청 |
| `getCurrentLocation()` | 현재 위도·경도 1회 조회 |
| `setGeofence({latitude, longitude, radius, label})` | 회사 위치 저장 + `CLCircularRegion` 감시 시작 |
| `clearGeofence()` | 감시 중지 + 저장 위치 삭제 |
| `getPendingAction()` | 앱이 꺼진 상태에서 알림을 눌렀을 때의 대기 payload (`type`, `timestamp`, `epoch`, `date`) |
| `clearDelivered()` | 알림 센터에 남은 알림 정리 |
| `testNotification({type, delay})` | 실제 이동 없이 같은 알림을 예약(알림 탭 → 입력 흐름 확인용) |
| `syncCommuteNag({date, clockIn, clockOut, regularEnd, enabled})` | 오늘 출퇴근 기록과 정규 퇴근시간을 넘겨 **퇴근 독촉 알림** 예약을 다시 맞춤 |
| `getNagStatus()` | 오늘의 독촉 상태(예약됨/출근 전/퇴근 완료/이탈/꺼짐)와 4회 문구·시각 |
| `testNagNotification({round, delay})` | 1분 뒤 오늘 문구로 독촉 알림 테스트 |
| 이벤트 `commuteAction` | 앱이 떠 있는 상태에서 알림을 눌렀을 때 전달 |

알림 payload에는 `type`(`in`/`out`)과 알림 발생 시각(`timestamp` ISO8601, `epoch` ms, `date` yyyy-MM-dd)이 들어갑니다.
탭하면 해당 날짜의 출퇴근 입력 화면이 열리고 **알림 시각이 기본값으로 채워지며**, `기록 저장`을 눌러야만 기록됩니다.
이미 그 날짜에 다른 시각이 기록되어 있으면 자동으로 덮어쓰지 않고 "알림 시각으로 변경" 버튼을 눌러야 바뀝니다.

## 빌드

```bash
npm install
npx cap sync ios      # www → ios/App/App/public, 플러그인/Podfile 갱신
```

서명 빌드는 Mac이 필요 없습니다. Codemagic `ios-release` 워크플로가
`npm install → cap sync → pod install → use-profiles → build-ipa → TestFlight 업로드`를 수행합니다.

## 데이터

출퇴근 기록은 `localStorage`(`ot.local.records`), 기본 시간은 `ot.local.settings`, 회사 위치는 `ot.geofence.v1` + iOS `UserDefaults`에 저장됩니다.
서버·유료 지도 API를 쓰지 않습니다.

## 퇴근 독촉 알림

출근을 했는데 정규 퇴근시간(기본 17:30, 설정 탭에서 변경)이 지나도록 퇴근 기록이 없으면
**+1시간, +2시간, +3시간, +4시간에 1시간 간격으로 총 4회** 잠금 화면 알림을 보냅니다.

- 알림은 앱이 백그라운드·종료 상태여도 울리도록 `UNCalendarNotificationTrigger` 로 미리 예약합니다(앱을 꺼도 동작).
- 출근은 **앱의 출근 기록** 또는 **회사 반경 진입**(지오펜스, 앱이 꺼져 있어도)으로 판단합니다.
- 퇴근 기록을 저장하거나 회사 반경을 벗어나면 오늘 남은 독촉 알림을 **즉시 모두 취소**합니다.
- 이미 지난 회차는 건너뛰고 남은 회차만 예약하며, 자정을 넘는 회차는 만들지 않습니다.
- 알림을 누르면 앱이 열리고 **지금 시각**이 퇴근 칸에 채워집니다(저장은 사용자가 직접).

### 문구 200개

`CommuteNagCopy.swift` 에 4단계 × 50개 = **200개**가 들어 있고, 날짜 기반으로 골라
하루 4개(단계별 1개)가 서로 다르고 **매일 문구가 달라집니다**(50일이면 200개를 모두 사용).
날짜 인덱스는 2020-01-01 부터의 일수라서 하루만 넘어가도 항상 다른 문구가 나옵니다.

## 검증

```bash
python3 tests/verify-native-config.py    # 정적 구성 검사
python3 tests/verify-nag.py              # 문구 200개·예약/취소 규칙·웹 연동 (헤드리스)
python3 tests/verify-ios-web.py          # 기존 기능 회귀 (헤드리스)
python3 tests/verify-bridge-mock.py      # 가짜 네이티브 브리지 (헤드리스)
```

## 기존 기록 가져오기

설정 탭 → **기존 기록 가져오기**에서
- 브라우저 웹앱의 `CSV 저장` 파일
- 이 앱의 `JSON 백업` 파일
- 엑셀에서 복사한 표(붙여넣기)

를 불러올 수 있습니다. 기존 날짜는 기본적으로 유지하고, 체크박스를 켜야 덮어씁니다.
