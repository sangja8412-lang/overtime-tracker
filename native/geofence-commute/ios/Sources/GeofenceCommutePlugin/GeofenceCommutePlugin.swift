import Foundation
import Capacitor
import CoreLocation
import UserNotifications
import UIKit

// MARK: - Values shared between the web layer and the native layer

struct CommuteGeofence: Codable {
    var latitude: Double
    var longitude: Double
    var radius: Double
    var label: String
}

/// Everything that has to survive an app kill: the saved company area,
/// the last known inside/outside state and the last notification we fired.
enum CommuteStore {
    private static let defaults = UserDefaults.standard
    private static let geofenceKey = "commute.geofence.v1"
    private static let pendingKey = "commute.pendingAction.v1"
    private static let lastStateKey = "commute.lastRegionState.v1"
    private static let lastNotifyAtKey = "commute.lastNotifyAt.v1"
    private static let lastNotifyTypeKey = "commute.lastNotifyType.v1"
    private static let lastErrorKey = "commute.lastError.v1"

    static var geofence: CommuteGeofence? {
        get {
            guard let data = defaults.data(forKey: geofenceKey) else { return nil }
            return try? JSONDecoder().decode(CommuteGeofence.self, from: data)
        }
        set {
            if let value = newValue, let data = try? JSONEncoder().encode(value) {
                defaults.set(data, forKey: geofenceKey)
            } else {
                defaults.removeObject(forKey: geofenceKey)
            }
        }
    }

    /// A notification the user tapped but the web layer has not consumed yet.
    static var pendingAction: [String: Any]? {
        defaults.dictionary(forKey: pendingKey)
    }

    static func setPending(_ payload: [String: Any]) {
        defaults.set(payload, forKey: pendingKey)
    }

    static func takePending() -> [String: Any]? {
        guard let payload = defaults.dictionary(forKey: pendingKey) else { return nil }
        defaults.removeObject(forKey: pendingKey)
        return payload
    }

    static var lastRegionState: String? {
        get { defaults.string(forKey: lastStateKey) }
        set { defaults.set(newValue, forKey: lastStateKey) }
    }

    static func resetRegionState() {
        defaults.removeObject(forKey: lastStateKey)
    }

    /// Guard against notification storms: at most one notification per type
    /// every `cooldown` seconds (GPS drift around the fence border).
    static func canNotify(type: String, now: Date = Date(), cooldown: TimeInterval = 300) -> Bool {
        guard let last = defaults.object(forKey: lastNotifyAtKey) as? Date else { return true }
        let lastType = defaults.string(forKey: lastNotifyTypeKey)
        if lastType == type && now.timeIntervalSince(last) < cooldown { return false }
        return true
    }

    static func recordNotification(type: String, at date: Date) {
        defaults.set(date, forKey: lastNotifyAtKey)
        defaults.set(type, forKey: lastNotifyTypeKey)
    }

    static var lastError: String? {
        get { defaults.string(forKey: lastErrorKey) }
        set { defaults.set(newValue, forKey: lastErrorKey) }
    }
}

// MARK: - Notification delegate

extension Notification.Name {
    static let commuteActionTapped = Notification.Name("commuteActionTapped")
    /// 앱이 떠 있을 때 자동 기록이 만들어지면 웹 레이어에 바로 알린다.
    static let commuteAutoRecordCreated = Notification.Name("commuteAutoRecordCreated")
}

/// Owns UNUserNotificationCenter so that a tap works even when the app was
/// launched from a terminated state (the delegate is installed in AppDelegate
/// before the web view exists).
public final class CommuteNotificationHandler: NSObject, UNUserNotificationCenterDelegate {
    public static let shared = CommuteNotificationHandler()
    /// 자동 기록 완료 알림용 카테고리(누를 동작 없이 앱만 열린다).
    public static let autoCategory = "COMMUTE_AUTO"
    /// 출근 확인 알림(출근 찍기 버튼)
    public static let clockInCategory = "COMMUTE_IN"
    /// 퇴근 확인·야근 독촉·기록 깜빡 알림(퇴근 찍기 버튼)
    public static let clockOutCategory = "COMMUTE_OUT"
    /// build 7 이전에 예약된 알림이 쓰던 카테고리(퇴근 찍기 버튼을 그대로 단다)
    public static let legacyCategory = "COMMUTE"

    private var started = false

    public func start() {
        if started { return }
        started = true
        let center = UNUserNotificationCenter.current()
        center.delegate = self
        // [출근 찍기] / [퇴근 찍기] 는 백그라운드 액션(options: [])이라
        // 애플워치에서 버튼만 눌러도 앱을 열지 않고 기록이 남는다.
        let clockIn = UNNotificationAction(identifier: CommuteNotificationAction.clockIn,
                                           title: "출근 찍기",
                                           options: [])
        let clockOut = UNNotificationAction(identifier: CommuteNotificationAction.clockOut,
                                            title: "퇴근 찍기",
                                            options: [])
        let record = UNNotificationAction(identifier: CommuteNotificationAction.record,
                                          title: "기록하기",
                                          options: [.foreground])
        let later = UNNotificationAction(identifier: CommuteNotificationAction.later,
                                         title: "나중에",
                                         options: [])
        let inCategory = UNNotificationCategory(identifier: Self.clockInCategory,
                                                actions: [clockIn, record, later],
                                                intentIdentifiers: [],
                                                options: [])
        let outCategory = UNNotificationCategory(identifier: Self.clockOutCategory,
                                                 actions: [clockOut, record, later],
                                                 intentIdentifiers: [],
                                                 options: [])
        let legacy = UNNotificationCategory(identifier: Self.legacyCategory,
                                            actions: [clockOut, record, later],
                                            intentIdentifiers: [],
                                            options: [])
        let auto = UNNotificationCategory(identifier: Self.autoCategory,
                                          actions: [],
                                          intentIdentifiers: [],
                                          options: [])
        center.setNotificationCategories([inCategory, outCategory, legacy, auto])
    }

