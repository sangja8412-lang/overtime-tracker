import AppIntents
import SwiftUI
import WidgetKit

// MARK: - 타임라인

struct CommuteEntry: TimelineEntry {
    let date: Date
    let state: CommuteWidgetState
}

struct CommuteProvider: TimelineProvider {
    func placeholder(in context: Context) -> CommuteEntry {
        CommuteEntry(date: Date(), state: .preview)
    }

    func getSnapshot(in context: Context, completion: @escaping (CommuteEntry) -> Void) {
        completion(CommuteEntry(date: Date(), state: CommuteWidgetStore.load() ?? .preview))
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<CommuteEntry>) -> Void) {
        let now = Date()
        let state = CommuteWidgetStore.today(now)
        // 앱이 기록을 바꾸면 WidgetCenter 로 즉시 새로 그리므로,
        // 여기서는 근무 시간 표시가 늘 최신이도록 15분마다 한 번씩만 다시 그린다.
        let next = Calendar.current.date(byAdding: .minute, value: 15, to: now) ?? now.addingTimeInterval(900)
        completion(Timeline(entries: [CommuteEntry(date: now, state: state)], policy: .after(next)))
    }
}

// MARK: - 위젯

struct OvertimeWidget: Widget {
    let kind = "OvertimeWidget"

    var body: some WidgetConfiguration {
        StaticConfiguration(kind: kind, provider: CommuteProvider()) { entry in
            OvertimeWidgetView(entry: entry)
        }
        .configurationDisplayName("퇴근했니?")
        .description("오늘 출근·퇴근 상태를 한눈에 보고, 위젯에서 바로 출근·퇴근을 찍어요.")
        .supportedFamilies([.systemSmall, .systemMedium, .accessoryCircular, .accessoryRectangular])
    }
}

// MARK: - 색·문구

private enum Theme {
    static let background = Color(red: 0.94, green: 0.90, blue: 0.84)   // 웹 배경(#f0e6d5)
    static let ink = Color(red: 0.24, green: 0.20, blue: 0.16)
    static let accent = Color(red: 0.82, green: 0.45, blue: 0.24)      // 카피바라 갈색 계열
    static let mint = Color(red: 0.20, green: 0.55, blue: 0.45)
}

private enum Copy {
    static func title(_ state: CommuteWidgetState) -> String {
        switch state.phase {
        case "working": return "근무 중"
        case "done": return "퇴근 완료"
        default: return "아직 출근 전"
        }
    }

    static func detail(_ state: CommuteWidgetState) -> String {
        switch state.phase {
        case "working":
            return "\(state.clockIn) 출근"
        case "done":
            return "\(state.clockIn) ~ \(state.clockOut)"
        default:
            return "정규 출근 \(state.regularStart)"
        }
    }

    static func sub(_ state: CommuteWidgetState) -> String {
        switch state.phase {
        case "working":
            let elapsed = duration(state.clockIn, "")
            return elapsed.isEmpty ? "" : "지금까지 \(elapsed)"
        case "done":
            return duration(state.clockIn, state.clockOut)
        default:
            return "위젯에서 바로 찍을 수 있어요"
        }
    }
}

// MARK: - 계산 도우미

private func minutes(_ text: String) -> Int? {
    let parts = text.split(separator: ":")
    guard parts.count == 2, let hour = Int(parts[0]), let minute = Int(parts[1]) else { return nil }
    return hour * 60 + minute
}

/// "HH:mm" ~ "HH:mm"(없으면 지금) 사이의 분. 자정을 넘기면 24시간을 더한다.
private func worked(_ start: String, _ end: String) -> Int? {
    guard let from = minutes(start) else { return nil }
    var to: Int
    if let value = minutes(end) {
        to = value
    } else {
        let parts = Calendar.current.dateComponents([.hour, .minute], from: Date())
        to = (parts.hour ?? 0) * 60 + (parts.minute ?? 0)
    }
    if to < from { to += 24 * 60 }
    return to - from
}

private func duration(_ start: String, _ end: String) -> String {
    guard let total = worked(start, end) else { return "" }
    let hours = total / 60
    let mins = total % 60
    return mins == 0 ? "\(hours)시간" : "\(hours)시간 \(mins)분"
}

// MARK: - 본문

struct OvertimeWidgetView: View {
    @Environment(\.widgetFamily) private var family
    let entry: CommuteEntry

    private var state: CommuteWidgetState { entry.state }
    private var isAccessory: Bool {
        family == .accessoryCircular || family == .accessoryRectangular
    }

