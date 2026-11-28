import Foundation

// MARK: - 홈·잠금화면 위젯과 주고받는 저장소 (App Group)
//
// 이 파일은 **앱(GeofenceCommute 플러그인)** 과 **위젯 익스텐션** 두 타깃에 함께 들어간다.
// 그래서 Foundation 만 쓰고, 플러그인 모듈의 다른 타입(CommuteNagPlanner 등)을 참조하지 않는다.
// 키 이름·JSON 필드 이름은 웹(`www/index.html`의 `widgetPayload()`)과 반드시 같아야 한다.
//
// - `widget_state`          : 오늘 출퇴근 현황(앱이 쓰고 위젯이 읽는다)
// - `widget_pending_records` : 위젯에서 찍은 기록 대기 큐(앱이 다음에 열릴 때 읽어 반영)

enum CommuteWidgetShared {
    static let appGroupId = "group.com.kyeore.overtimetracker"
    static let stateKey = "widget_state"
    static let pendingKey = "widget_pending_records"

    /// 위젯 번들 ID(로그·검사용)
    static let widgetBundleId = "com.kyeore.overtimetracker.widget"

    static var defaults: UserDefaults? { UserDefaults(suiteName: appGroupId) }

    /// 앱 그룹이 아직 켜지지 않은(프로파일이 없는) 상태를 구분하기 위한 값.
    static var isAvailable: Bool { defaults != nil }
}

/// 오늘 하루의 출퇴근 현황 (앱이 써 두고 위젯이 읽는다).
struct CommuteWidgetState: Codable {
    var date: String
    var clockIn: String
    var clockOut: String
    var regularStart: String
    var regularEnd: String
    var updatedAt: String?

    var hasClockIn: Bool { !clockIn.isEmpty }
    var hasClockOut: Bool { !clockOut.isEmpty }

    /// before(미출근) / working(근무 중) / done(퇴근 완료)
    var phase: String {
        if hasClockOut { return "done" }
        if hasClockIn { return "working" }
        return "before"
    }

    static func empty(date: String, regularStart: String = "08:30", regularEnd: String = "17:30") -> CommuteWidgetState {
        CommuteWidgetState(date: date, clockIn: "", clockOut: "",
                           regularStart: regularStart, regularEnd: regularEnd, updatedAt: nil)
    }

    /// 위젯 갤러리 미리보기용
    static var preview: CommuteWidgetState {
        CommuteWidgetState(date: CommuteWidgetStore.todayKey(), clockIn: "08:52", clockOut: "",
                           regularStart: "08:30", regularEnd: "17:30", updatedAt: nil)
    }

    init(date: String, clockIn: String, clockOut: String,
         regularStart: String, regularEnd: String, updatedAt: String?) {
        self.date = date
        self.clockIn = clockIn
        self.clockOut = clockOut
        self.regularStart = regularStart
        self.regularEnd = regularEnd
        self.updatedAt = updatedAt
    }

    enum CodingKeys: String, CodingKey {
        case date, clockIn, clockOut, regularStart, regularEnd, updatedAt
    }

    /// 웹이 보낸 JSON 에 일부 필드가 없어도 기본값으로 읽는다(앱 버전이 섞여도 위젯이 깨지지 않게).
    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        date = try container.decodeIfPresent(String.self, forKey: .date) ?? ""
        clockIn = try container.decodeIfPresent(String.self, forKey: .clockIn) ?? ""
        clockOut = try container.decodeIfPresent(String.self, forKey: .clockOut) ?? ""
        regularStart = try container.decodeIfPresent(String.self, forKey: .regularStart) ?? "08:30"
        regularEnd = try container.decodeIfPresent(String.self, forKey: .regularEnd) ?? "17:30"
        updatedAt = try container.decodeIfPresent(String.self, forKey: .updatedAt)
    }
}

/// 위젯(또는 알림 액션 버튼)에서 찍은 기록 한 건. 앱이 다음에 열릴 때 기록에 반영한다.
struct CommuteWidgetRecord: Codable {
    var id: String
    var date: String
    var type: String      // "in" / "out"
    var time: String      // "HH:mm"
    var epoch: Double     // ms
    /// "widget" (위젯 버튼) / "watch" (알림 액션 버튼)
    var source: String
}

enum CommuteWidgetStore {
    // MARK: 날짜·시각

    /// 기기 로컬 날짜(yyyy-MM-dd) — 웹의 dayKey()와 같은 기준
    static func todayKey(_ date: Date = Date(), calendar: Calendar = .current) -> String {
        let parts = calendar.dateComponents([.year, .month, .day], from: date)
        return String(format: "%04d-%02d-%02d", parts.year ?? 1970, parts.month ?? 1, parts.day ?? 1)
    }

    static func hhmm(_ date: Date, calendar: Calendar = .current) -> String {
        let parts = calendar.dateComponents([.hour, .minute], from: date)
        return String(format: "%02d:%02d", parts.hour ?? 0, parts.minute ?? 0)
    }

    // MARK: 현황

    static func load() -> CommuteWidgetState? {
        guard let defaults = CommuteWidgetShared.defaults,
              let raw = defaults.string(forKey: CommuteWidgetShared.stateKey),
              let data = raw.data(using: .utf8) else { return nil }
        return try? JSONDecoder().decode(CommuteWidgetState.self, from: data)
    }

    static func save(_ state: CommuteWidgetState) {
        guard let defaults = CommuteWidgetShared.defaults,
              let data = try? JSONEncoder().encode(state),
              let raw = String(data: data, encoding: .utf8) else { return }
        defaults.set(raw, forKey: CommuteWidgetShared.stateKey)
    }

