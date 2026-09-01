"""경기국면 해석층.

모델이 고른 공식 국면을 설명한다. 국면을 다시 계산하지 않고, 투자 판단을 만들지
않는다. 모든 규칙은 표시 전용이며 모델 확률로 되먹임하지 않는다.
"""

from __future__ import annotations

from .contract import SchemaViolation, validate_output
from .diagnosis import diagnose, render_markdown

__all__ = ["SchemaViolation", "diagnose", "render_markdown", "validate_output"]