    /// 액션으로 찍으려 했지만 그 시각이 이미 기록되어 있을 때 조용히 알려 준다.
    /// (카테고리 COMMUTE_AUTO 라서 누를 동작은 없다)
    public func recordKeptNotice(type: String, time: String) {
        let center = UNUserNotificationCenter.current()
        let content = UNMutableNotificationContent()
        content.title = "🦫 퇴근했니?"
        content.body = type == "in"
            ? "이미 \(time) 출근 기록이 있어서 그대로 두었어요."
            : "이미 \(time) 퇴근 기록이 있어서 그대로 두었어요."
        content.sound = .default
        content.categoryIdentifier = Self.autoCategory
        content.threadIdentifier = "commute"
        let trigger = UNTimeIntervalNotificationTrigger(timeInterval: 1, repeats: false)
        center.add(UNNotificationRequest(identifier: "commute-kept-\(type)-\(Int(Date().timeIntervalSince1970))",
                                         content: content,
                                         trigger: trigger),
                   withCompletionHandler: nil)
    }

    // Show the banner even while the app is in the foreground (잠금 화면과 동일한 문구).
    public func userNotificationCenter(_ center: UNUserNotificationCenter,
                                       willPresent notification: UNNotification,
                                withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void) {
        completionHandler([.banner, .list, .sound])
    }

    public func userNotificationCenter(_ center: UNUserNotificationCenter,
                                       didReceive response: UNNotificationResponse,
                                withCompletionHandler completionHandler: @escaping () -> Void) {
        if response.actionIdentifier == UNNotificationDismissActionIdentifier {
            completionHandler()
            return
        }
        let info = response.notification.request.content.userInfo
        // 애플워치·잠금화면의 [출근 찍기] / [퇴근 찍기]: 앱을 열지 않고 바로 기록한다.
        if let quickType = CommuteNotificationAction.type(for: response.actionIdentifier) {
            CommuteActionRecorder.record(type: quickType, userInfo: info)
            completionHandler()
            return
        }
        var payload: [String: Any] = [:]
        if let type = info["type"] as? String { payload["type"] = type }
        if let timestamp = info["timestamp"] as? String { payload["timestamp"] = timestamp }
        if let epoch = info["epoch"] as? Double { payload["epoch"] = epoch }
        if let date = info["date"] as? String { payload["date"] = date }
        if let radius = info["radius"] as? Double { payload["radius"] = radius }
        if let nag = info["nag"] as? Bool { payload["nag"] = nag }
        if let round = info["round"] as? Int { payload["round"] = round }
        // '퇴근 기록 깜빡' 알림: 탭하면 회사를 나선 시각이 퇴근 칸에 채워진다.
        if let forget = info["forget"] as? Bool { payload["forget"] = forget }
        if let exitEpoch = info["exitEpoch"] as? Double { payload["exitEpoch"] = exitEpoch }
        payload["action"] = response.actionIdentifier

        if payload["type"] != nil {
            CommuteStore.setPending(payload)
            NotificationCenter.default.post(name: .commuteActionTapped, object: nil, userInfo: payload)
        }
        completionHandler()
    }
}

// MARK: - Geofence monitoring

final class CommuteGeofenceManager: NSObject, CLLocationManagerDelegate {
    static let shared = CommuteGeofenceManager()
    static let regionIdentifier = "commute-office"

    let locationManager = CLLocationManager()
    private var oneShotCompletion: ((Result<CLLocation, Error>) -> Void)?
    private(set) var monitoring = false

    private override init() {
        super.init()
        locationManager.delegate = self
        locationManager.desiredAccuracy = kCLLocationAccuracyHundredMeters
        locationManager.pausesLocationUpdatesAutomatically = false
        locationManager.distanceFilter = 50
    }

    var authorizationStatus: CLAuthorizationStatus {
        locationManager.authorizationStatus
    }

    /// 마지막으로 확인된 회사 반경 안/밖 상태(앱이 종료돼 있던 동안의 값 포함).
    var isCurrentlyInside: Bool {
        CommuteStore.lastRegionState == "inside"
    }

