# 사후 관점 인코더 패키지의 공개 진입점
"""Lane C, Processing II. The DAG imports `engram.encode.encode:encode`."""
from engram.encode.encode import EncodeStats, encode

__all__ = ["encode", "EncodeStats"]
