import Foundation
import UserNotifications
import WidgetKit

// MARK: - 알림 액션 버튼 (애플워치에서 앱을 열지 않고 바로 출근/퇴근 찍기)
//
// - 출근 확인 알림(`COMMUTE_IN`)  : [출근 찍기] [기록하기] [나중에]
// - 퇴근 확인/야근 독촉/기록 깜빡(`COMMUTE_OUT`, 기존 `COMMUTE`) : [퇴근 찍기] [기록하기] [나중에]
//
// 액션은 `options: []`(백그라운드)라서 알림에서 버튼만 누르면 앱이 앞으로 나오지 않은 채로
// 기록이 남는다. 이미 기록된 시각은 덮어쓰지 않는다.

enum CommuteNotificationAction {
    static let clockIn = "COMMUTE_CLOCK_IN"
    static let clockOut = "COMMUTE_CLOCK_OUT"
    static let record = "RECORD"
    static let later = "LATER"

    static func isQuickRecord(_ identifier: String) -> Bool {
        identifier == clockIn || identifier == clockOut
    }

    static func type(for identifier: String) -> String? {
        if identifier == clockIn { return "in" }
        if identifier == clockOut { return "out" }
        return nil
    }
}

/// 액션 버튼으로 들어온 출근/퇴근을 백그라운드에서 처리한다.
enum CommuteActionRecorder {

    /// - Parameters:
    ///   - type: "in" / "out"
    ///   - userInfo: 알림 payload (알림이 뜬 시각·날짜·'깜빡' 알림의 회사 이탈 시각)
    /// - Returns: 새로 기록했으면 true, 이미 기록이 있어 그대로 두었으면 false
    @discardableResult
    static func record(type: String, userInfo: [AnyHashable: Any], now: Date = Date()) -> Bool {
        let day = (userInfo["date"] as? String) ?? CommuteNagPlanner.dayKey(now)
        let when = CommuteWidgetStore.payloadDate(userInfo, now: now)
        let time = CommuteNagPlanner.hhmm(when)

        let entry = CommuteAutoRecordStore.record(date: day, type: type, time: time, at: when)

        // 알림 시각이 오늘이 아니면(자정을 넘긴 알림) 독촉 상태는 건드리지 않는다.
        let today = CommuteNagPlanner.dayKey(now)
        if day == today {
            if type == "in" {
                var current = CommuteNagStore.day
                if current == nil || current!.date != today {
                    current = CommuteNagStore.Day(date: today,
                                                  clockIn: time,
                                                  clockOut: "",
                                                  regularEnd: CommuteNagStore.settings.regularEnd)
                } else if current!.clockIn.isEmpty {
                    current!.clockIn = time
                }
                CommuteNagStore.day = current
                CommuteNagStore.clearSuppression()
            } else if entry != nil {
                // 퇴근이 찍히면 오늘 남은 야근 독촉·기록 깜빡 알림을 즉시 모두 취소한다.
                var current = CommuteNagStore.day
                if current == nil || current!.date != today {
                    current = CommuteNagStore.Day(date: today,
                                                  clockIn: CommuteAutoRecordStore.dayState(for: today).clockIn,
                                                  clockOut: time,
                                                  regularEnd: CommuteNagStore.settings.regularEnd)
                } else {
                    current!.clockOut = time
                }
                CommuteNagStore.day = current
                CommuteNagStore.suppress(date: today, reason: "clockOut")
                CommuteNagScheduler.cancelAll()
                CommuteForgetScheduler.cancelAll(clear: true)
            }
        }
        CommuteNagScheduler.refresh()
        CommuteForgetScheduler.refresh()

        // 위젯이 바로 새 시각을 보여 주도록 App Group 상태도 함께 갱신한다.
        // (기록 자체는 CommuteAutoRecordStore 대기 큐로 웹에 전달되므로 여기서는 큐에 넣지 않는다)
        CommuteWidgetStore.record(type: type, at: when, source: "watch", enqueue: false)
        CommuteWidgetCenter.reload()

        if entry == nil {
            // 이미 기록이 있어 덮어쓰지 않았다: 남은 알림만 정리하고 조용히 끝낸다.
            CommuteNotificationHandler.shared.recordKeptNotice(type: type, time: time)
        }
        return entry != nil
    }
}

/// 위젯 타임라인 새로 고침 (앱 타깃과 익스텐션이 같은 코드를 쓴다)
enum CommuteWidgetCenter {
    static func reload() {
        WidgetCenter.shared.reloadAllTimelines()
    }
}