    func startMonitoring(_ geofence: CommuteGeofence) {
        guard CLLocationManager.isMonitoringAvailable(for: CLCircularRegion.self) else {
            CommuteStore.lastError = "이 기기에서는 영역 감시를 지원하지 않아요."
            return
        }
        stopMonitoring(resetState: false)
        let center = CLLocationCoordinate2D(latitude: geofence.latitude, longitude: geofence.longitude)
        let maximum = locationManager.maximumRegionMonitoringDistance
        let radius = max(50, min(geofence.radius, maximum))
        let region = CLCircularRegion(center: center, radius: radius, identifier: Self.regionIdentifier)
        region.notifyOnEntry = true
        region.notifyOnExit = true
        locationManager.startMonitoring(for: region)
        monitoring = true
        enableBackgroundUpdatesIfAllowed()
        // Ask for the current state: with no stored state this becomes the
        // baseline (no notification), with a stored state it reports a change
        // that happened while the app was not running.
        locationManager.requestState(for: region)
    }

    func stopMonitoring(resetState: Bool = true) {
        for region in locationManager.monitoredRegions where region.identifier == Self.regionIdentifier {
            locationManager.stopMonitoring(for: region)
        }
        monitoring = false
        if resetState { CommuteStore.resetRegionState() }
    }

    func restoreMonitoring() {
        guard let geofence = CommuteStore.geofence else { return }
        startMonitoring(geofence)
        // Keep monitoring "started" even before the first determination arrives.
        monitoring = !locationManager.monitoredRegions.isEmpty
    }

    func enableBackgroundUpdatesIfAllowed() {
        if authorizationStatus == .authorizedAlways {
            locationManager.allowsBackgroundLocationUpdates = true
            locationManager.showsBackgroundLocationIndicator = false
        }
    }

    func isMonitoringRegion() -> Bool {
        locationManager.monitoredRegions.contains { $0.identifier == Self.regionIdentifier }
    }

    func requestCurrentLocation(completion: @escaping (Result<CLLocation, Error>) -> Void) {
        oneShotCompletion = completion
        locationManager.requestLocation()
    }

