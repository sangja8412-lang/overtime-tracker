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
}

/// Owns UNUserNotificationCenter so that a tap works even when the app was
/// launched from a terminated state (the delegate is installed in AppDelegate
/// before the web view exists).
public final class CommuteNotificationHandler: NSObject, UNUserNotificationCenterDelegate {
    public static let shared = CommuteNotificationHandler()

    private var started = false

    public func start() {
        if started { return }
        started = true
        let center = UNUserNotificationCenter.current()
        center.delegate = self
        let record = UNNotificationAction(identifier: "RECORD", title: "기록하기", options: [.foreground])
        let later = UNNotificationAction(identifier: "LATER", title: "나중에", options: [])
        let category = UNNotificationCategory(identifier: "COMMUTE",
                                              actions: [record, later],
                                              intentIdentifiers: [],
                                              options: [])
        center.setNotificationCategories([category])
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
        var payload: [String: Any] = [:]
        if let type = info["type"] as? String { payload["type"] = type }
        if let timestamp = info["timestamp"] as? String { payload["timestamp"] = timestamp }
        if let epoch = info["epoch"] as? Double { payload["epoch"] = epoch }
        if let date = info["date"] as? String { payload["date"] = date }
        if let radius = info["radius"] as? Double { payload["radius"] = radius }
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

    private func handleTransition(inside: Bool, baselineIfUnknown: Bool = false) {
        guard CommuteStore.geofence != nil else { return }
        let newState = inside ? "inside" : "outside"
        let previous = CommuteStore.lastRegionState
        CommuteStore.lastRegionState = newState
        if previous == nil {
            // First determination after install/save: this is the baseline, not a transition.
            if baselineIfUnknown { return }
        } else if previous == newState {
            return
        }
        let type = inside ? "in" : "out"
        guard CommuteStore.canNotify(type: type) else { return }
        postNotification(type: type, at: Date())
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
        content.categoryIdentifier = "COMMUTE"
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
        CAPPluginMethod(name: "testNotification", returnType: CAPPluginReturnPromise)
    ]

    private var observer: NSObjectProtocol?

    override public func load() {
        CommuteNotificationHandler.shared.start()
        CommuteGeofenceManager.shared.restoreMonitoring()
        observer = NotificationCenter.default.addObserver(forName: .commuteActionTapped,
                                                          object: nil,
                                                          queue: .main) { [weak self] note in
            guard let self = self else { return }
            let payload = note.userInfo as? [String: Any] ?? [:]
            self.notifyListeners("commuteAction", data: payload)
        }
    }

    deinit {
        if let observer = observer {
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
            "backgroundUpdates": manager.locationManager.allowsBackgroundLocationUpdates
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
}
