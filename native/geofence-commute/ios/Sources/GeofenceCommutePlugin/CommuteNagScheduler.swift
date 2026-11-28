import Foundation
import UserNotifications

// MARK: - 퇴근 독촉 알림

/// 퇴근 독촉 알림 계획(순수 계산).
///
/// - 기준: 앱 설정의 정규 퇴근시간(예: 17:30)
/// - 예약: 퇴근시간 +1시간, +2시간, +3시간, +4시간 (1시간 간격, 최대 4회)
/// - 출근 시각이 이미 지난 회차는 건너뛰고 남은 회차만 예약한다.
/// - 자정을 넘는 회차는 만들지 않는다(새벽 독촉 방지).
struct CommuteNagRound {
    let round: Int
    let fireDate: Date
    let phrase: String
}

enum CommuteNagPlanner {
    static let roundCount = 4
    static let intervalMinutes = 60

    static func dayKey(_ date: Date = Date(), calendar: Calendar = .current) -> String {
        let parts = calendar.dateComponents([.year, .month, .day], from: date)
        return String(format: "%04d-%02d-%02d", parts.year ?? 1970, parts.month ?? 1, parts.day ?? 1)
    }

    static func hhmm(_ date: Date, calendar: Calendar = .current) -> String {
        let parts = calendar.dateComponents([.hour, .minute], from: date)
        return String(format: "%02d:%02d", parts.hour ?? 0, parts.minute ?? 0)
    }

    /// "HH:mm" → 자정부터의 분. 형식이 틀리면 nil.
    static func minutes(from text: String) -> Int? {
        let parts = text.split(separator: ":")
        guard parts.count == 2,
              let hour = Int(parts[0]),
              let minute = Int(parts[1]),
              hour >= 0, hour < 24, minute >= 0, minute < 60 else { return nil }
        return hour * 60 + minute
    }

    /// day(YYYY-MM-DD) 의 자정 + minutes. 같은 날짜를 넘어가면 nil.
    static func date(day: String, minutes: Int, calendar: Calendar = .current) -> Date? {
        guard minutes >= 0, minutes < 24 * 60 else { return nil }
        let parts = day.split(separator: "-").compactMap { Int($0) }
        guard parts.count == 3 else { return nil }
        var components = DateComponents()
        components.year = parts[0]
        components.month = parts[1]
        components.day = parts[2]
        components.hour = minutes / 60
        components.minute = minutes % 60
        components.second = 0
        return calendar.date(from: components)
    }

    /// 지금부터 아직 울릴 회차만 고른다(지난 시각·자정 이후 회차 제외).
    static func rounds(day: String,
                       regularEndMinutes: Int,
                       now: Date,
                       calendar: Calendar = .current) -> [CommuteNagRound] {
        var result: [CommuteNagRound] = []
        for index in 1...roundCount {
            let fireMinutes = regularEndMinutes + intervalMinutes * index
            guard let fireDate = date(day: day, minutes: fireMinutes, calendar: calendar) else { continue }
            if fireDate.timeIntervalSince(now) <= 30 { continue }
            result.append(CommuteNagRound(round: index,
                                          fireDate: fireDate,
                                          phrase: CommuteNagCopy.phrase(day: day, round: index, calendar: calendar)))
        }
        return result
    }
}

// MARK: - 저장소 (앱이 종료돼도 남는 상태)

enum CommuteNagStore {
    private static let defaults = UserDefaults.standard
    private static let settingsKey = "nag.settings.v1"
    private static let dayKey = "nag.day.v1"
    private static let suppressionKey = "nag.suppressed.v1"

    struct Settings: Codable {
        var enabled: Bool
        var regularEnd: String
    }

    /// 오늘의 출퇴근 상태. 웹앱이 저장한 기록이 syncCommuteNag 로 들어온다.
    struct Day: Codable {
        var date: String
        var clockIn: String
        var clockOut: String
        var regularEnd: String
    }

