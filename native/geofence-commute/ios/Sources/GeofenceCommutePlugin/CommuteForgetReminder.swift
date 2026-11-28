import Foundation
import UserNotifications

// MARK: - 퇴근 기록 깜빡 알림

/// 회사 반경을 벗어난 뒤 남는 '퇴근 기록 깜빡' 알림 계획(순수 계산).
///
/// - 기준: 회사 지오펜스를 **벗어난 시각**
/// - 예약: 이탈 +1시간, +2시간, +3시간 (1시간 간격, 최대 3회)
/// - 자정을 넘는 회차는 만들지 않는다(새벽 알림 방지).
/// - 이미 지난 회차는 만들지 않는다.
struct CommuteForgetRound {
    let round: Int
    let fireDate: Date
    let phrase: String
}

enum CommuteForgetPlanner {
    static let roundCount = 3
    static let intervalMinutes = 60

    /// exitDate(분 단위로 자른 이탈 시각) 기준으로 남은 회차를 고른다.
    static func rounds(exitDate: Date, now: Date = Date(), calendar: Calendar = .current) -> [CommuteForgetRound] {
        let exitDay = CommuteNagPlanner.dayKey(exitDate, calendar: calendar)
        var result: [CommuteForgetRound] = []
        for index in 1...roundCount {
            guard let fire = calendar.date(byAdding: .minute,
                                           value: intervalMinutes * index,
                                           to: exitDate) else { continue }
            // 자정을 넘기면 그날의 기록이 아니므로 예약하지 않는다.
            guard CommuteNagPlanner.dayKey(fire, calendar: calendar) == exitDay else { continue }
            if fire.timeIntervalSince(now) <= 30 { continue }
            result.append(CommuteForgetRound(round: index,
                                             fireDate: fire,
                                             phrase: CommuteForgetCopy.phrase(day: exitDay, round: index, calendar: calendar)))
        }
        return result
    }

    /// 초 단위를 버려서 예약 시각(분)과 표시 시각을 맞춘다.
    static func truncated(_ date: Date, calendar: Calendar = .current) -> Date {
        let parts = calendar.dateComponents([.year, .month, .day, .hour, .minute], from: date)
        return calendar.date(from: parts) ?? date
    }
}

// MARK: - 저장소 (앱이 종료돼도 남는 이탈 기록)

enum CommuteForgetStore {
    private static let defaults = UserDefaults.standard
    private static let key = "forget.reminder.v1"

    /// 오늘 회사 반경을 벗어난 시각. 이 값이 있으면 깜빡 알림의 기준이 된다.
    struct Reminder: Codable {
        var date: String
        var exitEpoch: Double
    }

    static var reminder: Reminder? {
        get {
            guard let data = defaults.data(forKey: key) else { return nil }
            return try? JSONDecoder().decode(Reminder.self, from: data)
        }
        set {
            guard let value = newValue, let data = try? JSONEncoder().encode(value) else {
                defaults.removeObject(forKey: key)
                return
            }
            defaults.set(data, forKey: key)
        }
    }
}

// MARK: - 예약/취소

enum CommuteForgetScheduler {
    static let identifierPrefix = "commute-forget-"
    static let testIdentifierPrefix = "commute-forgettest-"

    static func identifier(day: String, round: Int) -> String {
        "\(identifierPrefix)\(day)-\(round)"
    }

    /// 반경 이탈 직후 호출. 오늘 출근 기록이 있고 퇴근 기록이 비어 있으면
    /// 이탈 시각을 기준으로 +1·+2·+3시간 알림을 예약한다.
    @discardableResult
    static func scheduleAfterExit(exitDate: Date,
                                  day: CommuteNagStore.Day?,
                                  now: Date = Date(),
                                  calendar: Calendar = .current) -> [CommuteForgetRound] {
        let today = CommuteNagPlanner.dayKey(exitDate, calendar: calendar)
        guard CommuteNagStore.settings.enabled,
              let day = day, day.date == today,
              !day.clockIn.isEmpty,
              day.clockOut.isEmpty else {
            // 퇴근 기록이 이미 있거나 출근 기록이 없으면 깜빡 알림은 필요 없다.
            cancelAll(clear: true)
            return []
        }
        let exit = CommuteForgetPlanner.truncated(exitDate, calendar: calendar)
        CommuteForgetStore.reminder = CommuteForgetStore.Reminder(date: today,
                                                                  exitEpoch: exit.timeIntervalSince1970)
        let rounds = CommuteForgetPlanner.rounds(exitDate: exit, now: now, calendar: calendar)
        pruneStale(keep: rounds.map { identifier(day: today, round: $0.round) })
        add(rounds, day: today, exitDate: exit, calendar: calendar)
        return rounds
    }

