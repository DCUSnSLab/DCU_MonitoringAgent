"""
사용자 상태 분류기(State Analyzer)

수집된 프로세스 및 브라우저 데이터를 기반으로 
사용자의 패턴을 분석하여 안전(SAFE), 주의(WARNING), 경고(DANGER)로 상태를 분류합니다.
"""

from typing import List
from agent.models import StatusReport, UserState, Alert

class StateAnalyzer:
    """StatusReport를 분석하여 UserState를 도출하는 클래스"""

    @staticmethod
    def evaluate(report: StatusReport, current_alerts: List[Alert]) -> UserState:
        """
        주어진 보고서를 통해 사용자의 현재 상태를 평가합니다.

        분류 기준:
        1. SAFE: 현재 발생한 전체 알림(current_alerts)이 0개인 경우
        2. DANGER: 알림의 원인이 된 대상이 현재 화면에서 활성화된(Active) 상태인 경우
           - 차단된 프로세스가 현재 포그라운드(Foreground) 프로세스일 때
           - 차단된 URL이 크롬의 현재 포커스 탭(is_active=True)일 때
        3. WARNING: 알림 목록이 존재하지만, 모두 백그라운드에 숨어있는 경우
        """
        if not current_alerts:
            return UserState.SAFE

        # DANGER(경고) 조건 검사: 알림 대상이 현재 화면에 활성화돼 있으면 DANGER.
        # 알림 코드가 아니라 알림이 가리키는 대상(process_name / url)으로 판정하므로
        # BLOCKED_PROCESS/UNAUTHORIZED_PROCESS/MESSENGER_DETECTED(프로세스),
        # BLOCKED_URL/MESSENGER_DETECTED(URL)를 모두 동일하게 처리한다.
        for alert in current_alerts:
            # 프로세스 기반 알림: 해당 프로세스가 포그라운드인지 검사
            if alert.process_name:
                for proc in report.processes:
                    if proc.name == alert.process_name and proc.is_foreground:
                        return UserState.DANGER

            # URL 기반 알림: 해당 URL이 크롬의 활성화된 탭인지 검사
            if alert.url:
                chrome_info = report.browser_details.chrome
                if chrome_info and chrome_info.tabs:
                    for tab in chrome_info.tabs:
                        if tab.url == alert.url and tab.is_active:
                            return UserState.DANGER

        # 알림이 있긴 하지만 아무것도 포그라운드에 노출되지 않았으므로 주의
        return UserState.WARNING