    /// 오늘은 더 이상 독촉하지 않는다는 표시 (퇴근 저장 / 회사 반경 이탈).
    struct Suppression: Codable {
        var date: String
        var reason: String
    }

    static var settings: Settings {
        get {
            guard let data = defaults.data(forKey: settingsKey),
                  let value = try? JSONDecoder().decode(Settings.self, from: data) else {
                return Settings(enabled: true, regularEnd: "17:30")
            }
            return value
        }
        set { encode(newValue, key: settingsKey) }
    }

    static var day: Day? {
        get { decode(Day.self, key: dayKey) }
        set { encode(newValue, key: dayKey) }
    }

    static var suppression: Suppression? {
        get { decode(Suppression.self, key: suppressionKey) }
        set { encode(newValue, key: suppressionKey) }
    }

    static func suppress(date: String, reason: String) {
        suppression = Suppression(date: date, reason: reason)
    }

    static func clearSuppression() {
        suppression = nil
    }

    static func suppressionReason(date: String) -> String? {
        guard let value = suppression, value.date == date else { return nil }
        return value.reason
    }

    private static func decode<T: Decodable>(_ type: T.Type, key: String) -> T? {
        guard let data = defaults.data(forKey: key) else { return nil }
        return try? JSONDecoder().decode(type, from: data)
    }

    private static func encode<T: Encodable>(_ value: T?, key: String) {
        guard let value = value, let data = try? JSONEncoder().encode(value) else {
            defaults.removeObject(forKey: key)
            return
        }
        defaults.set(data, forKey: key)
    }
}

// MARK: - 예약/취소

enum CommuteNagScheduler {
    static let identifierPrefix = "commute-nag-"
    static let testIdentifierPrefix = "commute-nagtest-"

    static func identifier(day: String, round: Int) -> String {
        "\(identifierPrefix)\(day)-\(round)"
    }

    /// 저장된 출근/퇴근 상태에 맞춰 예약을 다시 맞춘다. 여러 번 불러도 안전하다.
    static func refresh(now: Date = Date(), calendar: Calendar = .current) {
        let settings = CommuteNagStore.settings
        let today = CommuteNagPlanner.dayKey(now, calendar: calendar)
        guard settings.enabled else {
            cancelAll()
            return
        }
        guard let day = CommuteNagStore.day, day.date == today else {
            // 오늘 기록이 없거나 지난 날짜의 상태만 남아 있으면 오늘 독촉은 없다.
            cancelAll()
            return
        }
        if !day.clockOut.isEmpty {
            CommuteNagStore.suppress(date: today, reason: "clockOut")
            cancelAll()
            return
        }
        let insideNow = CommuteGeofenceManager.shared.isCurrentlyInside
        if CommuteNagStore.suppressionReason(date: today) == "geofenceExit", !insideNow {
            cancelAll()
            return
        }
        guard !day.clockIn.isEmpty || insideNow else {
            cancelAll()
            return
        }
        let regularEndText = day.regularEnd.isEmpty ? settings.regularEnd : day.regularEnd
        let regularEndMinutes = CommuteNagPlanner.minutes(from: regularEndText)
            ?? CommuteNagPlanner.minutes(from: settings.regularEnd)
            ?? (17 * 60 + 30)
        let rounds = CommuteNagPlanner.rounds(day: today,
                                             regularEndMinutes: regularEndMinutes,
                                             now: now,
                                             calendar: calendar)
        schedule(rounds: rounds, day: today, calendar: calendar)
    }