    func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        guard let completion = oneShotCompletion, let location = locations.last else { return }
        oneShotCompletion = nil
        completion(.success(location))
    }

    func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
        if let completion = oneShotCompletion {
            oneShotCompletion = nil
            completion(.failure(error))
            return
        }
        CommuteStore.lastError = error.localizedDescription
    }

    // MARK: CLLocationManagerDelegate

    func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        enableBackgroundUpdatesIfAllowed()
        if manager.authorizationStatus == .authorizedAlways, CommuteStore.geofence != nil {
            if !isMonitoringRegion() { restoreMonitoring() }
        }
    }

    func locationManager(_ manager: CLLocationManager, didStartMonitoringFor region: CLRegion) {
        monitoring = true
        manager.requestState(for: region)
    }

    func locationManager(_ manager: CLLocationManager, monitoringDidFailFor region: CLRegion?, withError error: Error) {
        CommuteStore.lastError = error.localizedDescription
        monitoring = false
    }

    func locationManager(_ manager: CLLocationManager, didEnterRegion region: CLRegion) {
        guard region.identifier == Self.regionIdentifier else { return }
        handleTransition(inside: true)
    }

    func locationManager(_ manager: CLLocationManager, didExitRegion region: CLRegion) {
        guard region.identifier == Self.regionIdentifier else { return }
        handleTransition(inside: false)
    }

    func locationManager(_ manager: CLLocationManager, didDetermineState state: CLRegionState, for region: CLRegion) {
        guard region.identifier == Self.regionIdentifier else { return }
        switch state {
        case .inside:
            handleTransition(inside: true, baselineIfUnknown: true)
        case .outside:
            handleTransition(inside: false, baselineIfUnknown: true)
        default:
            break
        }
    }

    /// 지오펜스 진입/이탈을 퇴근 독촉·기록 깜빡 상태에 반영한다
    /// (앱이 백그라운드·종료 상태여도 호출됨).
    /// - witnessedExit: 이번 호출이 '회사를 벗어난 것'을 실제로 목격했는지.
    ///   앱을 처음 켰을 때의 기준선(이미 밖)과 구분한다.
    private func updateNagState(inside: Bool, witnessedExit: Bool = false, at now: Date = Date()) {
        let today = CommuteNagPlanner.dayKey(now)
        if inside {
            CommuteNagStore.clearSuppression()
            var day = CommuteNagStore.day
            if day == nil || day!.date != today {
                day = CommuteNagStore.Day(date: today,
                                          clockIn: "",
                                          clockOut: "",
                                          regularEnd: CommuteNagStore.settings.regularEnd)
            }
            if day!.clockIn.isEmpty { day!.clockIn = CommuteNagPlanner.hhmm(now) }
            CommuteNagStore.day = day
            // 다시 회사 안이면 '기록 깜빡' 알림은 필요 없다.
            CommuteForgetScheduler.cancelAll(clear: true)
        } else {
            CommuteNagStore.suppress(date: today, reason: "geofenceExit")
            // 반경을 벗어나면 기존 야근 독촉 알림은 미리 끈다.
            CommuteNagScheduler.cancelAll()
        }
        CommuteNagScheduler.refresh()
        if witnessedExit && !inside {
            // 오늘 출근은 했는데 퇴근 기록이 없다 → 1시간 간격 3회 '기록 깜빡' 알림 예약.
            CommuteForgetScheduler.scheduleAfterExit(exitDate: now, day: CommuteNagStore.day, now: now)
        } else {
            CommuteForgetScheduler.refresh(now: now)
        }
    }

    private func handleTransition(inside: Bool, baselineIfUnknown: Bool = false) {
        guard CommuteStore.geofence != nil else { return }
        let newState = inside ? "inside" : "outside"
        let previous = CommuteStore.lastRegionState
        CommuteStore.lastRegionState = newState
        // 반경 이탈을 실제로 목격했는지: 기준선(previous == nil)은 이탈이 아니다.
        let witnessedExit = !inside && (previous != nil || !baselineIfUnknown)
        // 기준선(첫 확인)은 이동이 아니다. 자동 기록도 남기지 않는다.
        let isTransition = previous == nil ? !baselineIfUnknown : (previous != newState)
        // 퇴근 독촉: 사무실에 있으면 출근으로 보고 예약하고, 반경을 벗어나면 오늘 독촉은 끈다.
        updateNagState(inside: inside, witnessedExit: witnessedExit)
        guard isTransition else { return }

        let now = Date()
        let settings = CommuteAutoRecordStore.settings
        let today = CommuteNagPlanner.dayKey(now)

        if inside, settings.autoClockIn {
            // 자동 출근이 켜져 있으면 '출근 기록할까요?' 대신 자동으로 기록하고 완료만 알린다.
            if let entry = CommuteAutoRecordStore.record(date: today,
                                                         type: "in",
                                                         time: CommuteNagPlanner.hhmm(now),
                                                         at: now) {
                announceAutoRecord(entry)
                CommuteNagScheduler.refresh()
            }
            // 오늘 출근 기록이 이미 있으면 덮어쓰지 않고 조용히 넘어간다.
            return
        }

        if !inside, settings.autoClockOut {
            let state = CommuteAutoRecordStore.dayState(for: today)
            if let entry = CommuteAutoRecordStore.record(date: today,
                                                         type: "out",
                                                         time: CommuteNagPlanner.hhmm(now),
                                                         at: now) {
                markClockOut(time: entry.time, at: now)
                announceAutoRecord(entry)
                return
            }
            // 이미 퇴근 기록이 있으면 조용히 넘어간다.
            if !state.clockOut.isEmpty { return }
            // 출근 기록이 아예 없으면 남길 퇴근이 없으니 기존 안내 알림을 그대로 보낸다.
        }

        let type = inside ? "in" : "out"
        guard CommuteStore.canNotify(type: type) else { return }
        postNotification(type: type, at: now)
    }

    /// 자동 퇴근을 야근 독촉·기록 깜빡 상태에도 반영하고, 남은 알림을 모두 취소한다.
    private func markClockOut(time: String, at now: Date) {
        let today = CommuteNagPlanner.dayKey(now)
        var day = CommuteNagStore.day
        if day == nil || day!.date != today {
            day = CommuteNagStore.Day(date: today,
                                      clockIn: CommuteAutoRecordStore.dayState(for: today).clockIn,
                                      clockOut: time,
                                      regularEnd: CommuteNagStore.settings.regularEnd)
        } else {
            day!.clockOut = time
        }
        CommuteNagStore.day = day
        CommuteNagStore.suppress(date: today, reason: "clockOut")
        // 자동 퇴근이 찍히면 야근 독촉(+1~+4시간)과 퇴근 기록 깜빡 알림 3회를 즉시 모두 취소한다.
        CommuteNagScheduler.cancelAll()
        CommuteForgetScheduler.cancelAll(clear: true)
        CommuteNagScheduler.refresh()
    }

    /// "HH:MM 출근/퇴근이 자동으로 기록되었어요!" 알림을 띄우고 웹 레이어에도 알린다.
    func announceAutoRecord(_ entry: CommuteAutoRecordStore.Pending) {
        CommuteNotificationHandler.shared.start()
        let center = UNUserNotificationCenter.current()
        let content = UNMutableNotificationContent()
        content.title = "🦫 퇴근했니?"
        content.body = "\(entry.time) \(entry.type == "in" ? "출근" : "퇴근")이 자동으로 기록되었어요!"
        content.sound = .default
        // 누를 동작이 없는 카테고리: 알림을 눌러도 '기록할까요?' 배너가 뜨지 않고 앱만 열린다.
        content.categoryIdentifier = CommuteNotificationHandler.autoCategory
        content.threadIdentifier = "commute"
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        let recordDate = Date(timeIntervalSince1970: entry.epoch / 1000.0)
        content.userInfo = [
            "auto": true,
            "recordType": entry.type,
            "date": entry.date,
            "time": entry.time,
            "timestamp": formatter.string(from: recordDate),
            "epoch": entry.epoch
        ]
        let trigger = UNTimeIntervalNotificationTrigger(timeInterval: 1, repeats: false)
        let request = UNNotificationRequest(identifier: "commute-auto-\(entry.id)",
                                            content: content,
                                            trigger: trigger)
        center.add(request) { error in
            if let error = error { CommuteStore.lastError = error.localizedDescription }
        }
        NotificationCenter.default.post(name: .commuteAutoRecordCreated,
                                        object: nil,
                                        userInfo: CommuteAutoRecordStore.payload(entry))
        // 자동 기록도 위젯에 바로 보이게 맞춘다.
        let dayState = CommuteAutoRecordStore.dayState(for: entry.date)
        if entry.date == CommuteWidgetStore.todayKey() {
            CommuteWidgetStore.applyWeb(clockIn: dayState.clockIn, clockOut: dayState.clockOut)
            CommuteWidgetCenter.reload()
        }
    }

    // MARK: Local notification

    func postNotification(type: String, at date: Date, delayed: TimeInterval = 1) {
        CommuteNotificationHandler.shared.start()
        let center = UNUserNotificationCenter.current()
        let content = UNMutableNotificationContent()
        content.title = "퇴근했니?"
        if type == "in" {
            content.body = "회사 반경에 들어왔어요. 출근 기록할까요?"
        } else {
            content.body = "회사 반경에서 나왔어요. 퇴근 기록할까요?"
        }
        content.sound = .default
        // 출근 확인 알림에는 [출근 찍기], 퇴근 확인 알림에는 [퇴근 찍기] 버튼을 단다.
        content.categoryIdentifier = type == "in"
            ? CommuteNotificationHandler.clockInCategory
            : CommuteNotificationHandler.clockOutCategory
        content.threadIdentifier = "commute"
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        let timeZone = TimeZone.current
        let dateFormatter = DateFormatter()
        dateFormatter.dateFormat = "yyyy-MM-dd"
        dateFormatter.timeZone = timeZone
        var userInfo: [String: Any] = [
            "type": type,
            "timestamp": formatter.string(from: date),
            "epoch": date.timeIntervalSince1970 * 1000.0,
            "date": dateFormatter.string(from: date)
        ]
        if let geofence = CommuteStore.geofence {
            userInfo["radius"] = geofence.radius
            userInfo["label"] = geofence.label
        }
        content.userInfo = userInfo
        let trigger = UNTimeIntervalNotificationTrigger(timeInterval: max(1, delayed), repeats: false)
        let request = UNNotificationRequest(identifier: "commute-\(type)-\(Int(date.timeIntervalSince1970))",
                                           content: content,
                                           trigger: trigger)
        center.add(request) { error in
            if let error = error {
                CommuteStore.lastError = error.localizedDescription
            }
        }
        CommuteStore.recordNotification(type: type, at: date)
    }
}