    /// 오늘 상태. 저장된 값이 다른 날짜면 오늘 기준 빈 상태로 본다.
    static func today(_ now: Date = Date()) -> CommuteWidgetState {
        let key = todayKey(now)
        guard var state = load() else { return .empty(date: key) }
        if state.date != key {
            state = .empty(date: key, regularStart: state.regularStart, regularEnd: state.regularEnd)
        }
        return state
    }

    // MARK: 대기 큐

    static func pending() -> [CommuteWidgetRecord] {
        guard let defaults = CommuteWidgetShared.defaults,
              let raw = defaults.string(forKey: CommuteWidgetShared.pendingKey),
              let data = raw.data(using: .utf8) else { return [] }
        return (try? JSONDecoder().decode([CommuteWidgetRecord].self, from: data)) ?? []
    }

    static func enqueue(_ record: CommuteWidgetRecord) {
        guard let defaults = CommuteWidgetShared.defaults else { return }
        var list = pending()
        list.removeAll { $0.date == record.date && $0.type == record.type }
        list.append(record)
        if let data = try? JSONEncoder().encode(list),
           let raw = String(data: data, encoding: .utf8) {
            defaults.set(raw, forKey: CommuteWidgetShared.pendingKey)
        }
    }

    /// 앱(웹)이 기록에 반영한 뒤 큐에서 지운다.
    static func acknowledge(ids: [String]) {
        guard !ids.isEmpty, let defaults = CommuteWidgetShared.defaults else { return }
        let list = pending().filter { !ids.contains($0.id) }
        if list.isEmpty {
            defaults.removeObject(forKey: CommuteWidgetShared.pendingKey)
            return
        }
        if let data = try? JSONEncoder().encode(list),
           let raw = String(data: data, encoding: .utf8) {
            defaults.set(raw, forKey: CommuteWidgetShared.pendingKey)
        }
    }

    // MARK: 기록

    /// 앱(웹)이 저장한 오늘 기록을 위젯 상태에 반영한다.
    /// 아직 웹이 반영하지 않은 위젯 기록(대기 큐)은 지우지 않는다.
    static func applyWeb(clockIn: String,
                         clockOut: String,
                         regularStart: String = "",
                         regularEnd: String = "",
                         now: Date = Date()) {
        var state = today(now)
        let list = pending()
        let waitingIn = list.contains { $0.date == state.date && $0.type == "in" }
        let waitingOut = list.contains { $0.date == state.date && $0.type == "out" }
        if !clockIn.isEmpty {
            state.clockIn = clockIn
        } else if !waitingIn {
            state.clockIn = ""
        }
        if !clockOut.isEmpty {
            state.clockOut = clockOut
        } else if !waitingOut {
            state.clockOut = ""
        }
        if !regularStart.isEmpty { state.regularStart = regularStart }
        if !regularEnd.isEmpty { state.regularEnd = regularEnd }
        state.updatedAt = ISO8601DateFormatter().string(from: now)
        save(state)
    }

    /// 출근/퇴근을 찍는다. **이미 기록된 시각은 덮어쓰지 않는다.**
    /// - Returns: 반영했으면 만들어진 기록, 아니면 nil.
    /// - `enqueue` 가 true 면 앱이 나중에 읽어 갈 대기 큐에도 넣는다(위젯 버튼).
    @discardableResult
    static func record(type: String,
                       at now: Date = Date(),
                       source: String,
                       enqueue: Bool = true) -> CommuteWidgetRecord? {
        var state = today(now)
        let date = state.date
        let time = hhmm(now)
        if type == "in" {
            guard state.clockIn.isEmpty else { return nil }
            state.clockIn = time
        } else {
            // 퇴근은 출근 기록이 있을 때만, 아직 퇴근 기록이 없을 때만
            guard !state.clockIn.isEmpty, state.clockOut.isEmpty else { return nil }
            state.clockOut = time
        }
        state.updatedAt = ISO8601DateFormatter().string(from: now)
        save(state)

        let entry = CommuteWidgetRecord(id: "\(date)-\(type)-\(Int(now.timeIntervalSince1970))",
                                        date: date,
                                        type: type,
                                        time: time,
                                        epoch: now.timeIntervalSince1970 * 1000.0,
                                        source: source)
        if enqueue { CommuteWidgetStore.enqueue(entry) }
        return entry
    }

    /// 알림 페이로드(userInfo)에 담긴 알림 시각. 없으면 지금.
    static func payloadDate(_ userInfo: [AnyHashable: Any], now: Date = Date()) -> Date {
        if let epoch = userInfo["epoch"] as? Double, epoch > 0 {
            return Date(timeIntervalSince1970: epoch / 1000.0)
        }
        if let text = userInfo["timestamp"] as? String {
            let formatter = ISO8601DateFormatter()
            formatter.formatOptions = [.withInternetDateTime]
            if let date = formatter.date(from: text) { return date }
        }
        return now
    }

    static func payload(_ record: CommuteWidgetRecord) -> [String: Any] {
        ["id": record.id, "date": record.date, "type": record.type,
         "time": record.time, "epoch": record.epoch, "source": record.source]
    }

    /// 설정 화면·검사용 요약
    static func status(_ now: Date = Date()) -> [String: Any] {
        let state = today(now)
        return [
            "available": CommuteWidgetShared.isAvailable,
            "appGroup": CommuteWidgetShared.appGroupId,
            "date": state.date,
            "clockIn": state.clockIn,
            "clockOut": state.clockOut,
            "phase": state.phase,
            "regularStart": state.regularStart,
            "regularEnd": state.regularEnd,
            "pending": pending().count
        ]
    }
}
