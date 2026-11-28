# 퇴근했니? — iOS 앱 (Capacitor + 네이티브 지오펜스)

기존 웹앱(v4, `main` 브랜치의 `index.html`)을 아이폰 앱으로 감싸고, **회사 반경 진입/이탈을 잠금 화면 알림으로 알려 주는** 네이티브 기능을 더한 버전입니다.
`main` 브랜치(웹앱/PWA)는 그대로 두고, 이 작업은 `ios-testflight` 브랜치에서만 이루어졌습니다.

## 구성

| 경로 | 설명 |
|---|---|
| `www/index.html` | 앱 웹 레이어. 기존 웹앱 기능(카피바라·골드 디자인, 즉시 출퇴근, 직접 입력·수정, 조기 출근/퇴근 후 초과근무, 달력, 월 합계, 엑셀 복사·CSV/JSON 저장) + 설정 탭 + 가져오기 + 알림 배너 |
| `native/geofence-commute/` | 로컬 Capacitor 플러그인 `GeofenceCommute` (Swift). `CLLocationManager` 영역 감시 + `UNUserNotificationCenter` 로컬 알림 |
| `ios/` | Capacitor iOS 프로젝트 (`com.kyeore.overtimetracker`) |
| `ios/App/OvertimeWidget/` | 홈·잠금화면 위젯 익스텐션 `OvertimeWidgetExtension` (`com.kyeore.overtimetracker.widget`) |
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
| `getForgetStatus()` | 오늘의 퇴근 기록 깜빡 알림 상태(예약됨/이탈 전/퇴근 완료/끝남/꺼짐)와 3회 문구·시각, 회사를 나선 시각 |
| `testForgetNotification({round, delay})` | 1분 뒤 오늘 문구로 깜빡 알림 테스트 |
| `syncAutoRecordSettings({autoClockIn, autoClockOut})` | 자동 출퇴근 기록 설정을 `UserDefaults` 에 즉시 저장(앱이 꺼져 있어도 지오펜스가 적용) |
| `getAutoRecordStatus()` | 자동 기록 설정, 오늘 기록한 시각, 아직 반영 안 된 대기 기록 |
| `getAutoRecords()` | 앱이 꺼져 있는 동안 만들어진 자동 기록 목록(가져가기만 하고 지우지 않음) |
| `ackAutoRecords({ids})` | 웹이 기록에 반영한 뒤 대기 큐에서 지움 |
| `syncWidgetState({payload})` | 오늘 기록 JSON을 App Group(`group.com.kyeore.overtimetracker`)에 쓰고 위젯 타임라인을 새로 고침 |
| `getWidgetRecords()` | 위젯 버튼에서 찍어 둔 기록 목록(가져가기만 하고 지우지 않음) |
| `ackWidgetRecords({ids})` | 웹이 기록에 반영한 뒤 위젯 대기 큐에서 지움 |
| `reloadWidget()` | 위젯 타임라인만 새로 고침 |
| `getWidgetStatus()` | App Group 사용 가능 여부, 오늘 위젯 상태(출근/퇴근 시각), 대기 건수 |
| 이벤트 `commuteAction` | 앱이 떠 있는 상태에서 알림을 눌렀을 때 전달 |
| 이벤트 `autoRecord` | 앱이 떠 있는 상태에서 자동 기록이 만들어졌을 때 전달 |

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

## 퇴근 기록 깜빡 알림

회사 반경을 **벗어났는데 오늘 퇴근 기록이 아직 없으면**, 이탈 시각을 기준으로
**+1시간, +2시간, +3시간에 1시간 간격으로 총 3회** 잠금 화면 알림을 보냅니다.

- 판단: 반경 이탈을 실제로 목격했고(앱이 꺼져 있어도 지오펜스가 깨워 줌), 오늘 **출근 기록이 있고 퇴근 기록이 비어 있을 때**만 예약합니다.
- 이탈하는 순간 기존 **야근 독촉 알림(+1~+4시간)은 취소**하고, 기존 "퇴근 기록할까요?" 알림과 함께 3회 알림을 예약합니다.
- **퇴근 기록을 저장하면 남은 3회는 즉시 취소**되고, 알림 센터에 남은 알림도 지워집니다. 다시 회사 반경에 들어와도 취소됩니다.
- 자정을 넘는 회차는 만들지 않습니다(21:30 이탈 → 22:30·23:30 2회, 23:00 이탈 → 예약 없음).
- 알림을 누르면 앱이 열리고 **회사를 나선 시각**이 퇴근 칸에 채워집니다(저장은 사용자가 직접).

### 문구 150개

`CommuteForgetCopy.swift` 에 3단계 × 50개 = **150개**가 들어 있고, 날짜 기반으로 골라
하루 3개(단계별 1개)가 서로 다르고 **매일 문구가 달라집니다**(50일이면 150개를 모두 사용).

