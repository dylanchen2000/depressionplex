"""队列项状态与状态机（纯逻辑，不依赖 PySide6）。"""

from enum import Enum
from typing import NamedTuple


class ItemStatus(Enum):
    """队列项的六个状态，一个都不许合并。"""

    PENDING = "待跑"
    RUNNING = "运行中"
    COMPLETED = "完成"
    NO_OUTPUT = "无产出"
    FAILED = "失败"
    CANCELLED = "已取消"


def status_for_exit(exit_code: int) -> ItemStatus:
    """根据退出码映射状态（引擎的约定，见 cli/analyze.py 顶部 docstring）。

    - 0: 完成（至少一个隔间出数字）
    - 2: 无产出（跑完了，但一个隔间都没产出数字）
    - 1 或其它: 失败（解码失败 / 被信号杀等）
    """
    if exit_code == 0:
        return ItemStatus.COMPLETED
    elif exit_code == 2:
        return ItemStatus.NO_OUTPUT
    else:
        return ItemStatus.FAILED


class StateTransitionError(Exception):
    """非法状态迁移。"""

    pass


def validate_transition(from_status: ItemStatus, to_status: ItemStatus) -> None:
    """验证状态迁移是否合法。非法迁移抛 StateTransitionError。

    禁止的迁移：已取消 → 完成（取消后不许自动迁到完成）。
    """
    if from_status == ItemStatus.CANCELLED and to_status == ItemStatus.COMPLETED:
        raise StateTransitionError(
            f"禁止的状态迁移：{from_status.value} → {to_status.value}。"
            "已取消的项目不许迁移到完成状态。"
        )


class QueueItem(NamedTuple):
    """队列中的一个项目。"""

    video_index: int  # 在 experiment.json 的 videos 列表中的索引
    video_name: str  # 视频名（用于显示）
    status: ItemStatus
    progress_frame: int = 0  # 当前处理的帧数
    progress_total: int | None = None  # 总帧数，未知时为 None
    error_message: str = ""  # 失败时的错误信息