// MARK: - Capacitor plugin

@objc(GeofenceCommutePlugin)
public class GeofenceCommutePlugin: CAPPlugin, CAPBridgedPlugin {
    public let identifier = "GeofenceCommutePlugin"
    public let jsName = "GeofenceCommute"
    public let pluginMethods: [CAPPluginMethod] = [
        CAPPluginMethod(name: "getStatus", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "requestLocationPermission", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "requestNotificationPermission", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "getCurrentLocation", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "setGeofence", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "clearGeofence", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "getPendingAction", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "clearDelivered", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "testNotification", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "syncCommuteNag", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "getNagStatus", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "testNagNotification", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "getForgetStatus", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "testForgetNotification", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "syncAutoRecordSettings", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "getAutoRecordStatus", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "getAutoRecords", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "ackAutoRecords", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "syncWidgetState", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "getWidgetRecords", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "ackWidgetRecords", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "reloadWidget", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "getWidgetStatus", returnType: CAPPluginReturnPromise)
    ]

    private var observer: NSObjectProtocol?
    private var autoObserver: NSObjectProtocol?

    override public func load() {
        CommuteNotificationHandler.shared.start()
        CommuteGeofenceManager.shared.restoreMonitoring()
        // 앱이 다시 열리면 오늘 남은 독촉·기록 깜빡 알림을 상태에 맞춰 다시 맞춘다.
        CommuteNagScheduler.refresh()
        CommuteForgetScheduler.refresh()
        observer = NotificationCenter.default.addObserver(forName: .commuteActionTapped,
                                                          object: nil,
                                                          queue: .main) { [weak self] note in
            guard let self = self else { return }
            let payload = note.userInfo as? [String: Any] ?? [:]
            self.notifyListeners("commuteAction", data: payload)
        }
        autoObserver = NotificationCenter.default.addObserver(forName: .commuteAutoRecordCreated,
                                                              object: nil,
                                                              queue: .main) { [weak self] note in
            guard let self = self else { return }
            let payload = note.userInfo as? [String: Any] ?? [:]
            self.notifyListeners("autoRecord", data: payload)
        }
    }

    deinit {
        if let observer = observer {
            NotificationCenter.default.removeObserver(observer)
        }
        if let observer = autoObserver {
            NotificationCenter.default.removeObserver(observer)
        }
    }

    // MARK: - Status

    @objc func getStatus(_ call: CAPPluginCall) {
        getNotificationSettings { settings in
            let result = self.statusPayload(notificationStatus: settings)
            call.resolve(result)
        }
    }

    private func statusPayload(notificationStatus: String) -> [String: Any] {
        let manager = CommuteGeofenceManager.shared
        var payload: [String: Any] = [
            "location": locationAuthorizationString(manager.authorizationStatus),
            "notifications": notificationStatus,
            "monitoring": manager.isMonitoringRegion(),
            "backgroundUpdates": manager.locationManager.allowsBackgroundLocationUpdates,
            "autoRecord": CommuteAutoRecordStore.settingsPayload()
        ]
        if let geofence = CommuteStore.geofence {
            payload["geofence"] = [
                "latitude": geofence.latitude,
                "longitude": geofence.longitude,
                "radius": geofence.radius,
                "label": geofence.label
            ]
        }
        if let error = CommuteStore.lastError {
            payload["lastError"] = error
        }
        return payload
    }