    var body: some View {
        content
            .containerBackground(for: .widget) {
                if isAccessory {
                    AccessoryWidgetBackground()
                } else {
                    Theme.background
                }
            }
    }

    @ViewBuilder
    private var content: some View {
        switch family {
        case .accessoryCircular:
            circular
        case .accessoryRectangular:
            rectangular
        case .systemMedium:
            medium
        default:
            small
        }
    }

    // MARK: 홈화면 (소형)

    private var small: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 4) {
                Text("🦫").font(.system(size: 13))
                Text("퇴근했니?").font(.system(size: 11, weight: .semibold)).foregroundStyle(Theme.ink.opacity(0.6))
            }
            Text(Copy.title(state))
                .font(.system(size: 16, weight: .bold))
                .foregroundStyle(state.phase == "working" ? Theme.mint : Theme.ink)
            Text(Copy.detail(state))
                .font(.system(size: 12, weight: .medium))
                .foregroundStyle(Theme.ink)
                .monospacedDigit()
            Spacer(minLength: 0)
            actionButton
        }
        .padding(.vertical, 2)
    }

    // MARK: 홈화면 (중형)

    private var medium: some View {
        HStack(spacing: 10) {
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 4) {
                    Text("🦫").font(.system(size: 13))
                    Text("퇴근했니?").font(.system(size: 11, weight: .semibold)).foregroundStyle(Theme.ink.opacity(0.6))
                }
                Text(Copy.title(state))
                    .font(.system(size: 19, weight: .bold))
                    .foregroundStyle(state.phase == "working" ? Theme.mint : Theme.ink)
                HStack(spacing: 6) {
                    Text(Copy.detail(state))
                        .font(.system(size: 13, weight: .medium))
                        .monospacedDigit()
                    if !Copy.sub(state).isEmpty {
                        Text("·").foregroundStyle(Theme.ink.opacity(0.3))
                        Text(Copy.sub(state)).font(.system(size: 12))
                    }
                }
                .foregroundStyle(Theme.ink)
                Spacer(minLength: 0)
            }
            Spacer(minLength: 0)
            actionButton
        }
        .padding(.vertical, 2)
    }

    // MARK: 잠금화면 (원형)

    private var circular: some View {
        VStack(spacing: 1) {
            Text("🦫").font(.system(size: 10))
            if state.phase == "before" {
                Text("출근 전").font(.system(size: 10, weight: .semibold))
            } else {
                Text(state.phase == "done" ? "완료" : "근무")
                    .font(.system(size: 9, weight: .semibold))
                    .foregroundStyle(.secondary)
                Text(state.phase == "done" ? state.clockOut : state.clockIn)
                    .font(.system(size: 13, weight: .bold, design: .rounded))
                    .monospacedDigit()
                    .minimumScaleFactor(0.7)
            }
        }
    }

    // MARK: 잠금화면 (직사각형)

    private var rectangular: some View {
        HStack(spacing: 6) {
            Text("🦫").font(.system(size: 16))
            VStack(alignment: .leading, spacing: 1) {
                Text("\(Copy.title(state)) · \(Copy.detail(state))")
                    .font(.system(size: 13, weight: .semibold))
                    .lineLimit(1)
                Text(Copy.sub(state))
                    .font(.system(size: 11))
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
            Spacer(minLength: 0)
        }
    }

    // MARK: 버튼 (iOS 17+ 위젯 버튼)

    @ViewBuilder
    private var actionButton: some View {
        if state.phase == "done" {
            Text("오늘 퇴근 완료")
                .font(.system(size: 11, weight: .semibold))
                .foregroundStyle(Theme.ink.opacity(0.55))
        } else if state.phase == "working" {
            button(title: "퇴근 찍기", type: "out")
        } else {
            button(title: "출근 찍기", type: "in")
        }
    }

    private func button(title: String, type: String) -> some View {
        Button(intent: RecordCommuteIntent(type: type)) {
            HStack(spacing: 3) {
                Image(systemName: type == "in" ? "arrow.right.to.line" : "arrow.left.to.line")
                    .font(.system(size: 10, weight: .bold))
                Text(title).font(.system(size: 12, weight: .bold))
            }
            .foregroundStyle(Color.white)
            .padding(.horizontal, 9)
            .padding(.vertical, 5)
            .background(
                Capsule().fill(type == "in" ? Theme.mint : Theme.accent)
            )
        }
        .buttonStyle(.plain)
    }
}
