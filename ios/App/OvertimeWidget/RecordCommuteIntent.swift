import AppIntents
import WidgetKit

/// 위젯의 [출근 찍기] / [퇴근 찍기] 버튼이 실행하는 액션 (iOS 17+).
/// 앱을 열지 않고 App Group 저장소에 바로 기록하고, 앱은 다음에 열릴 때
/// 대기 큐(`widget_pending_records`)를 읽어 기록·독촉 알림에 반영한다.
/// 이미 기록된 시각은 덮어쓰지 않는다.
struct RecordCommuteIntent: AppIntent {
    static var title: LocalizedStringResource = "출근·퇴근 찍기"
    static var description: IntentDescription? = IntentDescription("위젯에서 오늘 출근 또는 퇴근 시각을 기록합니다.")
    static var openAppWhenRun: Bool = false

    /// "in" = 출근, "out" = 퇴근
    @Parameter(title: "구분")
    var type: String

    init() {}

    init(type: String) {
        self.type = type
    }

    func perform() async throws -> some IntentResult {
        CommuteWidgetStore.record(type: type, source: "widget")
        WidgetCenter.shared.reloadAllTimelines()
        return .result()
    }
}