    /// 오늘 남은 회차를 예약하고, 더 이상 유효하지 않은 독촉 알림은 지운다.
    static func schedule(rounds: [CommuteNagRound], day: String, calendar: Calendar = .current) {
        CommuteNotificationHandler.shared.start()
        pruneStale(keep: rounds.map { identifier(day: day, round: $0.round) })
        let center = UNUserNotificationCenter.current()
        for round in rounds {
            var components = calendar.dateComponents([.year, .month, .day, .hour, .minute], from: round.fireDate)
            components.second = 0
            let trigger = UNCalendarNotificationTrigger(dateMatching: components, repeats: false)
            let request = UNNotificationRequest(identifier: identifier(day: day, round: round.round),
                                                content: notificationContent(round: round, day: day, calendar: calendar),
                                                trigger: trigger)
            center.add(request) { error in
                if let error = error { CommuteStore.lastError = error.localizedDescription }
            }
        }
    }

    /// 오늘 남은 독촉 알림과 알림 센터에 남은 독촉 알림을 모두 지운다.
    static func cancelAll() {
        let center = UNUserNotificationCenter.current()
        center.getPendingNotificationRequests { requests in
            let ids = requests.map { $0.identifier }.filter { $0.hasPrefix(identifierPrefix) }
            if !ids.isEmpty { center.removePendingNotificationRequests(withIdentifiers: ids) }
        }
        center.getDeliveredNotifications { delivered in
            let ids = delivered.map { $0.request.identifier }.filter { $0.hasPrefix(identifierPrefix) }
            if !ids.isEmpty { center.removeDeliveredNotifications(withIdentifiers: ids) }
        }
    }

    /// keep 에 없는 독촉 알림(지난 날짜·이미 지난 회차)만 지운다.
    static func pruneStale(keep: [String]) {
        UNUserNotificationCenter.current().getPendingNotificationRequests { requests in
            let ids = requests.map { $0.identifier }
                .filter { $0.hasPrefix(identifierPrefix) && !keep.contains($0) }
            if !ids.isEmpty {
                UNUserNotificationCenter.current().removePendingNotificationRequests(withIdentifiers: ids)
            }
        }
    }

    static func notificationContent(round: CommuteNagRound, day: String, calendar: Calendar = .current) -> UNMutableNotificationContent {
        let content = UNMutableNotificationContent()
        content.title = "🦫 퇴근했니?"
        content.subtitle = "퇴근시간 +\(round.round)시간"
        content.body = round.phrase
        content.sound = .default
        content.categoryIdentifier = CommuteNotificationHandler.clockOutCategory
        content.threadIdentifier = "commute"
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        let parts = calendar.dateComponents([.year, .month, .day], from: round.fireDate)
        let dateText = String(format: "%04d-%02d-%02d", parts.year ?? 1970, parts.month ?? 1, parts.day ?? 1)
        content.userInfo = [
            "type": "out",
            "nag": true,
            "round": round.round,
            "timestamp": formatter.string(from: round.fireDate),
            "epoch": round.fireDate.timeIntervalSince1970 * 1000.0,
            "date": dateText
        ]
        return content
    }

    /// 설정 화면의 [1분 뒤 테스트] 버튼용. 오늘 회차의 문구를 그대로 쓴다.
    static func scheduleTest(round: Int = 1, delay: TimeInterval = 60, now: Date = Date()) {
        CommuteNotificationHandler.shared.start()
        let value = max(1, min(CommuteNagPlanner.roundCount, round))
        let fireDate = now.addingTimeInterval(delay)
        let day = CommuteNagPlanner.dayKey(now)
        let content = notificationContent(round: CommuteNagRound(round: value,
                                                                 fireDate: fireDate,
                                                                 phrase: CommuteNagCopy.phrase(day: day, round: value)),
                                          day: day)
        let trigger = UNTimeIntervalNotificationTrigger(timeInterval: max(1, delay), repeats: false)
        let request = UNNotificationRequest(identifier: "\(testIdentifierPrefix)\(Int(fireDate.timeIntervalSince1970))-\(value)",
                                            content: content,
                                            trigger: trigger)
        UNUserNotificationCenter.current().add(request) { error in
            if let error = error { CommuteStore.lastError = error.localizedDescription }
        }
    }
}
