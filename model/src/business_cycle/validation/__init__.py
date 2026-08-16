"""공식 FRED 실자료 검증 도구."""

from .real_data import ValidationResult, run_real_data_validation

__all__ = ["ValidationResult", "run_real_data_validation"]
