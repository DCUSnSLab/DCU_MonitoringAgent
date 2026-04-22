"""
로깅 유틸리티 모듈

애플리케이션 전체에서 사용할 통합 로깅 시스템을 제공합니다.
- 콘솔 출력 (컬러)
- 파일 출력 (로테이션)
"""

import os
import sys
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime


# 로그 포맷 상수
CONSOLE_FORMAT = "[%(asctime)s] %(levelname)-8s | %(name)-25s | %(message)s"
FILE_FORMAT = "[%(asctime)s] %(levelname)-8s | %(name)-25s | %(funcName)s:%(lineno)d | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logger(
    name: str = "agent",
    level: str = "INFO",
    log_file: str = "logs/agent.log",
    max_size_mb: int = 10,
    backup_count: int = 5,
    console_output: bool = True,
) -> logging.Logger:
    """
    로거를 설정하고 반환합니다.

    Args:
        name: 로거 이름
        level: 로그 레벨 (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_file: 로그 파일 경로
        max_size_mb: 로그 파일 최대 크기 (MB)
        backup_count: 백업 파일 수
        console_output: 콘솔 출력 여부

    Returns:
        설정된 Logger 인스턴스
    """
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # 기존 핸들러 제거 (중복 방지)
    logger.handlers.clear()

    # 콘솔 핸들러
    if console_output and sys.stdout is not None:
        # Windows cp949 터미널에서 한글 출력 시 UnicodeEncodeError 방지
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            pass  # Python 3.6 이하 무시

        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(getattr(logging, level.upper(), logging.INFO))

        try:
            import colorlog

            color_format = (
                "%(log_color)s[%(asctime)s] %(levelname)-8s%(reset)s | "
                "%(cyan)s%(name)-25s%(reset)s | %(message)s"
            )
            console_formatter = colorlog.ColoredFormatter(
                color_format,
                datefmt=DATE_FORMAT,
                log_colors={
                    "DEBUG": "white",
                    "INFO": "green",
                    "WARNING": "yellow",
                    "ERROR": "red",
                    "CRITICAL": "bold_red",
                },
            )
        except ImportError:
            console_formatter = logging.Formatter(CONSOLE_FORMAT, datefmt=DATE_FORMAT)

        console_handler.setFormatter(console_formatter)
        logger.addHandler(console_handler)

    # 파일 핸들러
    if log_file:
        log_dir = os.path.dirname(log_file)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)

        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=max_size_mb * 1024 * 1024,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.DEBUG)  # 파일에는 모든 레벨 기록
        file_formatter = logging.Formatter(FILE_FORMAT, datefmt=DATE_FORMAT)
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)

    return logger


def get_logger(name: str) -> logging.Logger:
    """
    기존 로거의 자식 로거를 반환합니다.

    Args:
        name: 모듈 이름 (예: 'monitors.process')

    Returns:
        자식 Logger 인스턴스
    """
    return logging.getLogger(f"agent.{name}")
