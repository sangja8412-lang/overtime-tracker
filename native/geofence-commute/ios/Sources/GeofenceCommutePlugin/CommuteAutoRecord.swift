import Foundation

// MARK: - 자동 출퇴근 기록 (사용자가 설정에서 켤 때만 동작)

/// 반경 진입/이탈 때 출근·퇴근 기록을 자동으로 남기는 옵션의 저장소.
///
/// - 설정(자동 출근/자동 퇴근)은 둘 다 기본 **꺼짐**이고, 앱이 백그라운드·완전 종료
///   상태여도 읽을 수 있도록 `UserDefaults` 에 보관한다.
/// - 앱이 꺼져 있는 동안 지오펜스가 만든 기록은 **대기 큐**에 넣어 두고, 앱이 다시
///   열리면 웹 레이어가 가져가 `localStorage` 기록에 반영한 뒤 큐에서 지운다.
/// - 오늘 이미 기록된 시각은 절대 덮어쓰지 않는다(같은 날 같은 종류는 한 번만).
enum CommuteAutoRecordStore {
    private static let defaults = UserDefaults.standard
    private static let settingsKey = "autoRecord.settings.v1"
    private static let dayKey = "autoRecord.day.v1"
    private static let pendingKey = "autoRecord.pending.v1"

    struct Settings: Codable {
        var autoClockIn: Bool
        var autoClockOut: Bool

        static let off = Settings(autoClockIn: false, autoClockOut: false)
    }

    /// 오늘의 기록 상태(웹에서 저장된 값 + 자동 기록). 덮어쓰기 판단에 쓴다.
    struct Day: Codable {
        var date: String
        var clockIn: String
        var clockOut: String
    }

    /// 앱이 꺼져 있는 동안 지오펜스가 만든 기록 한 건.
    struct Pending: Codable {
        var id: String
        var date: String
        var type: String
        var time: String
        var epoch: Double
        var createdAt: Double
    }

    // MARK: 설정

    static var settings: Settings {
        get {
            guard let data = defaults.data(forKey: settingsKey),
                  let value = try? JSONDecoder().decode(Settings.self, from: data) else {
                return .off
            }
            return value
        }
        set {
            if let data = try? JSONEncoder().encode(newValue) {
                defaults.set(data, forKey: settingsKey)
            }
        }
    }

    // MARK: 오늘 상태 / 대기 큐

    static var day: Day? {
        get {
            guard let data = defaults.data(forKey: dayKey) else { return nil }
            return try? JSONDecoder().decode(Day.self, from: data)
        }
        set {
            guard let value = newValue, let data = try? JSONEncoder().encode(value) else {
                defaults.removeObject(forKey: dayKey)
                return
            }
            defaults.set(data, forKey: dayKey)
        }
    }

    static var pending: [Pending] {
        get {
            guard let data = defaults.data(forKey: pendingKey) else { return [] }
            return (try? JSONDecoder().decode([Pending].self, from: data)) ?? []
        }
        set {
            if newValue.isEmpty {
                defaults.removeObject(forKey: pendingKey)
            } else if let data = try? JSONEncoder().encode(newValue) {
                defaults.set(data, forKey: pendingKey)
            }
        }
    }

    static func dayState(for date: String) -> Day {
        if let day = day, day.date == date { return day }
        return Day(date: date, clockIn: "", clockOut: "")
    }

    /// 웹이 보낸 오늘 기록을 반영한다. 아직 웹에 반영되지 않은 자동 기록(대기 큐)은 지우지 않는다.
    static func merge(webClockIn: String, webClockOut: String, date: String) {
        var current = dayState(for: date)
        let waitingIn = pending.contains { $0.date == date && $0.type == "in" }
        let waitingOut = pending.contains { $0.date == date && $0.type == "out" }
        if !webClockIn.isEmpty {
            current.clockIn = webClockIn
        } else if !waitingIn {
            current.clockIn = ""
        }
        if !webClockOut.isEmpty {
            current.clockOut = webClockOut
        } else if !waitingOut {
            current.clockOut = ""
        }
        day = current
    }

    /// 자동 기록 한 건을 만든다. 이미 기록이 있으면 nil 을 돌려주고 아무것도 바꾸지 않는다.
    /// - `type` 은 "in" / "out".
    static func record(date: String, type: String, time: String, at now: Date) -> Pending? {
        var current = dayState(for: date)
        if type == "in" {
            guard current.clockIn.isEmpty else { return nil }
            current.clockIn = time
        } else {
            // 퇴근은 출근 기록이 있을 때만, 그리고 아직 퇴근 기록이 없을 때만 남긴다.
            guard !current.clockIn.isEmpty, current.clockOut.isEmpty else { return nil }
            current.clockOut = time
        }
        day = current
        var list = pending.filter { !($0.date == date && $0.type == type) }
        let entry = Pending(id: "\(date)-\(type)-\(Int(now.timeIntervalSince1970))",
                            date: date,
                            type: type,
                            time: time,
                            epoch: now.timeIntervalSince1970 * 1000.0,
                            createdAt: now.timeIntervalSince1970)
        list.append(entry)
        pending = list
        return entry
    }

    /// 웹이 기록에 반영(또는 이미 같은 기록이 있음을 확인)한 뒤 큐에서 지운다.
    static func acknowledge(ids: [String]) {
        guard !ids.isEmpty else { return }
        pending = pending.filter { !ids.contains($0.id) }
    }

    // MARK: 웹/화면에 보여 줄 값

    static func payload(_ entry: Pending) -> [String: Any] {
        [
            "id": entry.id,
            "date": entry.date,
            "type": entry.type,
            "time": entry.time,
            "epoch": entry.epoch
        ]
    }

    static func settingsPayload() -> [String: Any] {
        let value = settings
        return ["autoClockIn": value.autoClockIn, "autoClockOut": value.autoClockOut]
    }

    static func status(now: Date = Date()) -> [String: Any] {
        let today = CommuteNagPlanner.dayKey(now)
        let current = dayState(for: today)
        return [
            "settings": settingsPayload(),
            "date": today,
            "clockIn": current.clockIn,
            "clockOut": current.clockOut,
            "pending": pending.map { payload($0) }
        ]
    }
}
