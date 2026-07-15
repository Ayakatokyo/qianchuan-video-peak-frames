class RpaNotConfiguredError(RuntimeError):
    """Raised until the online Qianchuan connector is available."""


def fetch_material_diagnosis(**params):
    del params
    raise RpaNotConfiguredError(
        "RPA entry is not configured: "
        "rpa.conn.juliang.qc.material.analysis.video.live.diagnosis"
    )
