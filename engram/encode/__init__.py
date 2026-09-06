# 사후 관점 인코더 패키지
"""Lane C, Processing II.

Deliberately empty of re-exports. The DAG reaches the encoder as
`dag._opt("engram.encode.encode", "encode")`, and re-exporting the name `encode`
here would shadow the submodule of the same name: `import engram.encode.encode`
would then bind the *function*, not the module, and `mod.LAST_STATS` would fail.

Import from the module:

    from engram.encode.encode import encode, EncodeStats
"""