    private func locationAuthorizationString(_ status: CLAuthorizationStatus) -> String {
        switch status {
        case .authorizedAlways: return "always"
        case .authorizedWhenInUse: return "whenInUse"
        case .denied: return "denied"
        case .restricted: return "restricted"
        case .notDetermined: return "notDetermined"
        @unknown default: return "unknown"
        }
    }

    private func getNotificationSettings(_ completion: @escaping (String) -> Void) {
        UNUserNotificationCenter.current().getNotificationSettings { settings in
            let value: String
            switch settings.authorizationStatus {
            case .authorized, .provisional, .ephemeral: value = "granted"
            case .denied: value = "denied"
            case .notDetermined: value = "notDetermined"
            @unknown default: value = "unknown"
            }
            DispatchQueue.main.async { completion(value) }
        }
    }

    // MARK: - Permissions

    @objc func requestLocationPermission(_ call: CAPPluginCall) {
        let manager = CommuteGeofenceManager.shared.locationManager
        let status = manager.authorizationStatus
        if status == .notDetermined {
            manager.requestWhenInUseAuthorization()
        } else if status == .authorizedWhenInUse {
            manager.requestAlwaysAuthorization()
        }
        // Give the system prompt a moment, then answer with the current status.
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.0) {
            let current = CommuteGeofenceManager.shared.authorizationStatus
            CommuteGeofenceManager.shared.enableBackgroundUpdatesIfAllowed()
            call.resolve([
                "location": self.locationAuthorizationString(current),
                "hint": current == .authorizedWhenInUse
                    ? "위치 권한이 '앱을 사용하는 동안'으로 되어 있어요. 잠금 화면 알림을 받으려면 '항상 허용'으로 바꿔 주세요."
                    : ""
            ])
        }
    }

    @objc func requestNotificationPermission(_ call: CAPPluginCall) {
        let center = UNUserNotificationCenter.current()
        center.requestAuthorization(options: [.alert, .sound, .badge]) { granted, _ in
            self.getNotificationSettings { status in
                call.resolve(["notifications": status, "granted": granted])
            }
        }
    }

    // MARK: - Location

    @objc func getCurrentLocation(_ call: CAPPluginCall) {
        let status = CommuteGeofenceManager.shared.authorizationStatus
        guard status == .authorizedAlways || status == .authorizedWhenInUse else {
            call.reject("위치 권한이 필요해요. '위치 권한 요청'을 먼저 눌러 주세요.", "PERMISSION_DENIED")
            return
        }
        CommuteGeofenceManager.shared.requestCurrentLocation { result in
            switch result {
            case .success(let location):
                call.resolve([
                    "latitude": location.coordinate.latitude,
                    "longitude": location.coordinate.longitude,
                    "accuracy": location.horizontalAccuracy,
                    "timestamp": location.timestamp.timeIntervalSince1970 * 1000.0
                ])
            case .failure(let error):
                call.reject(error.localizedDescription, "LOCATION_FAILED")
            }
        }
    }

    // MARK: - Geofence

    @objc func setGeofence(_ call: CAPPluginCall) {
        guard let latitude = call.getDouble("latitude"),
              let longitude = call.getDouble("longitude") else {
            call.reject("위도와 경도가 필요해요.", "INVALID_ARGUMENT")
            return
        }
        let radius = call.getDouble("radius") ?? 200
        let label = call.getString("label") ?? "회사"
        let geofence = CommuteGeofence(latitude: latitude,
                                       longitude: longitude,
                                       radius: max(50, min(radius, 1000)),
                                       label: label)
        let previous = CommuteStore.geofence
        let moved = previous == nil
            || previous!.latitude != geofence.latitude
            || previous!.longitude != geofence.longitude
            || previous!.radius != geofence.radius
        CommuteStore.geofence = geofence
        CommuteStore.lastError = nil
        if moved {
            // A new area: forget the old inside/outside state so it can be re-baselined.
            CommuteStore.resetRegionState()
        }
        CommuteGeofenceManager.shared.startMonitoring(geofence)
        getNotificationSettings { settings in
            var payload = self.statusPayload(notificationStatus: settings)
            payload["saved"] = true
            call.resolve(payload)
        }
    }

    @objc func clearGeofence(_ call: CAPPluginCall) {
        CommuteGeofenceManager.shared.stopMonitoring()
        CommuteStore.geofence = nil
        CommuteStore.resetRegionState()
        CommuteForgetScheduler.cancelAll(clear: true)
        getNotificationSettings { settings in
            call.resolve(self.statusPayload(notificationStatus: settings))
        }
    }

    // MARK: - Notification taps

    @objc func getPendingAction(_ call: CAPPluginCall) {
        if let payload = CommuteStore.takePending() {
            call.resolve(payload)
        } else {
            call.resolve([:])
        }
    }

    @objc func clearDelivered(_ call: CAPPluginCall) {
        UNUserNotificationCenter.current().removeAllDeliveredNotifications()
        call.resolve()
    }

    /// Fires the same notification the geofence would, so the user can check the
    /// tap → prefill → save flow without driving to the office.
    @objc func testNotification(_ call: CAPPluginCall) {
        let type = call.getString("type") == "out" ? "out" : "in"
        let delay = call.getDouble("delay") ?? 5
        CommuteGeofenceManager.shared.postNotification(type: type, at: Date(), delayed: delay)
        call.resolve(["scheduled": true, "type": type, "delay": delay])
    }

    // MARK: - 퇴근 독촉 알림

    /// 웹앱의 오늘 기록(출근·퇴근·정규 퇴근시간)과 설정을 받아 독촉 예약을 다시 맞춘다.
    /// 앱이 꺼져 있을 때는 지오펜스 진입/이탈이 같은 일을 대신한다.
    @objc func syncCommuteNag(_ call: CAPPluginCall) {
        var settings = CommuteNagStore.settings
        if let enabled = call.getBool("enabled") { settings.enabled = enabled }
        if let regularEnd = call.getString("regularEnd"), !regularEnd.isEmpty { settings.regularEnd = regularEnd }
        CommuteNagStore.settings = settings

        let today = CommuteNagPlanner.dayKey()
        let requestedDay = call.getString("date") ?? today
        if requestedDay == today {
            let clockIn = call.getString("clockIn") ?? ""
            let clockOut = call.getString("clockOut") ?? ""
            let regularEnd = call.getString("regularEnd") ?? ""
            // 자동 기록의 덮어쓰기 판단에도 같은 기록을 반영한다.
            CommuteAutoRecordStore.merge(webClockIn: clockIn, webClockOut: clockOut, date: today)
            if clockIn.isEmpty && clockOut.isEmpty {
                CommuteNagStore.day = nil
                CommuteNagStore.clearSuppression()
            } else {
                var day = CommuteNagStore.day
                if day == nil || day!.date != today {
                    day = CommuteNagStore.Day(date: today,
                                              clockIn: "",
                                              clockOut: "",
                                              regularEnd: regularEnd.isEmpty ? settings.regularEnd : regularEnd)
                }
                day!.clockIn = clockIn
                day!.clockOut = clockOut
                day!.regularEnd = regularEnd.isEmpty ? settings.regularEnd : regularEnd
                CommuteNagStore.day = day
                if !clockOut.isEmpty {
                    CommuteNagStore.suppress(date: today, reason: "clockOut")
                } else if CommuteNagStore.suppressionReason(date: today) == "clockOut" {
                    // 퇴근 기록을 지웠다면 다시 독촉한다.
                    CommuteNagStore.clearSuppression()
                }
            }
        }
        CommuteNagScheduler.refresh()
        // 퇴근 기록이 저장되면 남은 '기록 깜빡' 알림을 즉시 지운다.
        CommuteForgetScheduler.refresh()
        // 위젯도 같은 오늘 기록을 보도록 맞춘다.
        if requestedDay == today {
            CommuteWidgetStore.applyWeb(clockIn: call.getString("clockIn") ?? "",
                                        clockOut: call.getString("clockOut") ?? "",
                                        regularStart: call.getString("regularStart") ?? "",
                                        regularEnd: call.getString("regularEnd") ?? "")
            CommuteWidgetCenter.reload()
        }
        var payload = nagPayload()
        payload["saved"] = true
        payload["forget"] = CommuteForgetScheduler.status()
        call.resolve(payload)
    }

    @objc func getNagStatus(_ call: CAPPluginCall) {
        call.resolve(nagPayload())
    }

    @objc func testNagNotification(_ call: CAPPluginCall) {
        let round = call.getInt("round") ?? 1
        let delay = call.getDouble("delay") ?? 60
        CommuteNagScheduler.scheduleTest(round: round, delay: delay)
        call.resolve(["scheduled": true, "round": round, "delay": delay])
    }

    // MARK: - 퇴근 기록 깜빡 알림

    @objc func getForgetStatus(_ call: CAPPluginCall) {
        call.resolve(CommuteForgetScheduler.status())
    }

    @objc func testForgetNotification(_ call: CAPPluginCall) {
        let round = call.getInt("round") ?? 1
        let delay = call.getDouble("delay") ?? 60
        CommuteForgetScheduler.scheduleTest(round: round, delay: delay)
        call.resolve(["scheduled": true, "round": round, "delay": delay])
    }

    // MARK: - 자동 출퇴근 기록

    /// 설정 화면의 토글을 바로 `UserDefaults` 에 저장한다(앱이 꺼져 있어도 지오펜스가 읽음).
    @objc func syncAutoRecordSettings(_ call: CAPPluginCall) {
        var settings = CommuteAutoRecordStore.settings
        if let value = call.getBool("autoClockIn") { settings.autoClockIn = value }
        if let value = call.getBool("autoClockOut") { settings.autoClockOut = value }
        CommuteAutoRecordStore.settings = settings
        call.resolve(CommuteAutoRecordStore.status())
    }

    @objc func getAutoRecordStatus(_ call: CAPPluginCall) {
        call.resolve(CommuteAutoRecordStore.status())
    }

    /// 앱이 꺼져 있는 동안 만들어진 자동 기록을 웹 레이어에 넘긴다(지우지 않음).
    @objc func getAutoRecords(_ call: CAPPluginCall) {
        call.resolve([
            "records": CommuteAutoRecordStore.pending.map { CommuteAutoRecordStore.payload($0) },
            "settings": CommuteAutoRecordStore.settingsPayload()
        ])
    }

    /// 웹이 기록에 반영한 뒤 큐에서 지운다. 남은 건수를 돌려준다.
    @objc func ackAutoRecords(_ call: CAPPluginCall) {
        let ids = call.getArray("ids", String.self) ?? []
        CommuteAutoRecordStore.acknowledge(ids: ids)
        call.resolve(["remaining": CommuteAutoRecordStore.pending.count])
    }

    // MARK: - 홈·잠금화면 위젯(1.0 빌드 8)

    /// 웹의 오늘 기록을 App Group 저장소에 쓰고 위젯을 새로 고친다.
    @objc func syncWidgetState(_ call: CAPPluginCall) {
        guard let payload = call.getString("payload"), !payload.isEmpty else {
            call.reject("payload가 비어 있어요", "INVALID_ARGUMENT")
            return
        }
        guard let defaults = CommuteWidgetShared.defaults else {
            call.reject("App Group 저장소를 열 수 없어요 (\(CommuteWidgetShared.appGroupId))", "APP_GROUP_UNAVAILABLE")
            return
        }
        // 웹이 만든 JSON을 그대로 보관한다(위젯이 같은 구조로 디코드한다).
        defaults.set(payload, forKey: CommuteWidgetShared.stateKey)
        CommuteWidgetCenter.reload()
        var result = CommuteWidgetStore.status()
        result["saved"] = true
        call.resolve(result)
    }

    /// 위젯 버튼에서 찍어 둔 기록을 웹에 넘긴다(지우지 않음).
    @objc func getWidgetRecords(_ call: CAPPluginCall) {
        call.resolve([
            "records": CommuteWidgetStore.pending().map { CommuteWidgetStore.payload($0) },
            "state": CommuteWidgetStore.status()
        ])
    }

    /// 웹이 기록에 반영한 뒤 큐에서 지운다.
    @objc func ackWidgetRecords(_ call: CAPPluginCall) {
        let ids = call.getArray("ids", String.self) ?? []
        CommuteWidgetStore.acknowledge(ids: ids)
        call.resolve(["remaining": CommuteWidgetStore.pending().count])
    }

    @objc func reloadWidget(_ call: CAPPluginCall) {
        CommuteWidgetCenter.reload()
        call.resolve(["reloaded": true])
    }

    @objc func getWidgetStatus(_ call: CAPPluginCall) {
        call.resolve(CommuteWidgetStore.status())
    }

    /// 설정 화면에 보여 줄 오늘의 독촉 계획(문구 포함).
    private func nagPayload(now: Date = Date()) -> [String: Any] {
        let settings = CommuteNagStore.settings
        let today = CommuteNagPlanner.dayKey(now)
        let day = CommuteNagStore.day
        let sameDay = day != nil && day!.date == today
        let clockIn = sameDay ? (day?.clockIn ?? "") : ""
        let clockOut = sameDay ? (day?.clockOut ?? "") : ""
        let storedEnd = sameDay ? (day?.regularEnd ?? "") : ""
        let regularEndText = storedEnd.isEmpty ? settings.regularEnd : storedEnd
        let regularEndMinutes = CommuteNagPlanner.minutes(from: regularEndText) ?? (17 * 60 + 30)

        var rounds: [[String: Any]] = []
        for index in 1...CommuteNagPlanner.roundCount {
            let fireMinutes = regularEndMinutes + CommuteNagPlanner.intervalMinutes * index
            guard let fireDate = CommuteNagPlanner.date(day: today, minutes: fireMinutes) else { continue }
            rounds.append([
                "round": index,
                "time": CommuteNagPlanner.hhmm(fireDate),
                "phrase": CommuteNagCopy.phrase(day: today, round: index),
                "passed": fireDate.timeIntervalSince(now) <= 0
            ])
        }

        let insideNow = CommuteGeofenceManager.shared.isCurrentlyInside
        var state = "waitingClockIn"
        if !settings.enabled {
            state = "disabled"
        } else if !clockOut.isEmpty {
            state = "clockedOut"
        } else if CommuteNagStore.suppressionReason(date: today) == "geofenceExit", !insideNow {
            state = "leftOffice"
        } else if !clockIn.isEmpty || insideNow {
            state = "scheduled"
        }

        let planned = state == "scheduled"
            ? CommuteNagPlanner.rounds(day: today, regularEndMinutes: regularEndMinutes, now: now)
            : []

        return [
            "enabled": settings.enabled,
            "date": today,
            "regularEnd": regularEndText,
            "clockIn": clockIn,
            "clockOut": clockOut,
            "state": state,
            "rounds": rounds,
            "scheduled": planned.map { $0.round },
            "scheduledTimes": planned.map { CommuteNagPlanner.hhmm($0.fireDate) },
            "nextTime": planned.first.map { CommuteNagPlanner.hhmm($0.fireDate) } ?? "",
            "totalPhrases": CommuteNagCopy.totalCount
        ]
    }
}