    /// 앱이 열리거나 상태가 바뀔 때 예약을 다시 맞춘다. 여러 번 불러도 안전하다.
    static func refresh(now: Date = Date(), calendar: Calendar = .current) {
        let today = CommuteNagPlanner.dayKey(now, calendar: calendar)
        guard CommuteNagStore.settings.enabled else {
            cancelAll(clear: true)
            return
        }
        guard let reminder = CommuteForgetStore.reminder, reminder.date == today else {
            // 지난 날짜의 이탈 기록은 남길 이유가 없다(알림은 자정을 넘지 않는다).
            CommuteForgetStore.reminder = nil
            cancelPending()
            return
        }
        if let day = CommuteNagStore.day, day.date == today, !day.clockOut.isEmpty {
            // 퇴근 기록이 저장됐다 → 남은 깜빡 알림과 알림 센터의 알림을 즉시 지운다.
            cancelAll(clear: true)
            return
        }
        if CommuteGeofenceManager.shared.isCurrentlyInside {
            // 다시 회사 안이면 깜빡 알림은 필요 없다.
            cancelAll(clear: true)
            return
        }
        let exitDate = Date(timeIntervalSince1970: reminder.exitEpoch)
        let rounds = CommuteForgetPlanner.rounds(exitDate: exitDate, now: now, calendar: calendar)
        pruneStale(keep: rounds.map { identifier(day: today, round: $0.round) })
        add(rounds, day: today, exitDate: exitDate, calendar: calendar)
    }