## 자동 출퇴근 기록 (선택 기능, 기본 OFF)

설정 탭의 **자동 출퇴근 기록**에서 두 스위치를 따로 켜고 끌 수 있습니다. 둘 다 기본은 **꺼짐**입니다.

| 스위치 | 켰을 때 |
|---|---|
| 반경 진입 시 자동 출근 기록 | 오늘 출근 기록이 비어 있으면 진입 시각을 **출근으로 자동 저장**하고 `"HH:MM 출근이 자동으로 기록되었어요!"` 알림 |
| 반경 이탈 시 자동 퇴근 기록 | 오늘 출근 기록이 있고 퇴근이 비어 있으면 이탈 시각을 **퇴근으로 자동 저장**하고 `"HH:MM 퇴근이 자동으로 기록되었어요!"` 알림 + **야근 독촉·기록 깜빡 알림 3회 즉시 취소** |

- 이미 기록된 시각은 **절대 덮어쓰지 않습니다**. 자동 기록이 할 일이 없으면 조용히 넘어갑니다(확인 알림도 띄우지 않음).
- 스위치를 누르면 바로 `UserDefaults` 에 저장되어, 앱이 **백그라운드·완전 종료** 상태여도 지오펜스가 같은 규칙으로 기록합니다.
- 앱이 떠 있으면 그 자리에서 기록에 반영되고, 꺼져 있으면 `UserDefaults` 대기 큐에 넣었다가 앱을 열 때 자동으로 반영합니다(반영 후 큐에서 지움).
- 앱을 처음 켰을 때 확인되는 기준선(이미 반경 안/밖)은 이동이 아니므로 자동 기록하지 않습니다.
- 자동 기록 알림은 누를 동작이 없어, 눌러도 "기록할까요?" 배너가 뜨지 않고 앱만 열립니다.

## 홈·잠금화면 위젯 + 애플워치 알림 버튼 (1.0 빌드 8)

**위젯** (`ios/App/OvertimeWidget/`)
- 홈화면 **소형·중형**, 잠금화면 **원형·직사각형**. 오늘 상태(미출근 / 근무 중·출근시각 / 퇴근 완료·출근~퇴근)를 그대로 보여 줍니다.
- iOS 17+ 에서는 위젯 안의 **[출근 찍기] / [퇴근 찍기]** 버튼(`RecordCommuteIntent`)으로 앱을 열지 않고 바로 기록합니다. 이미 기록된 시각은 덮어쓰지 않습니다.
- 위젯 버튼으로 찍은 기록은 App Group 대기 큐(`widget_pending_records`)에 쌓이고, 앱을 열거나 포그라운드로 돌아오면 오늘 기록에 반영한 뒤 야근 독촉·기록 깜빡 알림까지 다시 맞춥니다.
- 앱에서 기록을 저장·자동기록·삭제하거나 정규 시간을 바꾸면 즉시 App Group 상태(`widget_state`)를 갱신하고 위젯을 새로 그립니다.

**애플워치·잠금화면 알림 액션 버튼**
- 출근 확인 알림은 카테고리 `COMMUTE_IN` → **[출근 찍기]** [기록하기] [나중에]
- 퇴근 확인·야근 독촉·기록 깜빡 알림은 카테고리 `COMMUTE_OUT` → **[퇴근 찍기]** [기록하기] [나중에]
- [출근 찍기]/[퇴근 찍기] 는 백그라운드 액션(`options: []`)이라 **앱을 열지 않고** 알림 시각으로 기록됩니다(이미 있으면 그대로 두고 안내만). 퇴근이면 남은 야근 독촉·기록 깜빡 알림을 즉시 취소하고 위젯도 새로 고칩니다.

## 검증

```bash
python3 tests/verify-widget.py           # 위젯 저장소 규칙·App Group·알림 액션·웹 동기화 (헤드리스)
python3 tests/verify-autorecord.py   # 자동 출퇴근 기록 규칙·대기 큐·웹 연동 (헤드리스)
python3 tests/verify-native-config.py    # 정적 구성 검사
python3 tests/verify-nag.py              # 문구 200개·예약/취소 규칙·웹 연동 (헤드리스)
python3 tests/verify-forget.py           # 문구 150개·이탈 후 3회 예약/취소·웹 연동 (헤드리스)
python3 tests/verify-ios-web.py          # 기존 기능 회귀 (헤드리스)
python3 tests/verify-bridge-mock.py      # 가짜 네이티브 브리지 (헤드리스)
```

## 기존 기록 가져오기

설정 탭 → **기존 기록 가져오기**에서
- 브라우저 웹앱의 `CSV 저장` 파일
- 이 앱의 `JSON 백업` 파일
- 엑셀에서 복사한 표(붙여넣기)

를 불러올 수 있습니다. 기존 날짜는 기본적으로 유지하고, 체크박스를 켜야 덮어씁니다.