    /// 예약과 알림 센터에 남은 깜빡 알림을 모두 지운다(clear 면 이탈 기록도 지운다).
    static func cancelAll(clear: Bool = true) {
        if clear { CommuteForgetStore.reminder = nil }
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

    /// 예약만 정리하고 알림 센터에 이미 뜬 알림은 그대로 둔다.
    static func cancelPending() {
        UNUserNotificationCenter.current().getPendingNotificationRequests { requests in
            let ids = requests.map { $0.identifier }.filter { $0.hasPrefix(identifierPrefix) }
            if !ids.isEmpty {
                UNUserNotificationCenter.current().removePendingNotificationRequests(withIdentifiers: ids)
            }
        }
    }

    /// keep 에 없는 깜빡 알림(지난 회차)만 지운다.
    static func pruneStale(keep: [String]) {
        UNUserNotificationCenter.current().getPendingNotificationRequests { requests in
            let ids = requests.map { $0.identifier }
                .filter { $0.hasPrefix(identifierPrefix) && !keep.contains($0) }
            if !ids.isEmpty {
                UNUserNotificationCenter.current().removePendingNotificationRequests(withIdentifiers: ids)
            }
        }
    }

    private static func add(_ rounds: [CommuteForgetRound],
                            day: String,
                            exitDate: Date,
                            calendar: Calendar = .current) {
        guard !rounds.isEmpty else { return }
        CommuteNotificationHandler.shared.start()
        let center = UNUserNotificationCenter.current()
        for round in rounds {
            var components = calendar.dateComponents([.year, .month, .day, .hour, .minute], from: round.fireDate)
            components.second = 0
            let trigger = UNCalendarNotificationTrigger(dateMatching: components, repeats: false)
            let request = UNNotificationRequest(identifier: identifier(day: day, round: round.round),
                                                content: notificationContent(round: round,
                                                                             day: day,
                                                                             exitDate: exitDate,
                                                                             calendar: calendar),
                                                trigger: trigger)
            center.add(request) { error in
                if let error = error { CommuteStore.lastError = error.localizedDescription }
            }
        }
    }

    static func notificationContent(round: CommuteForgetRound,
                                    day: String,
                                    exitDate: Date,
                                    calendar: Calendar = .current) -> UNMutableNotificationContent {
        let content = UNMutableNotificationContent()
        content.title = "🦫 퇴근 기록 깜빡?"
        content.subtitle = "회사 나온 지 \(round.round)시간"
        content.body = round.phrase
        content.sound = .default
        content.categoryIdentifier = "COMMUTE"
        content.threadIdentifier = "commute"
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        content.userInfo = [
            // type 은 기존 퇴근 알림과 같게 두고, forget 으로 '기록 깜빡' 알림을 구분한다.
            "type": "out",
            "nag": true,
            "forget": true,
            "round": round.round,
            // 알림을 누르면 이 시각(회사를 나선 시각)이 퇴근 칸에 채워진다.
            "exitEpoch": exitDate.timeIntervalSince1970 * 1000.0,
            "exitTime": CommuteNagPlanner.hhmm(exitDate, calendar: calendar),
            "timestamp": formatter.string(from: round.fireDate),
            "epoch": round.fireDate.timeIntervalSince1970 * 1000.0,
            "date": day
        ]
        return content
    }

    /// 설정 화면의 [1분 뒤 깜빡 테스트] 버튼용.
    static func scheduleTest(round: Int = 1,
                             delay: TimeInterval = 60,
                             now: Date = Date(),
                             calendar: Calendar = .current) {
        CommuteNotificationHandler.shared.start()
        let value = max(1, min(CommuteForgetPlanner.roundCount, round))
        let delay = max(1, delay)
        let exitDate = CommuteForgetPlanner.truncated(now, calendar: calendar)
        let fireDate = now.addingTimeInterval(delay)
        let day = CommuteNagPlanner.dayKey(now, calendar: calendar)
        let content = notificationContent(round: CommuteForgetRound(round: value,
                                                                    fireDate: fireDate,
                                                                    phrase: CommuteForgetCopy.phrase(day: day, round: value)),
                                          day: day,
                                          exitDate: exitDate,
                                          calendar: calendar)
        let trigger = UNTimeIntervalNotificationTrigger(timeInterval: delay, repeats: false)
        let request = UNNotificationRequest(identifier: "\(testIdentifierPrefix)\(Int(fireDate.timeIntervalSince1970))-\(value)",
                                            content: content,
                                            trigger: trigger)
        UNUserNotificationCenter.current().add(request) { error in
            if let error = error { CommuteStore.lastError = error.localizedDescription }
        }
    }

    /// 설정 화면에 보여 줄 오늘의 깜빡 알림 상태.
    static func status(now: Date = Date(), calendar: Calendar = .current) -> [String: Any] {
        let settings = CommuteNagStore.settings
        let today = CommuteNagPlanner.dayKey(now, calendar: calendar)
        let day = CommuteNagStore.day
        let sameDay = day != nil && day!.date == today
        let clockIn = sameDay ? (day?.clockIn ?? "") : ""
        let clockOut = sameDay ? (day?.clockOut ?? "") : ""
        let reminder = CommuteForgetStore.reminder
        let exitDate: Date? = (reminder != nil && reminder!.date == today)
            ? Date(timeIntervalSince1970: reminder!.exitEpoch)
            : nil

        var rounds: [[String: Any]] = []
        if let exitDate = exitDate {
            for index in 1...CommuteForgetPlanner.roundCount {
                guard let fire = calendar.date(byAdding: .minute,
                                               value: CommuteForgetPlanner.intervalMinutes * index,
                                               to: exitDate) else { continue }
                let nextDay = CommuteNagPlanner.dayKey(fire, calendar: calendar) != today
                rounds.append([
                    "round": index,
                    "time": CommuteNagPlanner.hhmm(fire, calendar: calendar),
                    "phrase": CommuteForgetCopy.phrase(day: today, round: index, calendar: calendar),
                    "passed": fire.timeIntervalSince(now) <= 0,
                    "skipped": nextDay
                ])
            }
        }

        let insideNow = CommuteGeofenceManager.shared.isCurrentlyInside
        let planned: [CommuteForgetRound] = (exitDate != nil && clockOut.isEmpty && !insideNow)
            ? CommuteForgetPlanner.rounds(exitDate: exitDate!, now: now, calendar: calendar)
            : []
        var state = "waitingExit"
        if !settings.enabled {
            state = "disabled"
        } else if !clockOut.isEmpty {
            state = "clockedOut"
        } else if exitDate != nil {
            state = planned.isEmpty ? "finished" : "scheduled"
        }

        return [
            "enabled": settings.enabled,
            "date": today,
            "clockIn": clockIn,
            "clockOut": clockOut,
            "state": state,
            "exitTime": exitDate.map { CommuteNagPlanner.hhmm($0, calendar: calendar) } ?? "",
            "rounds": rounds,
            "scheduled": planned.map { $0.round },
            "scheduledTimes": planned.map { CommuteNagPlanner.hhmm($0.fireDate, calendar: calendar) },
            "nextTime": planned.first.map { CommuteNagPlanner.hhmm($0.fireDate, calendar: calendar) } ?? "",
            "totalPhrases": CommuteForgetCopy.totalCount
        ]
    }
}
